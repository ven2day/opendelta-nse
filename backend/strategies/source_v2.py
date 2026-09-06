"""Static validation for user-authored Strategy V2 Python source.

Validation deliberately never imports or executes the submitted module. Runtime
execution belongs in a separate, resource-limited worker added in a later phase.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

MAX_SOURCE_BYTES = 128_000
ALLOWED_IMPORT_ROOTS = frozenset({"math", "numpy", "pandas", "statistics"})
REQUIRED_FUNCTIONS = frozenset({"initialize", "handle_data"})
FORBIDDEN_CALLS = frozenset(
    {
        "__import__", "breakpoint", "compile", "eval", "exec", "globals", "input",
        "locals", "open", "setattr", "delattr", "getattr", "vars",
    }
)
FORBIDDEN_ROOTS = frozenset(
    {
        "aiohttp", "asyncio", "builtins", "ctypes", "http", "importlib", "multiprocessing",
        "os", "pathlib", "pickle", "requests", "shutil", "socket", "sqlite3", "subprocess",
        "sys", "urllib",
    }
)
STRATEGY_ID = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
MARKETS = frozenset({"NSE", "CRYPTO"})
TIMEFRAMES = frozenset({"1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"})


@dataclass(frozen=True)
class SourceValidation:
    valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    manifest: dict[str, Any] | None
    code_hash: str

    def public(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "manifest": self.manifest,
            "codeHash": self.code_hash,
        }


def starter_source() -> str:
    return '''"""OpenDelta Strategy V2 example."""

STRATEGY = {
    "id": "my_strategy_v2",
    "name": "My Strategy V2",
    "version": "1.0.0",
    "markets": ["NSE", "CRYPTO"],
    "timeframes": ["5m", "15m"],
    "description": "Describe the market setup this strategy detects.",
    "parameters": {"rsi_length": 14, "rsi_low": 30},
}


def initialize(context):
    """Set derived, non-secret state once when the isolated worker starts."""
    context.state["ready"] = True


def handle_data(context, data):
    """Return BUY, SELL, HOLD or None after evaluating completed candles only."""
    close = data.candles["close"]
    if len(close) < context.params["rsi_length"] + 1:
        return None
    return "HOLD"
'''


def validate_source(source: str) -> SourceValidation:
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    errors: list[str] = []
    warnings: list[str] = []
    if not source.strip():
        return SourceValidation(False, ("Source code is empty.",), (), None, digest)
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        return SourceValidation(False, (f"Source exceeds {MAX_SOURCE_BYTES} bytes.",), (), None, digest)
    try:
        tree = ast.parse(source, filename="strategy_v2.py", mode="exec")
    except SyntaxError as error:
        location = f"line {error.lineno}" if error.lineno else "unknown line"
        return SourceValidation(False, (f"Python syntax error at {location}: {error.msg}.",), (), None, digest)

    metadata_node: ast.AST | None = None
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = node
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "STRATEGY" for target in node.targets):
            metadata_node = node.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "STRATEGY":
            metadata_node = node.value

    missing = sorted(REQUIRED_FUNCTIONS - functions.keys())
    if missing:
        errors.append("Missing required function(s): " + ", ".join(missing) + ".")
    for name in REQUIRED_FUNCTIONS & functions.keys():
        fn = functions[name]
        if isinstance(fn, ast.AsyncFunctionDef):
            errors.append(f"{name} must be synchronous.")
        if len(fn.args.args) != (1 if name == "initialize" else 2):
            signature = "initialize(context)" if name == "initialize" else "handle_data(context, data)"
            errors.append(f"Use the exact signature {signature}.")

    manifest: dict[str, Any] | None = None
    if metadata_node is None:
        errors.append("Define STRATEGY as a literal metadata dictionary.")
    else:
        try:
            raw = ast.literal_eval(metadata_node)
        except (ValueError, TypeError):
            errors.append("STRATEGY must contain literals only; computed metadata is not allowed.")
        else:
            if not isinstance(raw, dict):
                errors.append("STRATEGY must be a dictionary.")
            else:
                manifest = _validate_manifest(raw, errors)

    for walked in ast.walk(tree):
        if isinstance(walked, (ast.Import, ast.ImportFrom)):
            modules = [alias.name for alias in walked.names] if isinstance(walked, ast.Import) else [walked.module or ""]
            for module in modules:
                root = module.split(".", 1)[0]
                if root not in ALLOWED_IMPORT_ROOTS:
                    errors.append(f"Import '{module}' is not allowed.")
        elif isinstance(walked, ast.Call):
            name = _call_name(walked.func)
            root = name.split(".", 1)[0]
            if name in FORBIDDEN_CALLS or root in FORBIDDEN_ROOTS:
                errors.append(f"Call '{name}' is not allowed.")
        elif isinstance(walked, ast.Attribute) and walked.attr.startswith("__"):
            errors.append("Dunder attribute access is not allowed.")

    if "shift(-" in source.replace(" ", ""):
        errors.append("Negative shift is not allowed because it can introduce look-ahead bias.")
    if manifest and not ast.get_docstring(tree):
        warnings.append("Add a module docstring describing the strategy assumptions.")
    errors = list(dict.fromkeys(errors))
    return SourceValidation(not errors, tuple(errors), tuple(warnings), manifest, digest)


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return "<dynamic>"


def _validate_manifest(raw: dict[Any, Any], errors: list[str]) -> dict[str, Any]:
    required = {"id", "name", "version", "markets", "timeframes", "parameters"}
    missing = sorted(required - raw.keys())
    if missing:
        errors.append("STRATEGY is missing: " + ", ".join(missing) + ".")
    strategy_id = str(raw.get("id", ""))
    version = str(raw.get("version", ""))
    name = str(raw.get("name", "")).strip()
    markets = raw.get("markets", [])
    timeframes = raw.get("timeframes", [])
    parameters = raw.get("parameters", {})
    if not STRATEGY_ID.fullmatch(strategy_id):
        errors.append("STRATEGY.id must be 3-64 lowercase letters, numbers or underscores, starting with a letter.")
    if not name or len(name) > 120:
        errors.append("STRATEGY.name must be between 1 and 120 characters.")
    if not SEMVER.fullmatch(version):
        errors.append("STRATEGY.version must use semantic versioning such as 1.0.0.")
    if not isinstance(markets, list) or not markets or any(item not in MARKETS for item in markets):
        errors.append("STRATEGY.markets must contain NSE and/or CRYPTO.")
    if not isinstance(timeframes, list) or not timeframes or any(item not in TIMEFRAMES for item in timeframes):
        errors.append("STRATEGY.timeframes contains an unsupported timeframe.")
    if not isinstance(parameters, dict):
        errors.append("STRATEGY.parameters must be a dictionary of JSON-compatible defaults.")
        parameters = {}
    else:
        try:
            json.dumps(parameters, allow_nan=False)
        except (TypeError, ValueError):
            errors.append("STRATEGY.parameters must contain JSON-compatible finite values only.")
    return {
        "strategyId": strategy_id,
        "name": name,
        "version": version,
        "description": str(raw.get("description", "")).strip(),
        "supportedMarkets": list(dict.fromkeys(markets)) if isinstance(markets, list) else [],
        "supportedTimeframes": list(dict.fromkeys(timeframes)) if isinstance(timeframes, list) else [],
        "parameters": parameters,
    }
