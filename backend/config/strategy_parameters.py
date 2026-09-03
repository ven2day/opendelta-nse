from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from backend.paths import data_file

StrategyKey = Literal[
    "rsi_range",
    "rsi_recovery",
]
PARAMETER_FILE = data_file("strategy-parameters.json")


@lru_cache(maxsize=4)
def parameter_definitions(strategy: StrategyKey | None = None) -> tuple[dict[str, Any], ...]:
    payload = json.loads(PARAMETER_FILE.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("strategy-parameters.json must contain a list")
    return tuple(
        dict(item)
        for item in payload
        if isinstance(item, dict) and (strategy is None or item.get("strategy") == strategy)
    )


def parameter_definition(strategy: StrategyKey, key: str) -> dict[str, Any]:
    for item in parameter_definitions(strategy):
        if item.get("key") == key:
            return dict(item)
    raise KeyError(f"No parameter definition for {strategy}.{key}")


def numeric_field_kwargs(strategy: StrategyKey, key: str) -> dict[str, Any]:
    definition = parameter_definition(strategy, key)
    if definition.get("type") not in {"number", "integer"}:
        raise TypeError(f"{strategy}.{key} is not numeric")
    result: dict[str, Any] = {"default": definition["default"]}
    if definition.get("minimum") is not None:
        result["ge"] = definition["minimum"]
    if definition.get("maximum") is not None:
        result["le"] = definition["maximum"]
    return result
