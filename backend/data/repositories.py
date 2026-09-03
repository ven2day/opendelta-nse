"""Repositories over the platform tables. One class per aggregate; no business rules here."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

from backend.data.database import Database, jsonb

ACTIVE_RUN_STATUSES = ("QUEUED", "RUNNING")
TERMINAL_RUN_STATUSES = ("COMPLETE", "FAILED", "CANCELLED", "INTERRUPTED")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public_run(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "runId": str(row["run_id"]),
        "market": row["market"],
        "strategyId": row["strategy_id"],
        "strategyVersion": row["strategy_version"],
        "configurationSnapshot": row["configuration_snapshot"],
        "executionSettings": row["execution_settings"],
        "timeframe": row["timeframe"],
        "symbols": row["symbols"],
        "startDate": row["start_date"].isoformat() if row["start_date"] else None,
        "endDate": row["end_date"].isoformat() if row["end_date"] else None,
        "status": row["status"],
        "cancelRequested": bool(row["cancel_requested"]),
        "symbolsTotal": int(row["symbols_total"]),
        "symbolsCompleted": int(row["symbols_completed"]),
        "currentSymbol": row["current_symbol"],
        "failedSymbols": row["failed_symbols"],
        "metrics": row["metrics"],
        "error": row["error"],
        "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
        "startedAt": row["started_at"].isoformat() if row["started_at"] else None,
        "completedAt": row["completed_at"].isoformat() if row["completed_at"] else None,
        "updatedAt": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


class BacktestRunRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        *,
        market: str,
        strategy_id: str,
        strategy_version: str,
        configuration_snapshot: Mapping[str, Any],
        execution_settings: Mapping[str, Any],
        timeframe: str,
        symbols: Sequence[str],
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        run_id = uuid.uuid4()
        self.database.execute(
            """
            INSERT INTO backtest_runs (
                run_id, market, strategy_id, strategy_version, configuration_snapshot, execution_settings,
                timeframe, symbols, start_date, end_date, status, symbols_total
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'QUEUED', %s)
            """,
            (run_id, market, strategy_id, strategy_version, jsonb(dict(configuration_snapshot)), jsonb(dict(execution_settings)), timeframe, jsonb(list(symbols)), start_date, end_date, len(symbols)),
        )
        return self.get(run_id)

    def get(self, run_id: uuid.UUID | str) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM backtest_runs WHERE run_id = %s", (uuid.UUID(str(run_id)),))
        if row is None:
            raise KeyError(f"Backtest run {run_id} was not found")
        return _public_run(row)

    def list(self, market: str | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
        if market:
            rows = self.database.fetch_all("SELECT * FROM backtest_runs WHERE market = %s ORDER BY created_at DESC LIMIT %s", (market, limit))
        else:
            rows = self.database.fetch_all("SELECT * FROM backtest_runs ORDER BY created_at DESC LIMIT %s", (limit,))
        return [_public_run(row) for row in rows]

    def mark_started(self, run_id: uuid.UUID | str) -> None:
        self.database.execute(
            "UPDATE backtest_runs SET status = 'RUNNING', started_at = %s, updated_at = %s WHERE run_id = %s AND status = 'QUEUED'",
            (_now(), _now(), uuid.UUID(str(run_id))),
        )

    def update_progress(self, run_id: uuid.UUID | str, *, symbols_completed: int, current_symbol: str | None, failed_symbols: Sequence[Mapping[str, str]]) -> None:
        self.database.execute(
            "UPDATE backtest_runs SET symbols_completed = %s, current_symbol = %s, failed_symbols = %s, updated_at = %s WHERE run_id = %s",
            (symbols_completed, current_symbol, jsonb(list(failed_symbols)), _now(), uuid.UUID(str(run_id))),
        )

    def request_cancel(self, run_id: uuid.UUID | str) -> dict[str, Any]:
        self.database.execute(
            "UPDATE backtest_runs SET cancel_requested = true, updated_at = %s WHERE run_id = %s AND status IN ('QUEUED', 'RUNNING')",
            (_now(), uuid.UUID(str(run_id))),
        )
        return self.get(run_id)

    def cancel_requested(self, run_id: uuid.UUID | str) -> bool:
        row = self.database.fetch_one("SELECT cancel_requested FROM backtest_runs WHERE run_id = %s", (uuid.UUID(str(run_id)),))
        return bool(row and row["cancel_requested"])

    def finish(self, run_id: uuid.UUID | str, *, status: str, metrics: Mapping[str, Any] | None, error: str | None = None) -> dict[str, Any]:
        if status not in TERMINAL_RUN_STATUSES:
            raise ValueError(f"{status} is not a terminal run status")
        self.database.execute(
            "UPDATE backtest_runs SET status = %s, metrics = %s, error = %s, current_symbol = NULL, completed_at = %s, updated_at = %s WHERE run_id = %s",
            (status, jsonb(dict(metrics)) if metrics is not None else None, error, _now(), _now(), uuid.UUID(str(run_id))),
        )
        return self.get(run_id)

    def interrupt_stale(self) -> int:
        """Runs left QUEUED/RUNNING by a previous process can never finish; say so."""
        return self.database.execute(
            "UPDATE backtest_runs SET status = 'INTERRUPTED', error = COALESCE(error, 'Interrupted by a service restart'), completed_at = %s, updated_at = %s WHERE status IN ('QUEUED', 'RUNNING')",
            (_now(), _now()),
        )


TRADE_COLUMNS = (
    "run_id", "market", "strategy_id", "strategy_version", "symbol", "timeframe", "lot_id", "cycle_id", "lot_number",
    "signal_timestamp", "signal_price", "entry_timestamp", "entry_price", "cost_basis_price", "fifo_allocations", "quantity", "target_price", "stop_price", "expires_at",
    "exit_timestamp", "exit_price", "status", "gross_pnl", "fees", "slippage", "net_pnl", "unrealized_pnl", "last_price",
    "mae_pct", "mfe_pct", "holding_bars", "holding_minutes",
)


def _public_trade(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "tradeId": int(row["trade_id"]),
        "runId": str(row["run_id"]),
        "market": row["market"],
        "strategyId": row["strategy_id"],
        "strategyVersion": row["strategy_version"],
        "timeframe": row["timeframe"],
        "symbol": row["symbol"],
        "lotId": row["lot_id"],
        "cycleId": row["cycle_id"],
        "lotNumber": int(row["lot_number"]),
        "signalTimestamp": row["signal_timestamp"].isoformat(),
        "signalPrice": row["signal_price"],
        "entryTimestamp": row["entry_timestamp"].isoformat(),
        "entryPrice": row["entry_price"],
        "costBasisPrice": row["cost_basis_price"],
        "fifoAllocations": row["fifo_allocations"],
        "quantity": row["quantity"],
        "targetPrice": row["target_price"],
        "stopPrice": row["stop_price"],
        "expiresAt": row["expires_at"].isoformat() if row["expires_at"] else None,
        "exitTimestamp": row["exit_timestamp"].isoformat() if row["exit_timestamp"] else None,
        "exitPrice": row["exit_price"],
        "status": row["status"],
        "grossPnl": row["gross_pnl"],
        "fees": row["fees"],
        "slippage": row["slippage"],
        "netPnl": row["net_pnl"],
        "unrealizedPnl": row["unrealized_pnl"],
        "lastPrice": row["last_price"],
        "maePct": row["mae_pct"],
        "mfePct": row["mfe_pct"],
        "holdingBars": int(row["holding_bars"]),
        "holdingMinutes": row["holding_minutes"],
    }


class BacktestTradeRepository:
    SORT_COLUMNS = {
        "symbol": "symbol",
        "status": "status",
        "entryTimestamp": "entry_timestamp",
        "entryPrice": "entry_price",
        "quantity": "quantity",
        "targetPrice": "target_price",
        "stopPrice": "stop_price",
        "exitTimestamp": "exit_timestamp",
        "exitPrice": "exit_price",
        "netPnl": "CASE WHEN status = 'OPEN' THEN unrealized_pnl ELSE net_pnl END",
        "maePct": "mae_pct",
        "mfePct": "mfe_pct",
        "holdingMinutes": "holding_minutes",
    }

    def __init__(self, database: Database) -> None:
        self.database = database

    def insert_many(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        placeholders = ", ".join(["%s"] * len(TRADE_COLUMNS))
        query = f"INSERT INTO backtest_trades ({', '.join(TRADE_COLUMNS)}) VALUES ({placeholders}) ON CONFLICT (run_id, lot_id) DO NOTHING"
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.executemany(query, [tuple(_trade_value(row, column) for column in TRADE_COLUMNS) for row in rows])
        return len(rows)

    @classmethod
    def _query_parts(
        cls,
        run_id: uuid.UUID | str,
        *,
        symbol: str | None,
        status: str | None,
    ) -> tuple[str, list[Any]]:
        clauses = ["run_id = %s"]
        parameters: list[Any] = [uuid.UUID(str(run_id))]
        if symbol:
            clauses.append("symbol ILIKE %s")
            parameters.append(f"%{symbol}%")
        if status:
            clauses.append("status = %s")
            parameters.append(status)
        return " AND ".join(clauses), parameters

    def list(
        self,
        run_id: uuid.UUID | str,
        *,
        symbol: str | None = None,
        status: str | None = None,
        sort_by: str = "entryTimestamp",
        direction: str = "asc",
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        try:
            sort_column = self.SORT_COLUMNS[sort_by]
        except KeyError as error:
            raise ValueError(f"Unsupported trade sort column: {sort_by}") from error
        direction_key = direction.strip().lower()
        if direction_key not in {"asc", "desc"}:
            raise ValueError("Trade sort direction must be asc or desc")
        where, parameters = self._query_parts(run_id, symbol=symbol, status=status)
        rows = self.database.fetch_all(
            f"SELECT * FROM backtest_trades WHERE {where} ORDER BY {sort_column} {direction_key.upper()} NULLS LAST, lot_id ASC LIMIT %s OFFSET %s",
            (*parameters, limit, offset),
        )
        return [_public_trade(row) for row in rows]

    def count(self, run_id: uuid.UUID | str, *, symbol: str | None = None, status: str | None = None) -> int:
        where, parameters = self._query_parts(run_id, symbol=symbol, status=status)
        row = self.database.fetch_one(f"SELECT count(*) AS total FROM backtest_trades WHERE {where}", parameters)
        return int(row["total"]) if row else 0


def _trade_value(row: Mapping[str, Any], column: str) -> Any:
    value = row.get(column)
    if column == "run_id":
        return uuid.UUID(str(value))
    if column == "fifo_allocations":
        return jsonb(value or [])
    return value


# ---------------------------------------------------------------- live signals

SIGNAL_STATUSES = ("STRONG_BUY", "HOLDING", "TARGET_HIT", "EXITED", "EXPIRED")
OPEN_SIGNAL_STATUSES = ("STRONG_BUY", "HOLDING")


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _public_signal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "signalId": str(row["signal_id"]),
        "market": row["market"],
        "strategyId": row["strategy_id"],
        "strategyVersion": row["strategy_version"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "candleTimestamp": _iso(row["candle_timestamp"]),
        "signalType": row["signal_type"],
        "status": row["status"],
        "signalPrice": row["signal_price"],
        "targetPrice": row["target_price"],
        "stopPrice": row["stop_price"],
        "expiresAt": _iso(row["expires_at"]),
        "reasons": row["reasons"],
        "indicators": row["indicators"],
        "configurationSnapshot": row["configuration_snapshot"],
        "lastPrice": row["last_price"],
        "exitTimestamp": _iso(row["exit_timestamp"]),
        "exitPrice": row["exit_price"],
        "createdAt": _iso(row["created_at"]),
        "updatedAt": _iso(row["updated_at"]),
    }


class LiveSignalRepository:
    """Stored signals; the database's uniqueness constraint is the duplicate guard."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def insert_new(
        self,
        *,
        market: str,
        strategy_id: str,
        strategy_version: str,
        symbol: str,
        timeframe: str,
        candle_timestamp: datetime,
        signal_type: str,
        signal_price: float,
        target_price: float | None,
        stop_price: float | None,
        expires_at: datetime | None,
        reasons: Sequence[str],
        indicators: Mapping[str, Any],
        configuration_snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Insert and return the stored signal, or ``None`` if an identical signal already exists."""
        signal_id = uuid.uuid4()
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO live_signals (
                    signal_id, market, strategy_id, strategy_version, symbol, timeframe, candle_timestamp, signal_type, status,
                    signal_price, target_price, stop_price, expires_at, reasons, indicators, configuration_snapshot, last_price
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'STRONG_BUY', %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT live_signals_unique_candle DO NOTHING
                RETURNING *
                """,
                (
                    signal_id, market, strategy_id, strategy_version, symbol, timeframe, candle_timestamp, signal_type,
                    signal_price, target_price, stop_price, expires_at, jsonb(list(reasons)), jsonb(dict(indicators)), jsonb(dict(configuration_snapshot)), signal_price,
                ),
            )
            row = cursor.fetchone()
        return _public_signal(row) if row else None

    def get(self, signal_id: uuid.UUID | str) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM live_signals WHERE signal_id = %s", (uuid.UUID(str(signal_id)),))
        if row is None:
            raise KeyError(f"Signal {signal_id} was not found")
        return _public_signal(row)

    def list(self, market: str | None = None, *, status: str | None = None, symbol: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if market:
            clauses.append("market = %s")
            parameters.append(market)
        if status:
            clauses.append("status = %s")
            parameters.append(status)
        if symbol:
            clauses.append("symbol = %s")
            parameters.append(symbol)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.database.fetch_all(f"SELECT * FROM live_signals{where} ORDER BY candle_timestamp DESC LIMIT %s", (*parameters, limit))
        return [_public_signal(row) for row in rows]

    def open(self, market: str, symbol: str | None = None) -> list[dict[str, Any]]:
        if symbol:
            rows = self.database.fetch_all("SELECT * FROM live_signals WHERE market = %s AND symbol = %s AND status IN ('STRONG_BUY', 'HOLDING') ORDER BY candle_timestamp", (market, symbol))
        else:
            rows = self.database.fetch_all("SELECT * FROM live_signals WHERE market = %s AND status IN ('STRONG_BUY', 'HOLDING') ORDER BY candle_timestamp", (market,))
        return [_public_signal(row) for row in rows]

    def mark_holding(self, signal_id: uuid.UUID | str, *, last_price: float) -> None:
        self.database.execute(
            "UPDATE live_signals SET status = 'HOLDING', last_price = %s, updated_at = %s WHERE signal_id = %s AND status = 'STRONG_BUY'",
            (last_price, _now(), uuid.UUID(str(signal_id))),
        )

    def update_last_price(self, signal_id: uuid.UUID | str, *, last_price: float) -> None:
        self.database.execute("UPDATE live_signals SET last_price = %s, updated_at = %s WHERE signal_id = %s", (last_price, _now(), uuid.UUID(str(signal_id))))

    def close(self, signal_id: uuid.UUID | str, *, status: str, exit_timestamp: datetime, exit_price: float) -> dict[str, Any]:
        if status not in ("TARGET_HIT", "EXITED", "EXPIRED"):
            raise ValueError(f"{status} is not a closing signal status")
        self.database.execute(
            "UPDATE live_signals SET status = %s, exit_timestamp = %s, exit_price = %s, last_price = %s, updated_at = %s WHERE signal_id = %s AND status IN ('STRONG_BUY', 'HOLDING')",
            (status, exit_timestamp, exit_price, exit_price, _now(), uuid.UUID(str(signal_id))),
        )
        return self.get(signal_id)


class EngineStatusRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def upsert(
        self,
        *,
        engine: str,
        market: str,
        status: str,
        connection_status: str | None,
        data_age_seconds: float | None,
        last_completed_candle: datetime | None,
        message: str | None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.database.execute(
            """
            INSERT INTO engine_status (engine, market, status, connection_status, data_age_seconds, last_completed_candle, message, details, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (engine, market) DO UPDATE SET
                status = EXCLUDED.status, connection_status = EXCLUDED.connection_status, data_age_seconds = EXCLUDED.data_age_seconds,
                last_completed_candle = EXCLUDED.last_completed_candle, message = EXCLUDED.message, details = EXCLUDED.details, updated_at = EXCLUDED.updated_at
            """,
            (engine, market, status, connection_status, data_age_seconds, last_completed_candle, message, jsonb(dict(details or {})), _now()),
        )

    def get(self, engine: str, market: str) -> dict[str, Any] | None:
        row = self.database.fetch_one("SELECT * FROM engine_status WHERE engine = %s AND market = %s", (engine, market))
        return _public_engine_status(row) if row else None

    def list(self) -> list[dict[str, Any]]:
        return [_public_engine_status(row) for row in self.database.fetch_all("SELECT * FROM engine_status ORDER BY engine, market")]


def _public_engine_status(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "engine": row["engine"],
        "market": row["market"],
        "status": row["status"],
        "connectionStatus": row["connection_status"],
        "dataAgeSeconds": row["data_age_seconds"],
        "lastCompletedCandle": _iso(row["last_completed_candle"]),
        "message": row["message"],
        "details": row["details"],
        "updatedAt": _iso(row["updated_at"]),
    }


class StrategyConfigRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def active(self, market: str, strategy_id: str) -> dict[str, Any] | None:
        row = self.database.fetch_one("SELECT * FROM strategy_configs WHERE market = %s AND strategy_id = %s AND active", (market, strategy_id))
        return _public_config(row) if row else None

    def save(self, *, market: str, strategy_id: str, strategy_version: str, name: str, configuration: Mapping[str, Any], risk_settings: Mapping[str, Any], activate: bool) -> dict[str, Any]:
        config_id = uuid.uuid4()
        with self.database.transaction() as connection, connection.cursor() as cursor:
            if activate:
                cursor.execute("UPDATE strategy_configs SET active = false, updated_at = %s WHERE market = %s AND strategy_id = %s AND active", (_now(), market, strategy_id))
            cursor.execute(
                """
                INSERT INTO strategy_configs (config_id, market, strategy_id, strategy_version, name, configuration, risk_settings, active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT strategy_configs_name DO UPDATE SET
                    strategy_version = EXCLUDED.strategy_version, configuration = EXCLUDED.configuration, risk_settings = EXCLUDED.risk_settings,
                    active = EXCLUDED.active, updated_at = now()
                RETURNING *
                """,
                (config_id, market, strategy_id, strategy_version, name, jsonb(dict(configuration)), jsonb(dict(risk_settings)), activate),
            )
            row = cursor.fetchone()
        return _public_config(row)

    def list(self, market: str | None = None) -> list[dict[str, Any]]:
        if market:
            rows = self.database.fetch_all("SELECT * FROM strategy_configs WHERE market = %s ORDER BY strategy_id, name", (market,))
        else:
            rows = self.database.fetch_all("SELECT * FROM strategy_configs ORDER BY market, strategy_id, name")
        return [_public_config(row) for row in rows]


def _public_config(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "configId": str(row["config_id"]),
        "market": row["market"],
        "strategyId": row["strategy_id"],
        "strategyVersion": row["strategy_version"],
        "name": row["name"],
        "configuration": row["configuration"],
        "riskSettings": row["risk_settings"],
        "active": bool(row["active"]),
        "createdAt": _iso(row["created_at"]),
        "updatedAt": _iso(row["updated_at"]),
    }


class SavedUniverseRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def active(self, market: str) -> dict[str, Any] | None:
        row = self.database.fetch_one("SELECT * FROM saved_universes WHERE market = %s AND active", (market,))
        return _public_universe(row) if row else None

    def active_symbols(self, market: str) -> list[str]:
        record = self.active(market)
        if record is None:
            return []
        excluded = set(record["manualExcludes"])
        ordered = [symbol for symbol in [*record["symbols"], *record["manualIncludes"]] if symbol not in excluded]
        return list(dict.fromkeys(ordered))

    def save(self, *, market: str, name: str, symbols: Sequence[str], source_run_id: str | None = None, manual_includes: Sequence[str] = (), manual_excludes: Sequence[str] = (), activate: bool = True) -> dict[str, Any]:
        universe_id = uuid.uuid4()
        with self.database.transaction() as connection, connection.cursor() as cursor:
            if activate:
                cursor.execute("UPDATE saved_universes SET active = false WHERE market = %s AND active", (market,))
            cursor.execute(
                """
                INSERT INTO saved_universes (universe_id, market, name, source_run_id, symbols, manual_includes, manual_excludes, active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
                """,
                (universe_id, market, name, uuid.UUID(str(source_run_id)) if source_run_id else None, jsonb(list(symbols)), jsonb(list(manual_includes)), jsonb(list(manual_excludes)), activate),
            )
            row = cursor.fetchone()
        return _public_universe(row)

    def list(self, market: str | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
        if market:
            rows = self.database.fetch_all("SELECT * FROM saved_universes WHERE market = %s ORDER BY created_at DESC LIMIT %s", (market, limit))
        else:
            rows = self.database.fetch_all("SELECT * FROM saved_universes ORDER BY created_at DESC LIMIT %s", (limit,))
        return [_public_universe(row) for row in rows]

    def activate(self, universe_id: uuid.UUID | str) -> dict[str, Any]:
        key = uuid.UUID(str(universe_id))
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT market FROM saved_universes WHERE universe_id = %s", (key,))
            row = cursor.fetchone()
            if row is None:
                raise KeyError(f"Universe {universe_id} was not found")
            cursor.execute("UPDATE saved_universes SET active = false WHERE market = %s AND active", (row["market"],))
            cursor.execute("UPDATE saved_universes SET active = true WHERE universe_id = %s RETURNING *", (key,))
            updated = cursor.fetchone()
        return _public_universe(updated)


# ---------------------------------------------------------------- screener


def _public_screener_run(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "runId": str(row["run_id"]),
        "market": row["market"],
        "status": row["status"],
        "filters": row["filters"],
        "symbolsTotal": int(row["symbols_total"]),
        "symbolsPassed": int(row["symbols_passed"]),
        "error": row["error"],
        "requestedAt": _iso(row["requested_at"]),
        "completedAt": _iso(row["completed_at"]),
    }


class ScreenerRunRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, *, market: str, filters: Mapping[str, Any], symbols_total: int) -> dict[str, Any]:
        run_id = uuid.uuid4()
        self.database.execute(
            "INSERT INTO screener_runs (run_id, market, status, filters, symbols_total) VALUES (%s, %s, 'RUNNING', %s, %s)",
            (run_id, market, jsonb(dict(filters)), symbols_total),
        )
        return self.get(run_id)

    def get(self, run_id: uuid.UUID | str) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM screener_runs WHERE run_id = %s", (uuid.UUID(str(run_id)),))
        if row is None:
            raise KeyError(f"Screener run {run_id} was not found")
        return _public_screener_run(row)

    def list(self, market: str | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
        if market:
            rows = self.database.fetch_all("SELECT * FROM screener_runs WHERE market = %s ORDER BY requested_at DESC LIMIT %s", (market, limit))
        else:
            rows = self.database.fetch_all("SELECT * FROM screener_runs ORDER BY requested_at DESC LIMIT %s", (limit,))
        return [_public_screener_run(row) for row in rows]

    def finish(self, run_id: uuid.UUID | str, *, status: str, symbols_passed: int, error: str | None = None) -> dict[str, Any]:
        if status not in ("COMPLETE", "FAILED"):
            raise ValueError(f"{status} is not a terminal screener status")
        self.database.execute(
            "UPDATE screener_runs SET status = %s, symbols_passed = %s, error = %s, completed_at = %s WHERE run_id = %s",
            (status, symbols_passed, error, _now(), uuid.UUID(str(run_id))),
        )
        return self.get(run_id)

    def recover_interrupted(self) -> int:
        """Fail unfinished in-process runs after a service restart."""
        return self.database.execute(
            "UPDATE screener_runs SET status = 'FAILED', error = COALESCE(error, 'Interrupted by a service restart'), completed_at = %s WHERE status = 'RUNNING'",
            (_now(),),
        )


def _public_screener_result(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "runId": str(row["run_id"]),
        "symbol": row["symbol"],
        "passed": bool(row["passed"]),
        "rank": row["rank"],
        "score": row["score"],
        "rejectionReason": row["rejection_reason"],
        "metrics": row["metrics"],
    }


class ScreenerResultRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def insert_many(self, run_id: uuid.UUID | str, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO screener_results (run_id, symbol, passed, rank, score, rejection_reason, metrics) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (run_id, symbol) DO UPDATE SET passed = EXCLUDED.passed, rank = EXCLUDED.rank, score = EXCLUDED.score, rejection_reason = EXCLUDED.rejection_reason, metrics = EXCLUDED.metrics",
                [(uuid.UUID(str(run_id)), row["symbol"], bool(row["passed"]), row.get("rank"), row.get("score"), row.get("rejection_reason"), jsonb(dict(row.get("metrics") or {}))) for row in rows],
            )
        return len(rows)

    def list(self, run_id: uuid.UUID | str, *, passed: bool | None = None, limit: int = 5_000) -> list[dict[str, Any]]:
        if passed is None:
            rows = self.database.fetch_all("SELECT * FROM screener_results WHERE run_id = %s ORDER BY passed DESC, rank NULLS LAST, symbol LIMIT %s", (uuid.UUID(str(run_id)), limit))
        else:
            rows = self.database.fetch_all("SELECT * FROM screener_results WHERE run_id = %s AND passed = %s ORDER BY rank NULLS LAST, symbol LIMIT %s", (uuid.UUID(str(run_id)), passed, limit))
        return [_public_screener_result(row) for row in rows]


# ---------------------------------------------------------------- paper trading

PAPER_LOT_OPEN = "OPEN"
PAPER_LOT_CLOSED_STATUSES = ("TARGET_HIT", "STOPPED", "EXPIRED", "CLOSED")


def _public_account(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "accountId": str(row["account_id"]),
        "market": row["market"],
        "currency": row["currency"],
        "startingBalance": row["starting_balance"],
        "cashBalance": row["cash_balance"],
        "riskSettings": row["risk_settings"],
        "createdAt": _iso(row["created_at"]),
        "updatedAt": _iso(row["updated_at"]),
        "resetAt": _iso(row["reset_at"]),
    }


class PaperAccountRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get(self, market: str) -> dict[str, Any] | None:
        row = self.database.fetch_one("SELECT * FROM paper_accounts WHERE market = %s", (market,))
        return _public_account(row) if row else None

    def get_or_create(self, market: str, *, currency: str, starting_balance: float, risk_settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
        existing = self.get(market)
        if existing is not None:
            return existing
        self.database.execute(
            "INSERT INTO paper_accounts (account_id, market, currency, starting_balance, cash_balance, risk_settings) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (market) DO NOTHING",
            (uuid.uuid4(), market, currency, starting_balance, starting_balance, jsonb(dict(risk_settings or {}))),
        )
        created = self.get(market)
        assert created is not None
        return created

    def reset(self, market: str, *, starting_balance: float | None = None, risk_settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Wipe orders/lots/trades for the market's account and restore the balance."""
        account = self.get(market)
        if account is None:
            raise KeyError(f"No paper account for {market}")
        balance = float(starting_balance if starting_balance is not None else account["startingBalance"])
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM paper_pending_entries WHERE account_id = %s", (uuid.UUID(account["accountId"]),))
            cursor.execute("DELETE FROM paper_trades WHERE account_id = %s", (uuid.UUID(account["accountId"]),))
            cursor.execute("DELETE FROM paper_lots WHERE account_id = %s", (uuid.UUID(account["accountId"]),))
            cursor.execute("DELETE FROM paper_orders WHERE account_id = %s", (uuid.UUID(account["accountId"]),))
            cursor.execute(
                "UPDATE paper_accounts SET starting_balance = %s, cash_balance = %s, risk_settings = %s, reset_at = %s, updated_at = %s WHERE account_id = %s",
                (balance, balance, jsonb(dict(risk_settings if risk_settings is not None else account["riskSettings"])), _now(), _now(), uuid.UUID(account["accountId"])),
            )
        refreshed = self.get(market)
        assert refreshed is not None
        return refreshed

    def adjust_cash(self, account_id: uuid.UUID | str, delta: float) -> float:
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT cash_balance FROM paper_accounts WHERE account_id = %s FOR UPDATE", (uuid.UUID(str(account_id)),))
            row = cursor.fetchone()
            if row is None:
                raise KeyError(f"Paper account {account_id} was not found")
            balance = float(row["cash_balance"]) + float(delta)
            cursor.execute("UPDATE paper_accounts SET cash_balance = %s, updated_at = %s WHERE account_id = %s", (balance, _now(), uuid.UUID(str(account_id))))
        return balance

    def list(self) -> list[dict[str, Any]]:
        return [_public_account(row) for row in self.database.fetch_all("SELECT * FROM paper_accounts ORDER BY market")]


def _public_order(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "orderId": str(row["order_id"]),
        "accountId": str(row["account_id"]),
        "market": row["market"],
        "signalId": str(row["signal_id"]) if row["signal_id"] else None,
        "strategyId": row["strategy_id"],
        "strategyVersion": row["strategy_version"],
        "symbol": row["symbol"],
        "side": row["side"],
        "quantity": row["quantity"],
        "requestedPrice": row["requested_price"],
        "executedPrice": row["executed_price"],
        "fees": row["fees"],
        "slippage": row["slippage"],
        "status": row["status"],
        "reason": row["reason"],
        "createdAt": _iso(row["created_at"]),
    }


class PaperOrderRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def insert(
        self,
        *,
        account_id: uuid.UUID | str,
        market: str,
        signal_id: str | None,
        strategy_id: str,
        strategy_version: str,
        symbol: str,
        side: str,
        quantity: float,
        requested_price: float,
        executed_price: float | None,
        fees: float,
        slippage: float,
        status: str,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Insert an order; a second filled BUY for the same signal on the same account returns ``None``."""
        order_id = uuid.uuid4()
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO paper_orders (order_id, account_id, market, signal_id, strategy_id, strategy_version, symbol, side, quantity, requested_price, executed_price, fees, slippage, status, reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING RETURNING *
                """,
                (order_id, uuid.UUID(str(account_id)), market, uuid.UUID(str(signal_id)) if signal_id else None, strategy_id, strategy_version, symbol, side, quantity, requested_price, executed_price, fees, slippage, status, reason),
            )
            row = cursor.fetchone()
        return _public_order(row) if row else None

    def list(self, account_id: uuid.UUID | str, *, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.database.fetch_all("SELECT * FROM paper_orders WHERE account_id = %s ORDER BY created_at DESC LIMIT %s", (uuid.UUID(str(account_id)), limit))
        return [_public_order(row) for row in rows]

    def for_signal(self, account_id: uuid.UUID | str, signal_id: str) -> list[dict[str, Any]]:
        rows = self.database.fetch_all("SELECT * FROM paper_orders WHERE account_id = %s AND signal_id = %s", (uuid.UUID(str(account_id)), uuid.UUID(str(signal_id))))
        return [_public_order(row) for row in rows]


def _public_pending_entry(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "pendingEntryId": str(row["pending_entry_id"]),
        "signalId": str(row["signal_id"]) if row["signal_id"] else None,
        "market": row["market"],
        "strategyId": row["strategy_id"],
        "strategyVersion": row["strategy_version"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "candleTimestamp": _iso(row["trigger_timestamp"]),
        "signalType": "BUY",
        "signalPrice": row["signal_price"],
        "targetPrice": row["target_price"],
        "stopPrice": row["stop_price"],
        "configurationSnapshot": row["configuration_snapshot"],
        "entryReason": row["entry_reason"],
        "cycleId": row["cycle_id"],
        "lotNumber": row["lot_number"],
    }


class PaperPendingEntryRepository:
    """Durable next-open entries, including price-triggered ladder additions."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def insert(self, signal: Mapping[str, Any], *, account_id: uuid.UUID | str, cycle_id: str | None = None, lot_number: int | None = None) -> dict[str, Any] | None:
        pending_entry_id = uuid.uuid4()
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO paper_pending_entries (
                    pending_entry_id, account_id, signal_id, market, strategy_id, strategy_version,
                    symbol, timeframe, trigger_timestamp, signal_price, target_price, stop_price,
                    configuration_snapshot, entry_reason, cycle_id, lot_number
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING RETURNING *
                """,
                (
                    pending_entry_id, uuid.UUID(str(account_id)), uuid.UUID(str(signal["signalId"])) if signal.get("signalId") else None,
                    signal["market"], signal["strategyId"], signal["strategyVersion"], signal["symbol"], signal["timeframe"],
                    signal["candleTimestamp"], signal["signalPrice"], signal.get("targetPrice"), signal.get("stopPrice"),
                    jsonb(dict(signal.get("configurationSnapshot") or {})), signal.get("entryReason", "SIGNAL_ENTRY"), cycle_id, lot_number,
                ),
            )
            row = cursor.fetchone()
        return _public_pending_entry(row) if row else None

    def list(self, account_id: uuid.UUID | str) -> list[dict[str, Any]]:
        rows = self.database.fetch_all(
            "SELECT * FROM paper_pending_entries WHERE account_id = %s ORDER BY trigger_timestamp, created_at",
            (uuid.UUID(str(account_id)),),
        )
        return [_public_pending_entry(row) for row in rows]

    def delete(self, pending_entry_id: uuid.UUID | str) -> None:
        self.database.execute("DELETE FROM paper_pending_entries WHERE pending_entry_id = %s", (uuid.UUID(str(pending_entry_id)),))

    def clear(self, account_id: uuid.UUID | str) -> None:
        self.database.execute("DELETE FROM paper_pending_entries WHERE account_id = %s", (uuid.UUID(str(account_id)),))


def _public_lot(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "lotId": str(row["lot_id"]),
        "accountId": str(row["account_id"]),
        "orderId": str(row["order_id"]),
        "signalId": str(row["signal_id"]) if row["signal_id"] else None,
        "market": row["market"],
        "strategyId": row["strategy_id"],
        "strategyVersion": row["strategy_version"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "cycleId": row["cycle_id"],
        "lotNumber": int(row["lot_number"]),
        "entryTimestamp": _iso(row["entry_timestamp"]),
        "entryPrice": row["entry_price"],
        "costBasisPrice": row["cost_basis_price"],
        "fifoAllocations": row["fifo_allocations"],
        "quantity": row["quantity"],
        "targetPrice": row["target_price"],
        "stopPrice": row["stop_price"],
        "expiresAt": _iso(row["expires_at"]),
        "status": row["status"],
        "exitTimestamp": _iso(row["exit_timestamp"]),
        "exitPrice": row["exit_price"],
        "realizedPnl": row["realized_pnl"],
        "unrealizedPnl": row["unrealized_pnl"],
        "fees": row["fees"],
        "lastPrice": row["last_price"],
        "maePct": row["mae_pct"],
        "mfePct": row["mfe_pct"],
        "configurationSnapshot": row["configuration_snapshot"],
        "createdAt": _iso(row["created_at"]),
        "updatedAt": _iso(row["updated_at"]),
    }


class PaperLotRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def insert(self, **values: Any) -> dict[str, Any]:
        lot_id = uuid.uuid4()
        self.database.execute(
            """
            INSERT INTO paper_lots (lot_id, account_id, order_id, signal_id, market, strategy_id, strategy_version, symbol, timeframe, cycle_id, lot_number,
                entry_timestamp, entry_price, cost_basis_price, fifo_allocations, quantity, target_price, stop_price, expires_at, status, fees, last_price, unrealized_pnl, mae_pct, mfe_pct, configuration_snapshot)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, %s, %s, %s, %s, %s)
            """,
            (
                lot_id, uuid.UUID(str(values["account_id"])), uuid.UUID(str(values["order_id"])), uuid.UUID(str(values["signal_id"])) if values.get("signal_id") else None,
                values["market"], values["strategy_id"], values["strategy_version"], values["symbol"], values["timeframe"], values["cycle_id"], values["lot_number"],
                values["entry_timestamp"], values["entry_price"], values.get("cost_basis_price", values["entry_price"]),
                jsonb(values.get("fifo_allocations") or [{"lotId": str(lot_id), "quantity": values["quantity"], "entryPrice": values["entry_price"], "fees": values.get("fees", 0.0)}]),
                values["quantity"], values["target_price"], values.get("stop_price"), values.get("expires_at"),
                values.get("fees", 0.0), values["entry_price"], values.get("unrealized_pnl", 0.0), 0.0, 0.0, jsonb(dict(values.get("configuration_snapshot") or {})),
            ),
        )
        return self.get(lot_id)

    def get(self, lot_id: uuid.UUID | str) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM paper_lots WHERE lot_id = %s", (uuid.UUID(str(lot_id)),))
        if row is None:
            raise KeyError(f"Paper lot {lot_id} was not found")
        return _public_lot(row)

    def open(self, account_id: uuid.UUID | str, symbol: str | None = None) -> list[dict[str, Any]]:
        if symbol:
            rows = self.database.fetch_all("SELECT * FROM paper_lots WHERE account_id = %s AND symbol = %s AND status = 'OPEN' ORDER BY entry_timestamp, lot_number", (uuid.UUID(str(account_id)), symbol))
        else:
            rows = self.database.fetch_all("SELECT * FROM paper_lots WHERE account_id = %s AND status = 'OPEN' ORDER BY symbol, entry_timestamp, lot_number", (uuid.UUID(str(account_id)),))
        return [_public_lot(row) for row in rows]

    def cycle(self, account_id: uuid.UUID | str, symbol: str, cycle_id: str) -> list[dict[str, Any]]:
        rows = self.database.fetch_all(
            "SELECT * FROM paper_lots WHERE account_id = %s AND symbol = %s AND cycle_id = %s ORDER BY lot_number",
            (uuid.UUID(str(account_id)), symbol, cycle_id),
        )
        return [_public_lot(row) for row in rows]

    def list(self, account_id: uuid.UUID | str, *, status: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        if status:
            rows = self.database.fetch_all("SELECT * FROM paper_lots WHERE account_id = %s AND status = %s ORDER BY entry_timestamp DESC LIMIT %s", (uuid.UUID(str(account_id)), status, limit))
        else:
            rows = self.database.fetch_all("SELECT * FROM paper_lots WHERE account_id = %s ORDER BY entry_timestamp DESC LIMIT %s", (uuid.UUID(str(account_id)), limit))
        return [_public_lot(row) for row in rows]

    def cycle_state(self, account_id: uuid.UUID | str, symbol: str) -> tuple[int, int]:
        """``(highest cycle number ever, open lot count)`` for a symbol."""
        row = self.database.fetch_one(
            "SELECT coalesce(max(cast(split_part(cycle_id, '-Cycle', 2) as integer)), 0) AS cycles, count(*) FILTER (WHERE status = 'OPEN') AS open_lots FROM paper_lots WHERE account_id = %s AND symbol = %s",
            (uuid.UUID(str(account_id)), symbol),
        )
        return (int(row["cycles"]), int(row["open_lots"])) if row else (0, 0)

    def mark(self, lot_id: uuid.UUID | str, *, last_price: float, cost_basis_price: float, fifo_allocations: Sequence[Mapping[str, Any]], entry_fees: float, unrealized_pnl: float, mae_pct: float, mfe_pct: float) -> None:
        self.database.execute(
            "UPDATE paper_lots SET last_price = %s, cost_basis_price = %s, fifo_allocations = %s, fees = %s, unrealized_pnl = %s, mae_pct = %s, mfe_pct = %s, updated_at = %s WHERE lot_id = %s AND status = 'OPEN'",
            (last_price, cost_basis_price, jsonb(list(fifo_allocations)), entry_fees, unrealized_pnl, mae_pct, mfe_pct, _now(), uuid.UUID(str(lot_id))),
        )

    def close(self, lot_id: uuid.UUID | str, *, status: str, exit_timestamp: datetime, exit_price: float, cost_basis_price: float, fifo_allocations: Sequence[Mapping[str, Any]], realized_pnl: float, fees: float) -> dict[str, Any]:
        if status not in PAPER_LOT_CLOSED_STATUSES:
            raise ValueError(f"{status} is not a closing lot status")
        self.database.execute(
            "UPDATE paper_lots SET status = %s, exit_timestamp = %s, exit_price = %s, cost_basis_price = %s, fifo_allocations = %s, realized_pnl = %s, fees = %s, unrealized_pnl = 0, last_price = %s, updated_at = %s WHERE lot_id = %s AND status = 'OPEN'",
            (status, exit_timestamp, exit_price, cost_basis_price, jsonb(list(fifo_allocations)), realized_pnl, fees, exit_price, _now(), uuid.UUID(str(lot_id))),
        )
        return self.get(lot_id)


def _public_paper_trade(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "tradeId": int(row["trade_id"]),
        "accountId": str(row["account_id"]),
        "lotId": str(row["lot_id"]),
        "market": row["market"],
        "symbol": row["symbol"],
        "side": row["side"],
        "quantity": row["quantity"],
        "price": row["price"],
        "fees": row["fees"],
        "slippage": row["slippage"],
        "reason": row["reason"],
        "executedAt": _iso(row["executed_at"]),
    }


class PaperTradeRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def insert(self, *, account_id: uuid.UUID | str, lot_id: uuid.UUID | str, market: str, symbol: str, side: str, quantity: float, price: float, fees: float, slippage: float, reason: str, executed_at: datetime) -> None:
        self.database.execute(
            "INSERT INTO paper_trades (account_id, lot_id, market, symbol, side, quantity, price, fees, slippage, reason, executed_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (uuid.UUID(str(account_id)), uuid.UUID(str(lot_id)), market, symbol, side, quantity, price, fees, slippage, reason, executed_at),
        )

    def list(self, account_id: uuid.UUID | str, *, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.database.fetch_all("SELECT * FROM paper_trades WHERE account_id = %s ORDER BY executed_at DESC, trade_id DESC LIMIT %s", (uuid.UUID(str(account_id)), limit))
        return [_public_paper_trade(row) for row in rows]

    def chronological(self, account_id: uuid.UUID | str) -> list[dict[str, Any]]:
        rows = self.database.fetch_all(
            "SELECT * FROM paper_trades WHERE account_id = %s ORDER BY executed_at, trade_id",
            (uuid.UUID(str(account_id)),),
        )
        return [_public_paper_trade(row) for row in rows]


def _public_universe(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "universeId": str(row["universe_id"]),
        "market": row["market"],
        "name": row["name"],
        "sourceRunId": str(row["source_run_id"]) if row["source_run_id"] else None,
        "symbols": row["symbols"],
        "manualIncludes": row["manual_includes"],
        "manualExcludes": row["manual_excludes"],
        "active": bool(row["active"]),
        "createdAt": _iso(row["created_at"]),
    }
