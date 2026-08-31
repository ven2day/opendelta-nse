from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from main import IST
from nse_signal_engine_v2 import (
    DETECTORS,
    ENGINE_VERSION,
    HistoricalEvidence,
    NseSignalEngineV2Config,
    build_market_context,
    build_trade_plan,
)


FUNNEL_VERSION = "nse-signal-funnel-2.0.0"


def _as_ist(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize(IST) if stamp.tzinfo is None else stamp.tz_convert(IST)


def _evaluation_id(symbol: str, strategy: str, timestamp: pd.Timestamp) -> str:
    identity = f"{FUNNEL_VERSION}|{strategy}|{symbol.upper()}|{timestamp.isoformat()}"
    return "NSEEV-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24].upper()


def build_nse_signal_funnel(
    feature_frames: Mapping[str, pd.DataFrame],
    ranked_rows: Sequence[Mapping[str, Any]],
    *,
    as_of: object,
    nifty_frame: pd.DataFrame | None = None,
    config: NseSignalEngineV2Config | None = None,
    evidence_by_strategy: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    settings = (config or NseSignalEngineV2Config()).validate()
    timestamp = _as_ist(as_of)
    eligible_rows = [dict(row) for row in ranked_rows if bool(row.get("eligible"))]
    eligible_by_symbol = {str(row["symbol"]): row for row in eligible_rows}
    context = build_market_context(feature_frames, nifty_frame, timestamp)
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    rejection_counts: dict[str, int] = {}
    for symbol, activity in eligible_by_symbol.items():
        frame = feature_frames.get(symbol)
        if frame is None or frame.empty:
            reasons = ["CACHED_CANDLE_DATA_UNAVAILABLE"]
            rejection_counts[reasons[0]] = rejection_counts.get(reasons[0], 0) + len(DETECTORS)
            continue
        for detector in DETECTORS:
            evaluation = detector.evaluate(frame, activity, context, timestamp, settings)
            reasons = list(evaluation.get("reasons") or [])
            if not evaluation.get("ready") or reasons:
                for reason in reasons:
                    rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
                rejected.append({
                    "eventId": _evaluation_id(symbol, detector.key, timestamp),
                    "symbol": symbol,
                    "strategyKey": detector.key,
                    "strategyName": detector.name,
                    "signalTimestamp": timestamp.isoformat(),
                    "status": "REJECTED",
                    "reasons": reasons or ["SETUP_NOT_READY"],
                    "activityScore": activity.get("score"),
                    "rules": list(evaluation.get("rules") or []),
                    "paperOnly": True,
                    "liveOrdersEnabled": False,
                })
                continue
            plan = build_trade_plan(detector, evaluation, frame, timestamp, settings)
            if not plan["planReady"]:
                for reason in plan["planRejectionReasons"]:
                    rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
                rejected.append({
                    "eventId": _evaluation_id(symbol, detector.key, timestamp),
                    "symbol": symbol,
                    "strategyKey": detector.key,
                    "strategyName": detector.name,
                    "signalTimestamp": timestamp.isoformat(),
                    "status": "REJECTED",
                    "reasons": plan["planRejectionReasons"],
                    "rules": [*list(evaluation.get("rules") or []), *list(plan["planRules"])],
                    "paperOnly": True,
                    "liveOrdersEnabled": False,
                })
                continue
            evidence = HistoricalEvidence.from_mapping((evidence_by_strategy or {}).get(detector.key))
            evidence_payload = evidence.public(settings)
            event_id = _evaluation_id(symbol, detector.key, timestamp)
            candidate = {
                "eventId": event_id,
                "signalId": event_id.replace("NSEEV-", "NSESIG-"),
                "symbol": symbol,
                "strategyKey": detector.key,
                "strategyName": detector.name,
                "strategyVersion": detector.version,
                "strategyStatus": "WALK_FORWARD_VALIDATED" if evidence_payload["passesQualificationGate"] else "RESEARCH_ONLY_UNVALIDATED",
                "signalTimestamp": timestamp.isoformat(),
                "status": "QUALIFIED" if evidence_payload["passesQualificationGate"] else "RESEARCH_SIGNAL",
                "executionModel": "STOP_ENTRY_AFTER_COMPLETED_CANDLE",
                "quantity": settings.quantity_per_trade,
                "activityRank": activity.get("rank"),
                "activityScore": activity.get("score"),
                "relativeToNiftyPct": activity.get("relativeToNiftyPct"),
                "relativeToSectorPct": activity.get("relativeToSectorPct"),
                "whyBuy": list(evaluation.get("whyBuy") or []),
                "rules": [*list(evaluation.get("rules") or []), *list(plan["planRules"])],
                "trigger": dict(evaluation.get("trigger") or {}),
                "historicalEvidence": evidence_payload,
                **{key: value for key, value in plan.items() if key not in {"planReady", "planRejectionReasons", "planRules"}},
                "paperOnly": True,
                "liveOrdersEnabled": False,
            }
            candidates.append(candidate)

    candidates.sort(
        key=lambda item: (
            -int(bool(item["historicalEvidence"]["passesQualificationGate"])),
            -float(item.get("relativeToNiftyPct") or 0.0),
            -float(item.get("trigger", {}).get("rvol") or 0.0),
            str(item["symbol"]),
            str(item["strategyKey"]),
        )
    )
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank
    qualified = [candidate for candidate in candidates if candidate["status"] == "QUALIFIED"]
    research = [candidate for candidate in candidates if candidate["status"] == "RESEARCH_SIGNAL"]

    return {
        "metadata": {
            "funnelVersion": FUNNEL_VERSION,
            "engineVersion": ENGINE_VERSION,
            "generatedAt": timestamp.isoformat(),
            "completedCandlesOnly": True,
            "paperOnly": True,
            "liveOrdersEnabled": False,
            "strategies": [
                {
                    "key": detector.key,
                    "name": detector.name,
                    "version": detector.version,
                    "tradeReadyAllowed": HistoricalEvidence.from_mapping((evidence_by_strategy or {}).get(detector.key)).passes(settings),
                }
                for detector in DETECTORS
            ],
            "configuration": settings.public(),
            "marketContext": context,
            "rankingPolicy": "DISPLAY_ORDER_ONLY_NEVER_HIDES_SIGNALS",
        },
        "counts": {
            "tradeable": len(eligible_rows),
            "strategyEvaluations": len(eligible_rows) * len(DETECTORS),
            "validSetups": len(candidates),
            "qualified": len(qualified),
            "researchSignals": len(research),
            "tradeReady": len(qualified),
            "watch": len(research),
            "rejected": len(rejected),
        },
        "allSignals": candidates,
        "tradeReady": qualified,
        "watch": research,
        "paperExecuted": [],
        "paperSkippedRisk": [],
        "rejected": rejected,
        "rejectionCounts": dict(sorted(rejection_counts.items(), key=lambda pair: (-pair[1], pair[0]))),
    }


class NseSignalFunnelRepository:
    """Append-only, deduplicated audit history for accepted and rejected evaluations."""

    def __init__(self, database_path: Path) -> None:
        if not database_path.is_absolute():
            raise ValueError("Signal-funnel database path must be absolute")
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS nse_signal_funnel_events (
                    event_id TEXT PRIMARY KEY,
                    session_date TEXT NOT NULL,
                    signal_timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    strategy_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def persist(self, funnel: Mapping[str, Any]) -> int:
        metadata = dict(funnel.get("metadata") or {})
        generated = _as_ist(metadata.get("generatedAt") or datetime.now(IST))
        rows = [
            *(dict(item) for item in funnel.get("tradeReady", [])),
            *(dict(item) for item in funnel.get("watch", [])),
            *(dict(item) for item in funnel.get("rejected", [])),
        ]
        inserted = 0
        with self._lock, self._connect() as connection:
            for row in rows:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO nse_signal_funnel_events (
                        event_id, session_date, signal_timestamp, symbol,
                        strategy_key, status, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(row["eventId"]),
                        generated.date().isoformat(),
                        str(row.get("signalTimestamp") or generated.isoformat()),
                        str(row["symbol"]),
                        str(row["strategyKey"]),
                        str(row["status"]),
                        json.dumps(row, ensure_ascii=False, separators=(",", ":")),
                        datetime.now(IST).isoformat(),
                    ),
                )
                inserted += int(cursor.rowcount == 1)
        return inserted

    def enforce_daily_controls_and_persist(
        self,
        funnel: Mapping[str, Any],
    ) -> tuple[dict[str, Any], int]:
        """Apply the durable daily limits before recording the visible decision.

        A repeated refresh of the same deterministic event remains paper-executed
        and does not consume another slot. Risk limits never hide or reject a valid
        signal: they only decide whether the paper portfolio executes it.
        """
        controlled = deepcopy(dict(funnel))
        metadata = dict(controlled.get("metadata") or {})
        generated = _as_ist(metadata.get("generatedAt") or datetime.now(IST))
        session_date = generated.date().isoformat()
        configuration = dict(metadata.get("configuration") or {})
        daily_limit = max(1, int(configuration.get("maximumTradesPerDay") or 5))
        with self._lock, self._connect() as connection:
            existing_rows = connection.execute(
                """
                SELECT event_id, symbol FROM nse_signal_funnel_events
                WHERE session_date = ? AND status = 'PAPER_EXECUTED'
                """,
                (session_date,),
            ).fetchall()
        existing_ids = {str(row[0]) for row in existing_rows}
        accepted_symbols = {str(row[1]) for row in existing_rows}
        accepted_count = len(existing_ids)
        executed_now: list[dict[str, Any]] = []
        skipped_now: list[dict[str, Any]] = []
        visible_qualified: list[dict[str, Any]] = []
        for raw in controlled.get("tradeReady", []):
            signal = dict(raw)
            event_id = str(signal["eventId"])
            symbol = str(signal["symbol"])
            if event_id in existing_ids:
                executed = {**signal, "status": "PAPER_EXECUTED", "paperDecision": "EXECUTED"}
                executed_now.append(executed)
                visible_qualified.append(executed)
                continue
            reason = None
            if symbol in accepted_symbols:
                reason = "ONE_TRADE_PER_SYMBOL_PER_DAY"
            elif accepted_count >= daily_limit:
                reason = "DAILY_TRADE_LIMIT"
            if reason is not None:
                skipped = {
                    **signal,
                    "status": "PAPER_SKIPPED_RISK_LIMIT",
                    "paperDecision": "SKIPPED",
                    "paperSkipReasons": [reason],
                }
                skipped_now.append(skipped)
                visible_qualified.append(skipped)
                continue
            executed = {**signal, "status": "PAPER_EXECUTED", "paperDecision": "EXECUTED"}
            executed_now.append(executed)
            visible_qualified.append(executed)
            existing_ids.add(event_id)
            accepted_symbols.add(symbol)
            accepted_count += 1

        for rank, signal in enumerate(visible_qualified, start=1):
            signal["rank"] = rank
        controlled["tradeReady"] = visible_qualified
        controlled["paperExecuted"] = executed_now
        controlled["paperSkippedRisk"] = skipped_now
        paper_by_event = {str(signal["eventId"]): signal for signal in visible_qualified}
        controlled["allSignals"] = [
            paper_by_event.get(str(signal.get("eventId")), dict(signal))
            for signal in controlled.get("allSignals", [])
        ]
        counts = dict(controlled.get("counts") or {})
        counts["tradeReady"] = len(visible_qualified)
        counts["paperExecuted"] = len(executed_now)
        counts["paperSkippedRisk"] = len(skipped_now)
        controlled["counts"] = counts
        controlled["metadata"] = {
            **metadata,
            "dailyTradeReadyAccepted": accepted_count,
            "dailyTradeReadyRemaining": max(0, daily_limit - accepted_count),
        }
        inserted = self.persist(controlled)
        return controlled, inserted

    def recent(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM nse_signal_funnel_events ORDER BY signal_timestamp DESC, event_id DESC LIMIT ?",
                (max(1, min(int(limit), 5_000)),),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]
