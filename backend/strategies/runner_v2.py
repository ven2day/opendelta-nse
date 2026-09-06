"""Secret-free Strategy V2 execution service over a Unix domain socket.

Production runs this module in its own read-only, networkless container. Every
request is evaluated in a short-lived child process with CPU/file/process
limits. The backtest API never executes submitted source in its own process.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import multiprocessing
import os
import resource
import socketserver
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd

from backend.strategies.source_v2 import validate_source

MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 90


def _safe_import(name: str, globals_: Any = None, locals_: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    del globals_, locals_, fromlist
    if level != 0 or name.split(".", 1)[0] not in {"math", "numpy", "pandas", "statistics"}:
        raise ImportError(f"Import {name!r} is not allowed")
    return __import__(name)


SAFE_BUILTINS = {
    "__import__": _safe_import,
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "float": float, "int": int, "isinstance": isinstance,
    "len": len, "list": list, "max": max, "min": min, "range": range,
    "round": round, "set": set, "sorted": sorted, "str": str, "sum": sum,
    "tuple": tuple, "zip": zip,
}


def _apply_limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (60, 65))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    resource.setrlimit(resource.RLIMIT_NPROC, (8, 8))
    os.environ.clear()


def _normalise_decision(value: Any, close: float, params: dict[str, Any]) -> dict[str, Any]:
    if value is None:
        value = "NONE"
    if isinstance(value, str):
        payload: dict[str, Any] = {"decision": value}
    elif isinstance(value, dict):
        payload = dict(value)
    else:
        raise ValueError("handle_data must return BUY, SELL, HOLD, NONE, a decision dictionary, or None")
    decision = str(payload.get("decision", payload.get("action", "NONE"))).strip().upper()
    if decision == "HOLD":
        decision = "NONE"
    if decision not in {"BUY", "SELL", "NONE"}:
        raise ValueError(f"Unsupported Strategy V2 decision {decision!r}")
    signal_price = float(payload.get("signal_price", close)) if decision != "NONE" else None
    target_price = payload.get("target_price")
    if decision == "BUY" and target_price is None:
        target_pct = payload.get("target_pct", params.get("target_pct", 1.0))
        target_price = signal_price * (1 + float(target_pct) / 100)
    stop_price = payload.get("stop_price")
    for name, number in (("signal_price", signal_price), ("target_price", target_price), ("stop_price", stop_price)):
        if number is not None and not math.isfinite(float(number)):
            raise ValueError(f"{name} must be finite")
    reasons = payload.get("reasons", [])
    indicators = payload.get("indicators", {})
    if not isinstance(reasons, (list, tuple)) or not all(isinstance(item, str) for item in reasons):
        raise ValueError("reasons must be a list of strings")
    if not isinstance(indicators, dict):
        raise ValueError("indicators must be a dictionary")
    return {
        "decision": decision,
        "signalPrice": signal_price,
        "targetPrice": float(target_price) if target_price is not None else None,
        "stopPrice": float(stop_price) if stop_price is not None else None,
        "reasons": list(reasons)[:20],
        "indicators": indicators,
    }


def evaluate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    source = str(payload.get("sourceCode", ""))
    validation = validate_source(source)
    if not validation.valid:
        raise ValueError("Strategy source failed validation: " + " ".join(validation.errors))
    candles = payload.get("candles")
    if not isinstance(candles, dict):
        raise ValueError("candles must be a column dictionary")
    frame = pd.DataFrame(candles)
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    if any(name not in frame for name in required):
        raise ValueError("candles are missing required columns")
    frame.index = pd.to_datetime(frame.pop("timestamp"), utc=True)
    frame = frame[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="raise")
    params = dict(payload.get("params") or {})
    namespace: dict[str, Any] = {"__builtins__": SAFE_BUILTINS, "pd": pd, "np": np, "math": math}
    exec(compile(source, "strategy_v2.py", "exec"), namespace, namespace)  # noqa: S102 - isolated worker purpose
    initialize = namespace["initialize"]
    handle_data = namespace["handle_data"]
    context = SimpleNamespace(
        state={}, params=params, market=str(payload["market"]), symbol=str(payload["symbol"]),
        timeframe=str(payload["timeframe"]),
    )
    initialize(context)
    warmup = max(1, int((validation.manifest or {}).get("requiredHistory", 1)))
    rows: list[dict[str, Any]] = []
    for position in range(len(frame)):
        if position + 1 < warmup:
            rows.append(_normalise_decision(None, float(frame.iloc[position]["close"]), params))
            continue
        data = SimpleNamespace(candles=frame.iloc[: position + 1], current=frame.iloc[position])
        rows.append(_normalise_decision(handle_data(context, data), float(frame.iloc[position]["close"]), params))
    return {"rows": rows}


def _child(payload: dict[str, Any], connection: Any) -> None:
    try:
        _apply_limits()
        connection.send({"ok": True, "result": evaluate_payload(payload)})
    except BaseException as error:  # noqa: BLE001 - child must report exits and resource failures
        connection.send({"ok": False, "error": f"{type(error).__name__}: {error}"[:2_000]})
    finally:
        connection.close()


def evaluate_isolated(payload: dict[str, Any], *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    parent, child = multiprocessing.Pipe(duplex=False)
    process = multiprocessing.get_context("spawn").Process(target=_child, args=(payload, child), daemon=True)
    process.start()
    child.close()
    if not parent.poll(timeout_seconds):
        process.kill()
        process.join(timeout=2)
        raise TimeoutError(f"Strategy V2 exceeded the {timeout_seconds}s execution limit")
    message = parent.recv()
    process.join(timeout=2)
    if not message.get("ok"):
        raise RuntimeError(message.get("error", "Strategy V2 worker failed"))
    return dict(message["result"])


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            response = {"ok": False, "error": "Strategy runner request is too large"}
        else:
            try:
                result = evaluate_isolated(json.loads(raw))
                response = {"ok": True, "result": result}
            except Exception as error:  # noqa: BLE001 - protocol boundary returns a bounded error
                response = {"ok": False, "error": f"{type(error).__name__}: {error}"[:2_000]}
        encoded = json.dumps(response, separators=(",", ":"), allow_nan=False).encode() + b"\n"
        if len(encoded) > MAX_RESPONSE_BYTES:
            encoded = b'{"ok":false,"error":"Strategy runner response is too large"}\n'
        self.wfile.write(encoded)


def serve(socket_path: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(socket_path)
    os.makedirs(os.path.dirname(socket_path), mode=0o700, exist_ok=True)
    with socketserver.UnixStreamServer(socket_path, _Handler) as server:
        os.chmod(socket_path, 0o660)
        server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default="/run/opendelta-strategy/runner.sock")
    args = parser.parse_args()
    serve(args.socket)


if __name__ == "__main__":
    main()
