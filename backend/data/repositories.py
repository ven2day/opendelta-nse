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
    "signal_timestamp", "signal_price", "entry_timestamp", "entry_price", "quantity", "target_price", "stop_price", "expires_at",
    "exit_timestamp", "exit_price", "status", "gross_pnl", "fees", "slippage", "net_pnl", "unrealized_pnl",
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
        "maePct": row["mae_pct"],
        "mfePct": row["mfe_pct"],
        "holdingBars": int(row["holding_bars"]),
        "holdingMinutes": row["holding_minutes"],
    }


class BacktestTradeRepository:
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

    def list(self, run_id: uuid.UUID | str, *, symbol: str | None = None, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        if symbol:
            rows = self.database.fetch_all(
                "SELECT * FROM backtest_trades WHERE run_id = %s AND symbol = %s ORDER BY entry_timestamp, lot_id LIMIT %s OFFSET %s",
                (uuid.UUID(str(run_id)), symbol, limit, offset),
            )
        else:
            rows = self.database.fetch_all(
                "SELECT * FROM backtest_trades WHERE run_id = %s ORDER BY entry_timestamp, lot_id LIMIT %s OFFSET %s",
                (uuid.UUID(str(run_id)), limit, offset),
            )
        return [_public_trade(row) for row in rows]

    def count(self, run_id: uuid.UUID | str) -> int:
        row = self.database.fetch_one("SELECT count(*) AS total FROM backtest_trades WHERE run_id = %s", (uuid.UUID(str(run_id)),))
        return int(row["total"]) if row else 0


def _trade_value(row: Mapping[str, Any], column: str) -> Any:
    value = row.get(column)
    if column == "run_id":
        return uuid.UUID(str(value))
    return value
