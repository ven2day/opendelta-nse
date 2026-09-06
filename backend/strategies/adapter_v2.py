"""Isolated adapter for an immutable Strategy V2 source snapshot."""

from __future__ import annotations

import json
import socket
from collections.abc import Mapping
from typing import Any

import pandas as pd

from backend.core.models import MarketContext, SignalDecision, normalize_candles
from backend.strategies.base import resolve_config

MAX_RESPONSE_BYTES = 32 * 1024 * 1024


def _schema(parameters: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, value in parameters.items():
        kind = "boolean" if isinstance(value, bool) else "integer" if isinstance(value, int) else "number" if isinstance(value, float) else "integer_array" if isinstance(value, list) and all(isinstance(item, int) and not isinstance(item, bool) for item in value) else "string"
        result[name] = {"type": kind, "default": value, "label": name.replace("_", " ").title()}
    return result


class StrategyRunnerClient:
    def __init__(self, socket_path: str, *, timeout_seconds: float = 100.0, execution_timeout_seconds: int = 90) -> None:
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds
        self.execution_timeout_seconds = execution_timeout_seconds

    def evaluate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        request_payload = {**dict(payload), "timeoutSeconds": self.execution_timeout_seconds}
        request = json.dumps(request_payload, separators=(",", ":"), allow_nan=False).encode() + b"\n"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(self.timeout_seconds)
            connection.connect(self.socket_path)
            connection.sendall(request)
            response = b""
            while not response.endswith(b"\n"):
                chunk = connection.recv(1024 * 1024)
                if not chunk:
                    break
                response += chunk
                if len(response) > MAX_RESPONSE_BYTES:
                    raise RuntimeError("Strategy V2 runner response is too large")
        if not response:
            raise RuntimeError("Strategy V2 runner closed without a response")
        message = json.loads(response)
        if not message.get("ok"):
            raise RuntimeError(str(message.get("error") or "Strategy V2 runner failed"))
        return dict(message["result"])


class StrategyV2BacktestAdapter:
    """Shared V2 strategy contract; all submitted source executes in the isolated runner."""

    def __init__(self, source: Mapping[str, Any], client: StrategyRunnerClient) -> None:
        manifest = dict(source["manifest"])
        self.source_id = str(source["sourceId"])
        self.source_code = str(source["sourceCode"])
        self.strategy_id = str(manifest["strategyId"])
        self.name = str(manifest["name"])
        self.version = str(manifest["version"])
        self.supported_markets = tuple(manifest["supportedMarkets"])
        self.supported_timeframes = tuple(manifest["supportedTimeframes"])
        self.config_schema = _schema(manifest.get("parameters") or {})
        self._required_history = max(1, int(manifest.get("requiredHistory", 1)))
        self.client = client

    def resolve(self, config: Mapping[str, Any] | None) -> dict[str, Any]:
        return resolve_config(self.config_schema, config)

    def validate_config(self, config: Mapping[str, Any]) -> None:
        resolved = self.resolve(config)
        json.dumps(resolved, allow_nan=False)

    def required_history(self, config: Mapping[str, Any]) -> int:
        self.validate_config(config)
        return self._required_history

    def _evaluate_rows(self, candles: pd.DataFrame, context: MarketContext, config: Mapping[str, Any]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
        data = normalize_candles(candles, context.timezone)
        if data.empty:
            raise ValueError("Strategy V2 requires at least one completed candle")
        payload = {
            "sourceCode": self.source_code,
            "market": context.market,
            "symbol": context.symbol,
            "timeframe": context.timeframe,
            "params": self.resolve(config),
            "candles": {
                "timestamp": [stamp.isoformat() for stamp in data.index],
                "open": data["Open"].tolist(), "high": data["High"].tolist(),
                "low": data["Low"].tolist(), "close": data["Close"].tolist(),
                "volume": data["Volume"].tolist(),
            },
        }
        rows = self.client.evaluate(payload).get("rows", [])
        if len(rows) != len(data):
            raise RuntimeError("Strategy V2 runner returned the wrong number of decisions")
        return data, rows

    def evaluate(self, candles: pd.DataFrame, context: MarketContext, config: Mapping[str, Any]) -> SignalDecision:
        data, rows = self._evaluate_rows(candles, context, config)
        row = rows[-1]
        return SignalDecision(
            decision=row["decision"],
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            market=context.market,
            symbol=context.symbol,
            timeframe=context.timeframe,
            candle_timestamp=data.index[-1].to_pydatetime(),
            signal_price=row.get("signalPrice"),
            target_price=row.get("targetPrice"),
            stop_price=row.get("stopPrice"),
            reasons=tuple(row.get("reasons") or ()),
            indicators=dict(row.get("indicators") or {}),
            configuration_snapshot=self.resolve(config),
        )

    def decision_frame(self, candles: pd.DataFrame, context: MarketContext, config: Mapping[str, Any]) -> pd.DataFrame:
        data, rows = self._evaluate_rows(candles, context, config)
        frame = data.copy()
        frame["Decision"] = [row["decision"] for row in rows]
        frame["SignalPrice"] = [row["signalPrice"] for row in rows]
        frame["TargetPrice"] = [row["targetPrice"] for row in rows]
        frame["StopPrice"] = [row["stopPrice"] for row in rows]
        return frame
