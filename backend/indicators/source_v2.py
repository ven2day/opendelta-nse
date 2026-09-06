"""Static validation for user-authored Indicator V2 Python source.

Submitted source is parsed but never imported or executed by the API process.
Execution is delegated to the same resource-limited, networkless worker used by
Strategy V2.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from backend.strategies.source_v2 import (
    ALLOWED_IMPORT_ROOTS,
    FORBIDDEN_ATTRIBUTES,
    FORBIDDEN_CALLS,
    FORBIDDEN_ROOTS,
    MAX_SOURCE_BYTES,
    SEMVER,
)

INDICATOR_ID = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
OUTPUT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
DISPLAY_TYPES = frozenset({"LINE", "HISTOGRAM", "BAND", "POINTS"})
PANES = frozenset({"OVERLAY", "PANEL"})


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
    return '''"""OpenDelta Indicator V2 example: simple moving average."""

INDICATOR = {
    "id": "simple_moving_average",
    "name": "Simple Moving Average",
    "version": "1.0.0",
    "description": "A configurable moving average displayed over candles.",
    "parameters": {"length": 20},
    "requiredHistory": 20,
    "outputs": [
        {"name": "sma", "label": "SMA", "display": "LINE", "pane": "OVERLAY"}
    ],
}


def calculate(context, data):
    """Return one value per declared output for the current completed candle."""
    length = context.params["length"]
    close = data.candles["close"]
    if len(close) < length:
        return {"sma": None}
    return {"sma": float(close.iloc[-length:].mean())}
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
        tree = ast.parse(source, filename="indicator_v2.py", mode="exec")
    except SyntaxError as error:
        location = f"line {error.lineno}" if error.lineno else "unknown line"
        return SourceValidation(False, (f"Python syntax error at {location}: {error.msg}.",), (), None, digest)

    metadata_node: ast.AST | None = None
    calculate: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "calculate":
            calculate = node
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "INDICATOR" for target in node.targets
        ):
            metadata_node = node.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "INDICATOR":
            metadata_node = node.value

    if calculate is None:
        errors.append("Missing required function: calculate.")
    else:
        if isinstance(calculate, ast.AsyncFunctionDef):
            errors.append("calculate must be synchronous.")
        if len(calculate.args.args) != 2:
            errors.append("Use the exact signature calculate(context, data).")

    manifest: dict[str, Any] | None = None
    if metadata_node is None:
        errors.append("Define INDICATOR as a literal metadata dictionary.")
    else:
        try:
            raw = ast.literal_eval(metadata_node)
        except (ValueError, TypeError):
            errors.append("INDICATOR must contain literals only; computed metadata is not allowed.")
        else:
            if not isinstance(raw, dict):
                errors.append("INDICATOR must be a dictionary.")
            else:
                manifest = _validate_manifest(raw, errors)

    for walked in ast.walk(tree):
        if isinstance(walked, (ast.Import, ast.ImportFrom)):
            modules = [alias.name for alias in walked.names] if isinstance(walked, ast.Import) else [walked.module or ""]
            for module in modules:
                if module.split(".", 1)[0] not in ALLOWED_IMPORT_ROOTS:
                    errors.append(f"Import '{module}' is not allowed.")
        elif isinstance(walked, ast.Call):
            name = _call_name(walked.func)
            if name in FORBIDDEN_CALLS or name.split(".", 1)[0] in FORBIDDEN_ROOTS:
                errors.append(f"Call '{name}' is not allowed.")
        elif isinstance(walked, ast.Attribute) and walked.attr.startswith("__"):
            errors.append("Dunder attribute access is not allowed.")
        elif isinstance(walked, ast.Attribute) and walked.attr in FORBIDDEN_ATTRIBUTES:
            errors.append(f"Data/file method '{walked.attr}' is not allowed in indicator source.")

    if "shift(-" in source.replace(" ", ""):
        errors.append("Negative shift is not allowed because it can introduce look-ahead bias.")
    if manifest and not ast.get_docstring(tree):
        warnings.append("Add a module docstring describing the indicator assumptions.")
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
    required = {"id", "name", "version", "parameters", "outputs"}
    missing = sorted(required - raw.keys())
    if missing:
        errors.append("INDICATOR is missing: " + ", ".join(missing) + ".")
    indicator_id = str(raw.get("id", ""))
    name = str(raw.get("name", "")).strip()
    version = str(raw.get("version", ""))
    parameters = raw.get("parameters", {})
    outputs = raw.get("outputs", [])
    required_history = raw.get("requiredHistory", 1)
    if not INDICATOR_ID.fullmatch(indicator_id):
        errors.append("INDICATOR.id must be 3-64 lowercase letters, numbers or underscores, starting with a letter.")
    if not name or len(name) > 120:
        errors.append("INDICATOR.name must be between 1 and 120 characters.")
    if not SEMVER.fullmatch(version):
        errors.append("INDICATOR.version must use semantic versioning such as 1.0.0.")
    if not isinstance(parameters, dict):
        errors.append("INDICATOR.parameters must be a dictionary of JSON-compatible defaults.")
        parameters = {}
    else:
        try:
            json.dumps(parameters, allow_nan=False)
        except (TypeError, ValueError):
            errors.append("INDICATOR.parameters must contain JSON-compatible finite values only.")
        if any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, (str, bool, int, float))
            for key, value in parameters.items()
        ):
            errors.append("INDICATOR.parameters supports named string, boolean, or number defaults only.")
    normalised_outputs: list[dict[str, str]] = []
    seen: set[str] = set()
    if not isinstance(outputs, list) or not outputs:
        errors.append("INDICATOR.outputs must declare at least one output.")
    else:
        for output in outputs:
            if not isinstance(output, dict):
                errors.append("Every INDICATOR output must be a dictionary.")
                continue
            output_name = str(output.get("name", ""))
            label = str(output.get("label", "")).strip()
            display = str(output.get("display", "LINE")).upper()
            pane = str(output.get("pane", "PANEL")).upper()
            if not OUTPUT_NAME.fullmatch(output_name) or output_name in seen:
                errors.append("INDICATOR output names must be unique lowercase identifiers.")
            if not label or len(label) > 80:
                errors.append("INDICATOR output labels must be between 1 and 80 characters.")
            if display not in DISPLAY_TYPES:
                errors.append("INDICATOR output display must be LINE, HISTOGRAM, BAND or POINTS.")
            if pane not in PANES:
                errors.append("INDICATOR output pane must be OVERLAY or PANEL.")
            seen.add(output_name)
            normalised_outputs.append({"name": output_name, "label": label, "display": display, "pane": pane})
    if isinstance(required_history, bool) or not isinstance(required_history, int) or not 1 <= required_history <= 5_000:
        errors.append("INDICATOR.requiredHistory must be a whole number between 1 and 5000.")
        required_history = 1
    return {
        "indicatorId": indicator_id,
        "name": name,
        "version": version,
        "description": str(raw.get("description", "")).strip(),
        "parameters": parameters,
        "requiredHistory": required_history,
        "outputs": normalised_outputs,
    }
