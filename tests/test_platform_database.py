"""Repository and migration tests against a real PostgreSQL (``TEST_DATABASE_URL``); skipped otherwise."""

from __future__ import annotations

import hashlib
import os
import secrets
import unittest
import uuid
from datetime import UTC, date, datetime, timedelta

from backend.agent.repository import AgentRateLimit, AgentRequestConflict, AgentTokenRepository
from backend.ai.repository import AICopilotRepository
from backend.backtest.result_writer import DatabaseResultWriter
from backend.connections.crypto import EnvelopeCipher, MasterKeyring
from backend.connections.providers import ConnectionPermissionReport
from backend.connections.repository import ExchangeConnectionRepository
from backend.data.database import Database
from backend.data.repositories import (
    BacktestRunRepository,
    BacktestTradeRepository,
    IndicatorSourceRepository,
    LiveSignalRepository,
    ResearchExperimentRepository,
    SavedUniverseRepository,
    StrategyConfigRepository,
    StrategyDeploymentRepository,
    StrategySourceRepository,
    WalkForwardValidationRepository,
)
from backend.monitoring.repository import MonitoringRepository
from psycopg.errors import CheckViolation, RaiseException

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL is not set")
class PlatformDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database = Database(TEST_DATABASE_URL, max_pool_size=2)
        cls.database.open()
        cls.database.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        cls.first_migration = cls.database.migrate()
        cls.runs = BacktestRunRepository(cls.database)
        cls.trades = BacktestTradeRepository(cls.database)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.close()

    def _run(self, **overrides):
        values = {
            "market": "NSE",
            "strategy_id": "ema_vwap_strong_buy",
            "strategy_version": "1.0.0",
            "configuration_snapshot": {"target_pct": 1.0},
            "execution_settings": {"batchSize": 500},
            "timeframe": "5m",
            "symbols": ["RELIANCE", "TCS"],
            "start_date": date(2026, 8, 1),
            "end_date": date(2026, 8, 31),
        }
        values.update(overrides)
        return self.runs.create(**values)

    def test_migrations_are_versioned_and_idempotent(self) -> None:
        self.assertEqual(
            self.first_migration,
            [
                "001_platform",
                "001_timescale_market_data",
                "002_timescale_candle_reader",
                "003_backtest_trade_last_price",
                "004_paper_pending_entries",
                "005_dhan_fifo_cost_basis",
                "006_live_signal_strategy_identity",
                "007_strategy_deployments",
                "008_strategy_deployment_universe",
                "009_watchlist_profile_versions",
                "010_tradingview_signal_ingestion",
                "011_strategy_governance",
                "012_strategy_sources",
                "013_strategy_v2_backtests",
                "014_strategy_v2_live",
                "015_indicator_sources",
                "016_research_experiments",
                "017_parameter_experiments",
                "018_walk_forward_validations",
                "019_ai_research_copilot",
                "020_secure_exchange_connections",
                "022_production_monitoring",
                "023_agent_mcp_access",
            ],
        )
        self.assertEqual(self.database.migrate(), [])
        tables = {
            row["table_name"]
            for row in self.database.fetch_all(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
        for expected in (
            "screener_runs",
            "screener_results",
            "saved_universes",
            "watchlist_profiles",
            "watchlist_profile_versions",
            "strategy_configs",
            "strategy_deployments",
            "strategy_approvals",
            "strategy_sources",
            "indicator_sources",
            "research_experiments",
            "research_variants",
            "walk_forward_validations",
            "walk_forward_folds",
            "walk_forward_training_runs",
            "ai_copilot_requests",
            "ai_research_drafts",
            "exchange_connections",
            "exchange_connection_events",
            "worker_leases",
            "operational_audit_events",
            "operational_alerts",
            "operational_alert_occurrences",
            "notification_deliveries",
            "agent_access_tokens",
            "agent_rate_limit_windows",
            "agent_tool_requests",
            "tradingview_webhook_events",
            "backtest_runs",
            "backtest_trades",
            "live_signals",
            "paper_accounts",
            "paper_orders",
            "paper_pending_entries",
            "paper_lots",
            "paper_trades",
            "engine_status",
            "schema_migrations",
        ):
            self.assertIn(expected, tables)
        research_columns = {
            row["column_name"]
            for row in self.database.fetch_all(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'research_experiments'
                """
            )
        }
        self.assertTrue(
            {
                "generation_mode",
                "sweep_definitions",
                "preview_hash",
                "variant_count",
                "symbol_count",
                "estimated_symbol_runs",
                "idempotency_key",
                "universe_id",
                "universe_name",
            }
            <= research_columns
        )
        constraints = {
            row["constraint_name"]
            for row in self.database.fetch_all(
                """
                SELECT constraint_name FROM information_schema.table_constraints
                WHERE table_schema = 'public' AND table_name = 'research_experiments'
                """
            )
        }
        self.assertIn("research_experiments_workload", constraints)
        self.assertIn("research_experiments_date_range", constraints)
        walk_forward_constraints = {
            row["constraint_name"]
            for row in self.database.fetch_all(
                """
                SELECT constraint_name FROM information_schema.table_constraints
                WHERE table_schema = 'public' AND table_name = 'walk_forward_validations'
                """
            )
        }
        self.assertIn("walk_forward_validations_date_range", walk_forward_constraints)
        self.assertIn("walk_forward_validations_workload", walk_forward_constraints)
        connection_columns = {
            row["column_name"]
            for row in self.database.fetch_all(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'exchange_connections'
                """
            )
        }
        self.assertNotIn("api_key", connection_columns)
        self.assertNotIn("api_secret", connection_columns)
        self.assertTrue(
            {
                "credentials_ciphertext",
                "credentials_nonce",
                "encrypted_data_key",
                "data_key_nonce",
                "master_key_version",
            }
            <= connection_columns
        )
    def test_monitoring_leases_are_compare_and_set_and_recover_after_expiry(self) -> None:
        repository = MonitoringRepository(self.database)
        first_time = datetime(2026, 9, 7, 10, 0, tzinfo=UTC)
        owner_one, owner_two = str(uuid.uuid4()), str(uuid.uuid4())
        first = repository.acquire_lease(
            worker_type="BACKTEST", task_key="run-monitoring-test", worker_identity="worker-one",
            host_identity="host-one", process_identity="1", owner_token=owner_one,
            ttl_seconds=30, now=first_time,
        )
        self.assertIsNotNone(first)
        self.assertIsNone(repository.acquire_lease(
            worker_type="BACKTEST", task_key="run-monitoring-test", worker_identity="worker-two",
            host_identity="host-two", process_identity="2", owner_token=owner_two,
            ttl_seconds=30, now=datetime(2026, 9, 7, 10, 0, 10, tzinfo=UTC),
        ))
        recovered = repository.acquire_lease(
            worker_type="BACKTEST", task_key="run-monitoring-test", worker_identity="worker-two",
            host_identity="host-two", process_identity="2", owner_token=owner_two,
            ttl_seconds=30, now=datetime(2026, 9, 7, 10, 0, 31, tzinfo=UTC),
        )
        self.assertEqual(recovered["workerIdentity"], "worker-two")
        self.assertNotEqual(recovered["ownerFingerprint"], first["ownerFingerprint"])

    def test_operational_audit_is_append_only_and_alerts_deduplicate_with_cooldown(self) -> None:
        repository = MonitoringRepository(self.database)
        audit = repository.append_audit(
            request_id=str(uuid.uuid4()), action="BACKTEST_CREATED", actor_type="USER",
            actor_id="database-test", success=True, details={"apiSecret": "omitted", "market": "NSE"},
        )
        self.assertNotIn("apiSecret", audit["details"])
        with self.assertRaises(RaiseException):
            self.database.execute(
                "UPDATE operational_audit_events SET success = false WHERE audit_id = %s",
                (uuid.UUID(audit["auditId"]),),
            )
        first_time = datetime(2026, 9, 7, 11, 0, tzinfo=UTC)
        first = repository.raise_alert(
            alert_type="STALE_NSE_DATA", severity="WARNING", source="market-data",
            title="NSE stale", message="No settled candle", context={"market": "NSE"},
            cooldown_seconds=300, now=first_time,
        )
        duplicate = repository.raise_alert(
            alert_type="STALE_NSE_DATA", severity="WARNING", source="market-data",
            title="NSE stale", message="Still stale", context={"market": "NSE"},
            cooldown_seconds=300, now=datetime(2026, 9, 7, 11, 1, tzinfo=UTC),
        )
        self.assertEqual(duplicate["alertId"], first["alertId"])
        self.assertEqual(duplicate["occurrenceCount"], 2)
        self.assertTrue(first["notificationDue"])
        self.assertFalse(duplicate["notificationDue"])
        repository.resolve_alert(first["alertId"], actor="operator", resolution="Feed recovered")
        next_incident = repository.raise_alert(
            alert_type="STALE_NSE_DATA", severity="WARNING", source="market-data",
            title="NSE stale", message="New incident", context={"market": "NSE"},
            cooldown_seconds=300, now=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        )
        self.assertNotEqual(next_incident["alertId"], first["alertId"])

    def test_agent_tokens_are_hashed_scoped_rate_limited_revocable_and_idempotent(self) -> None:
        now = datetime(2026, 9, 7, 13, 0, tzinfo=UTC)
        repository = AgentTokenRepository(self.database, clock=lambda: now)
        record, raw_token = repository.create(
            name="Database agent",
            scopes=["research:read", "backtests:submit"],
            expires_at=now + timedelta(days=1),
            created_by="database-test",
            rate_limit_per_minute=1,
        )
        stored = self.database.fetch_one(
            "SELECT token_hash, token_prefix FROM agent_access_tokens WHERE token_id = %s",
            (uuid.UUID(record["tokenId"]),),
        )
        self.assertIsNotNone(stored)
        self.assertNotEqual(stored["token_hash"], raw_token)
        self.assertEqual(stored["token_hash"], hashlib.sha256(raw_token.encode()).hexdigest())
        self.assertEqual(repository.authenticate(raw_token)["lastUsedAt"], now.isoformat())
        with self.assertRaises(AgentRateLimit):
            repository.authenticate(raw_token)

        request, created = repository.begin_tool_request(
            token_id=record["tokenId"], tool_name="opendelta_submit_backtest",
            idempotency_key="database-agent-1", request_hash="a" * 64,
        )
        self.assertTrue(created)
        repository.complete_tool_request(request["requestId"], {"runId": str(uuid.uuid4())})
        duplicate, created = repository.begin_tool_request(
            token_id=record["tokenId"], tool_name="opendelta_submit_backtest",
            idempotency_key="database-agent-1", request_hash="a" * 64,
        )
        self.assertFalse(created)
        self.assertEqual(duplicate["status"], "COMPLETE")
        with self.assertRaises(AgentRequestConflict):
            repository.begin_tool_request(
                token_id=record["tokenId"], tool_name="opendelta_submit_backtest",
                idempotency_key="database-agent-1", request_hash="b" * 64,
            )
        self.assertIsNotNone(repository.revoke(record["tokenId"])["revokedAt"])

    def test_strategy_sources_are_immutable_and_filter_by_market(self) -> None:
        repository = StrategySourceRepository(self.database)
        manifest = {
            "strategyId": "quality_breakout_v2",
            "name": "Quality Breakout",
            "version": "1.0.0",
            "description": "Test source",
            "supportedMarkets": ["CRYPTO"],
            "supportedTimeframes": ["5m"],
            "parameters": {},
        }
        saved = repository.create(
            source_code="source", code_hash="a" * 64, manifest=manifest, validation={"valid": True}
        )
        self.assertEqual(repository.get(saved["sourceId"])["sourceCode"], "source")
        self.assertEqual(len(repository.list("CRYPTO")), 1)
        self.assertEqual(repository.list("NSE"), [])

    def test_indicator_sources_are_immutable_and_archivable(self) -> None:
        repository = IndicatorSourceRepository(self.database)
        manifest = {
            "indicatorId": "relative_volume",
            "name": "Relative Volume",
            "version": "1.0.0",
            "description": "Test indicator",
            "parameters": {"length": 20},
            "requiredHistory": 20,
            "outputs": [{"name": "rvol", "label": "RVOL", "display": "LINE", "pane": "PANEL"}],
        }
        saved = repository.create(
            source_code="source", code_hash="b" * 64, manifest=manifest, validation={"valid": True}
        )
        self.assertEqual(repository.get(saved["sourceId"])["sourceCode"], "source")
        self.assertEqual(repository.list(status="VALIDATED")[0]["indicatorId"], "relative_volume")
        archived = repository.archive(saved["sourceId"])
        self.assertEqual(archived["status"], "ARCHIVED")
        self.assertEqual(repository.list(status="VALIDATED"), [])

    def test_different_strategy_ids_do_not_collide_at_same_candle(self) -> None:
        signals = LiveSignalRepository(self.database)
        stamp = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
        common = {
            "market": "NSE",
            "strategy_version": "1.0.0",
            "symbol": "TCS",
            "timeframe": "5m",
            "candle_timestamp": stamp,
            "signal_type": "BUY",
            "signal_price": 100.0,
            "target_price": 101.0,
            "stop_price": None,
            "expires_at": None,
            "reasons": ["TEST"],
            "indicators": {},
            "configuration_snapshot": {"target_pct": 1.0},
        }
        first = signals.insert_new(strategy_id="ema_vwap_strong_buy", **common)
        second = signals.insert_new(strategy_id="rsi_dip_ladder_v1", **common)
        duplicate = signals.insert_new(strategy_id="ema_vwap_strong_buy", **common)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNone(duplicate)

    def test_strategy_deployment_pins_the_active_configuration(self) -> None:
        active = StrategyConfigRepository(self.database).save(
            market="CRYPTO",
            strategy_id="ema_vwap_strong_buy",
            strategy_version="1.0.0",
            name="deployment-test",
            configuration={"target_pct": 1.0},
            risk_settings={"priceModel": "NEXT_OPEN"},
            activate=True,
        )
        universe = SavedUniverseRepository(self.database).save(
            market="CRYPTO", name="majors", symbols=["BTC-USDT"], manual_includes=["ETH-USDT"], activate=True
        )
        deployments = StrategyDeploymentRepository(self.database)
        paper = deployments.save(
            market="CRYPTO",
            strategy_id="ema_vwap_strong_buy",
            strategy_version="1.0.0",
            config_id=active["configId"],
            universe_id=universe["universeId"],
            timeframe="5m",
            mode="PAPER",
            signal_source="TRADINGVIEW",
        )
        self.assertEqual((paper["mode"], paper["configId"]), ("PAPER", active["configId"]))
        self.assertEqual(paper["signalSource"], "TRADINGVIEW")
        self.assertEqual(paper["universeId"], universe["universeId"])
        self.assertEqual(
            SavedUniverseRepository(self.database).symbols(universe["universeId"], market="CRYPTO"),
            ["BTC-USDT", "ETH-USDT"],
        )
        stopped = deployments.save(
            market="CRYPTO",
            strategy_id="ema_vwap_strong_buy",
            strategy_version="1.0.0",
            config_id=active["configId"],
            universe_id=universe["universeId"],
            timeframe="5m",
            mode="OFF",
            signal_source="TRADINGVIEW",
        )
        self.assertEqual(stopped["deploymentId"], paper["deploymentId"])
        self.assertEqual(deployments.get("CRYPTO", "ema_vwap_strong_buy")["mode"], "OFF")

    def test_run_lifecycle_and_progress(self) -> None:
        record = self._run()
        self.assertEqual(record["status"], "QUEUED")
        self.assertEqual(record["configurationSnapshot"], {"target_pct": 1.0})
        self.assertEqual(record["symbolsTotal"], 2)
        self.runs.mark_started(record["runId"])
        self.runs.update_progress(
            record["runId"],
            symbols_completed=1,
            current_symbol="TCS",
            failed_symbols=[{"symbol": "RELIANCE", "message": "no data"}],
        )
        progressed = self.runs.get(record["runId"])
        self.assertEqual(
            (progressed["status"], progressed["symbolsCompleted"], progressed["currentSymbol"]), ("RUNNING", 1, "TCS")
        )
        self.assertEqual(progressed["failedSymbols"][0]["symbol"], "RELIANCE")
        finished = self.runs.finish(record["runId"], status="COMPLETE", metrics={"completedTrades": 3})
        self.assertEqual(finished["status"], "COMPLETE")
        self.assertEqual(finished["metrics"], {"completedTrades": 3})
        self.assertIsNone(finished["currentSymbol"])
        self.assertIn(record["runId"], [item["runId"] for item in self.runs.list("NSE")])
        with self.assertRaises(ValueError):
            self.runs.finish(record["runId"], status="RUNNING", metrics=None)

    def test_research_experiment_is_atomic_idempotent_and_aggregates_status(self) -> None:
        repository = ResearchExperimentRepository(self.database)
        common = {
            "name": "Phase 7 database contract",
            "generation_mode": "GRID",
            "sweep_definitions": [
                {
                    "section": "strategy",
                    "parameter": "rsi_low",
                    "type": "number",
                    "method": "EXPLICIT_VALUES",
                    "values": [25, 30],
                }
            ],
            "preview_hash": "sha256:" + ("a" * 64),
            "idempotency_key": "database:phase7:idempotency",
            "market": "NSE",
            "strategy_id": "rsi_dip_ladder_v1",
            "strategy_version": "1.0.0",
            "strategy_source_id": None,
            "timeframe": "5m",
            "symbols": ["INFY", "TCS"],
            "start_date": date(2026, 8, 1),
            "end_date": date(2026, 8, 31),
            "universe_id": None,
            "universe_name": "Explicit symbol snapshot",
            "variants": [
                {
                    "name": "rsi_low=25",
                    "configuration": {"rsi_low": 25},
                    "execution": {"targetPct": 0.5},
                },
                {
                    "name": "rsi_low=30",
                    "configuration": {"rsi_low": 30},
                    "execution": {"targetPct": 0.75},
                },
            ],
        }
        created, was_created = repository.create_generated(**common)
        repeated, repeated_created = repository.create_generated(**common)
        self.assertTrue(was_created)
        self.assertFalse(repeated_created)
        self.assertEqual(created["experimentId"], repeated["experimentId"])
        self.assertEqual(created["estimatedSymbolRuns"], 4)
        self.assertEqual(
            [item["position"] for item in created["variants"]],
            [1, 2],
        )
        self.assertEqual(
            created["variants"][0]["configuration"],
            {"rsi_low": 25},
        )

        first_run, second_run = [item["run"]["runId"] for item in created["variants"]]
        self.runs.finish(first_run, status="COMPLETE", metrics={"realizedPnl": 25})
        partial = repository.get(created["experimentId"])
        self.assertEqual(partial["status"], "QUEUED")
        self.assertEqual(partial["variantStatusCounts"]["COMPLETE"], 1)
        self.runs.finish(second_run, status="COMPLETE", metrics={"realizedPnl": 30})
        complete = repository.get(created["experimentId"])
        self.assertEqual(complete["status"], "COMPLETE")
        self.assertEqual(complete["variantStatusCounts"]["COMPLETE"], 2)

        before = self.database.fetch_one("SELECT count(*) AS count FROM backtest_runs")["count"]
        invalid = {
            **common,
            "idempotency_key": "database:phase7:rollback",
            "preview_hash": "sha256:" + ("b" * 64),
            "variants": [
                common["variants"][0],
                {**common["variants"][1], "name": "x" * 81},
            ],
        }
        with self.assertRaises(CheckViolation):
            repository.create_generated(**invalid)
        after = self.database.fetch_one("SELECT count(*) AS count FROM backtest_runs")["count"]
        self.assertEqual(before, after)
        self.assertIsNone(repository.get_by_idempotency_key("database:phase7:rollback"))

    def test_walk_forward_graph_is_atomic_idempotent_and_pins_every_run(self) -> None:
        experiments = ResearchExperimentRepository(self.database)
        experiment, _ = experiments.create_generated(
            name="Phase 8 candidates",
            generation_mode="MANUAL",
            sweep_definitions=[],
            preview_hash="sha256:" + "c" * 64,
            idempotency_key="database:phase8:candidates",
            market="CRYPTO",
            strategy_id="crypto_pullback_v1",
            strategy_version="1.0.0",
            strategy_source_id=None,
            timeframe="5m",
            symbols=["BTC-USDT"],
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            universe_id=None,
            universe_name="Crypto snapshot",
            variants=[
                {"name": "candidate-a", "configuration": {}, "execution": {}},
                {"name": "candidate-b", "configuration": {}, "execution": {}},
            ],
        )
        repository = WalkForwardValidationRepository(self.database)
        common = {
            "name": "Phase 8 database contract",
            "mode": "ROLLING",
            "market": "CRYPTO",
            "strategy_id": "crypto_pullback_v1",
            "strategy_version": "1.0.0",
            "strategy_source_id": None,
            "timeframe": "5m",
            "symbols": ["BTC-USDT"],
            "universe_id": None,
            "universe_name": "Crypto snapshot",
            "overall_start_date": date(2026, 1, 1),
            "overall_end_date": date(2026, 1, 31),
            "training_window": 5,
            "testing_window": 2,
            "step_length": 2,
            "maximum_folds": 2,
            "candidate_experiment_id": experiment["experimentId"],
            "ranking_objective": "NET_PNL",
            "minimum_required_trades": 1,
            "transaction_cost_bps": 8,
            "slippage_bps": 2,
            "preview_hash": "sha256:" + "d" * 64,
            "idempotency_key": "database:phase8:validation",
            "workload": {
                "foldCount": 2,
                "candidateCount": 2,
                "childRunCount": 6,
                "symbolCount": 1,
                "estimatedSymbolRuns": 6,
                "estimatedCandleWorkload": 3168,
            },
            "folds": [
                {
                    "position": 1,
                    "trainingStart": "2026-01-01",
                    "trainingEnd": "2026-01-05",
                    "testingStart": "2026-01-06",
                    "testingEnd": "2026-01-07",
                    "trainingSessions": 5,
                    "testingSessions": 2,
                },
                {
                    "position": 2,
                    "trainingStart": "2026-01-03",
                    "trainingEnd": "2026-01-07",
                    "testingStart": "2026-01-08",
                    "testingEnd": "2026-01-09",
                    "trainingSessions": 5,
                    "testingSessions": 2,
                },
            ],
            "candidates": experiment["variants"],
        }
        created, was_created = repository.create_generated(**common)
        repeated, repeated_created = repository.create_generated(**common)
        self.assertTrue(was_created)
        self.assertFalse(repeated_created)
        self.assertEqual(created["validationId"], repeated["validationId"])
        self.assertEqual(len(created["folds"]), 2)
        self.assertEqual(len(created["folds"][0]["trainingCandidates"]), 2)
        training_run_ids = {
            candidate["run"]["runId"] for fold in created["folds"] for candidate in fold["trainingCandidates"]
        }
        self.assertEqual(len(training_run_ids), 4)
        winner = created["folds"][0]["trainingCandidates"][0]
        test_run = repository.create_test_run(fold_id=created["folds"][0]["foldId"], candidate=winner)
        same_test_run = repository.create_test_run(fold_id=created["folds"][0]["foldId"], candidate=winner)
        self.assertEqual(test_run["runId"], same_test_run["runId"])
        cancelled = repository.request_cancel(created["validationId"])
        self.assertTrue(cancelled["cancelRequested"])

    def test_ai_copilot_audit_omits_responses_and_requires_explicit_exact_draft(self) -> None:
        repository = AICopilotRepository(self.database)
        request_id = uuid.uuid4()
        content = "AI-generated source draft"
        repository.start_request(
            request_id=request_id,
            actor=f"database-test-{request_id}",
            action="DRAFT_STRATEGY",
            categories=["STRATEGY_SOURCE"],
            provider="test-provider",
            model="test-model",
            input_bytes=120,
            rate_limit=10,
        )
        repository.finish_request(
            request_id,
            status="SUCCEEDED",
            duration_ms=5,
            output_bytes=len(content.encode()),
            output_sha256=hashlib.sha256(content.encode()).hexdigest(),
            usage={"total_tokens": 4},
        )
        request = repository.get_request(request_id)
        self.assertEqual(request["status"], "SUCCEEDED")
        self.assertNotIn("content", request)
        with self.assertRaises(ValueError):
            repository.create_draft(request_id=request_id, draft_type="STRATEGY", content="modified")
        draft = repository.create_draft(request_id=request_id, draft_type="STRATEGY", content=content)
        repeated = repository.create_draft(request_id=request_id, draft_type="STRATEGY", content=content)
        self.assertEqual(draft["status"], "DRAFT")
        self.assertEqual(repeated["draftId"], draft["draftId"])
        self.assertEqual(repository.get_request(request_id)["draftIds"], [draft["draftId"]])

    def test_exchange_credentials_are_encrypted_rotatable_and_never_returned(self) -> None:
        repository = ExchangeConnectionRepository(self.database)
        connection_id = uuid.uuid4()
        private = {
            "apiKey": secrets.token_hex(32),
            "apiSecret": secrets.token_hex(32),
            "passphrase": secrets.token_urlsafe(20),
            "environment": "LIVE",
        }
        cipher = EnvelopeCipher(MasterKeyring("database-test", {"database-test": secrets.token_bytes(32)}))
        encrypted = cipher.encrypt(str(connection_id), private)
        public = repository.create(
            connection_id=connection_id,
            provider="OKX",
            label="Database test",
            environment="LIVE",
            masked_key_identifier=f"••••{private['apiKey'][-4:]}",
            encrypted=encrypted,
            actor="database-test",
        )
        self.assertNotIn(private["apiKey"], str(public))
        self.assertNotIn(private["apiSecret"], str(public))
        row, stored = repository.encrypted(connection_id)
        self.assertNotIn(private["apiSecret"].encode(), bytes(row["credentials_ciphertext"]))
        self.assertEqual(cipher.decrypt(str(connection_id), stored), private)
        report = ConnectionPermissionReport(
            True, True, True, False, True, "LIVE", "AVAILABLE", "Connection test succeeded"
        )
        tested = repository.record_test(connection_id, report=report, actor="database-test")
        self.assertEqual(tested["status"], "CONNECTED")
        rotated = cipher.rotate(str(connection_id), stored)
        repository.update_encryption(connection_id, encrypted=rotated, actor="database-test")
        self.assertEqual(cipher.decrypt(str(connection_id), repository.encrypted(connection_id)[1]), private)
        repository.delete(connection_id, actor="database-test")
        events = self.database.fetch_all(
            "SELECT action FROM exchange_connection_events WHERE connection_id = %s ORDER BY event_id",
            (connection_id,),
        )
        self.assertEqual([event["action"] for event in events], ["ADDED", "TEST_SUCCEEDED", "ROTATED", "DELETED"])

    def test_cancel_request_is_durable_and_stale_runs_are_interrupted_on_recovery(self) -> None:
        record = self._run()
        self.assertFalse(self.runs.cancel_requested(record["runId"]))
        self.runs.request_cancel(record["runId"])
        self.assertTrue(self.runs.cancel_requested(record["runId"]))
        self.runs.mark_started(record["runId"])
        stale = self._run()
        self.runs.mark_started(stale["runId"])
        interrupted = self.runs.interrupt_stale()
        self.assertGreaterEqual(interrupted, 2)
        self.assertEqual(self.runs.get(stale["runId"])["status"], "INTERRUPTED")
        self.assertEqual(self.runs.interrupt_stale(), 0)

    def test_trades_are_written_in_batches_with_duplicate_protection(self) -> None:
        record = self._run()
        writer = DatabaseResultWriter(self.runs, self.trades)
        writer.started(record["runId"])
        stamp = datetime(2026, 8, 4, 9, 30, tzinfo=UTC)
        row = {
            "run_id": record["runId"],
            "market": "NSE",
            "strategy_id": "ema_vwap_strong_buy",
            "strategy_version": "1.0.0",
            "symbol": "TCS",
            "timeframe": "5m",
            "lot_id": "TCS-Cycle1-Lot1",
            "cycle_id": "TCS-Cycle1",
            "lot_number": 1,
            "signal_timestamp": stamp,
            "signal_price": 100.0,
            "entry_timestamp": stamp,
            "entry_price": 100.05,
            "cost_basis_price": 100.05,
            "quantity": 100,
            "target_price": 101.05,
            "stop_price": None,
            "expires_at": None,
            "exit_timestamp": stamp,
            "exit_price": 101.0,
            "status": "TARGET_HIT",
            "gross_pnl": 95.0,
            "fees": 45.0,
            "slippage": 10.0,
            "net_pnl": 50.0,
            "unrealized_pnl": 0.0,
            "mae_pct": -0.2,
            "mfe_pct": 1.1,
            "holding_bars": 12,
            "holding_minutes": 60.0,
        }
        writer.write_trades(
            record["runId"],
            [
                row,
                {
                    **row,
                    "lot_id": "TCS-Cycle1-Lot2",
                    "lot_number": 2,
                    "status": "OPEN",
                    "exit_timestamp": None,
                    "exit_price": None,
                },
            ],
        )
        writer.write_trades(record["runId"], [row])  # duplicate lot is ignored, not duplicated
        self.assertEqual(self.trades.count(record["runId"]), 2)
        listed = self.trades.list(record["runId"], symbol="TCS")
        self.assertEqual([item["lotId"] for item in listed], ["TCS-Cycle1-Lot1", "TCS-Cycle1-Lot2"])
        self.assertEqual(listed[0]["netPnl"], 50.0)
        self.assertEqual(listed[1]["status"], "OPEN")
        open_trades = self.trades.list(record["runId"], symbol="CS", status="OPEN", sort_by="netPnl", direction="desc")
        self.assertEqual([item["lotId"] for item in open_trades], ["TCS-Cycle1-Lot2"])
        self.assertEqual(self.trades.count(record["runId"], symbol="CS", status="OPEN"), 1)
        with self.assertRaises(ValueError):
            self.trades.list(record["runId"], sort_by="not_a_column")
        writer.finished(record["runId"], status="COMPLETE", metrics={"completedTrades": 1})
        self.assertEqual(self.runs.get(record["runId"])["status"], "COMPLETE")
