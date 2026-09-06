"""Deterministic, schema-aware parameter experiment generation.

This module only resolves and validates immutable backtest inputs.  It cannot
approve strategies, mutate deployments, create signals, or execute trades.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.backtest.engine import EXECUTION_SETTINGS_SCHEMA, ExecutionSettings
from backend.strategies.base import resolve_config

MAX_SWEEP_PARAMETERS = 8
MAX_VALUES_PER_PARAMETER = 20
MAX_GENERATED_VARIANTS = 100
MAX_ESTIMATED_SYMBOL_RUNS = 20_000
LARGE_WORKLOAD_WARNING = 10_000


@dataclass(frozen=True)
class GeneratedVariant:
    name: str
    configuration: dict[str, Any]
    execution: dict[str, Any]
    execution_settings: ExecutionSettings

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "configuration": self.configuration,
            "execution": self.execution,
        }


def canonical_json(value: Any) -> str:
    """Canonical JSON used for duplicate detection and preview hashing."""
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def preview_hash(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def generate_variants(
    *,
    strategy: Any,
    market: str,
    timeframe: str,
    mode: str,
    base_configuration: Mapping[str, Any] | None,
    base_execution: Mapping[str, Any] | None,
    manual_variants: Sequence[Mapping[str, Any]],
    parameters: Sequence[Mapping[str, Any]],
) -> list[GeneratedVariant]:
    """Generate and fully validate every variant before any persistence occurs."""
    if mode == "MANUAL":
        if not manual_variants:
            raise ValueError("Manual experiments require at least one variant")
        if len(manual_variants) > MAX_GENERATED_VARIANTS:
            raise ValueError(f"An experiment may contain at most {MAX_GENERATED_VARIANTS} variants")
        names = [str(item.get("name", "")).strip() for item in manual_variants]
        if any(not name for name in names):
            raise ValueError("Every manual variant requires a name")
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("Variant names must be unique within an experiment")
        generated = [
            _resolved_variant(
                strategy=strategy,
                market=market,
                timeframe=timeframe,
                name=name,
                configuration={**dict(base_configuration or {}), **dict(item.get("configuration") or {})},
                execution={**dict(base_execution or {}), **dict(item.get("execution") or {})},
            )
            for name, item in zip(names, manual_variants, strict=True)
        ]
    elif mode == "GRID":
        generated = _grid_variants(
            strategy=strategy,
            market=market,
            timeframe=timeframe,
            base_configuration=base_configuration,
            base_execution=base_execution,
            parameters=parameters,
        )
    else:
        raise ValueError("mode must be MANUAL or GRID")
    _reject_duplicate_configurations(generated)
    return generated


def _grid_variants(
    *,
    strategy: Any,
    market: str,
    timeframe: str,
    base_configuration: Mapping[str, Any] | None,
    base_execution: Mapping[str, Any] | None,
    parameters: Sequence[Mapping[str, Any]],
) -> list[GeneratedVariant]:
    if not parameters:
        raise ValueError("Grid sweeps require at least one parameter")
    if len(parameters) > MAX_SWEEP_PARAMETERS:
        raise ValueError(f"A grid sweep may contain at most {MAX_SWEEP_PARAMETERS} parameters")
    paths = [(str(item.get("section", "")), str(item.get("parameter", ""))) for item in parameters]
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate parameter paths are not allowed")

    value_sets: list[list[Any]] = []
    for definition in parameters:
        section = str(definition.get("section", ""))
        parameter = str(definition.get("parameter", ""))
        if section == "strategy":
            schema = strategy.config_schema
        elif section == "execution":
            schema = EXECUTION_SETTINGS_SCHEMA
        else:
            raise ValueError(f"Unknown configuration section {section!r}")
        if parameter not in schema:
            raise ValueError(f"Unsupported {section} parameter {parameter!r}")
        value_sets.append(_parameter_values(definition, schema[parameter]))

    variant_count = math.prod(len(values) for values in value_sets)
    if variant_count > MAX_GENERATED_VARIANTS:
        raise ValueError(
            f"The grid generates {variant_count} variants; the limit is {MAX_GENERATED_VARIANTS}"
        )

    generated: list[GeneratedVariant] = []
    for combination in itertools.product(*value_sets):
        strategy_values = dict(base_configuration or {})
        execution_values = dict(base_execution or {})
        labels: list[str] = []
        for definition, value in zip(parameters, combination, strict=True):
            section = str(definition["section"])
            parameter = str(definition["parameter"])
            (strategy_values if section == "strategy" else execution_values)[parameter] = value
            labels.append(f"{parameter}={_format_value(value)}")
        generated.append(
            _resolved_variant(
                strategy=strategy,
                market=market,
                timeframe=timeframe,
                name=_variant_name(labels),
                configuration=strategy_values,
                execution=execution_values,
            )
        )
    return generated


def _parameter_values(definition: Mapping[str, Any], schema: Mapping[str, Any]) -> list[Any]:
    declared_type = str(definition.get("type", ""))
    schema_type = str(schema.get("type", ""))
    expected_type = "enum" if schema.get("enum") is not None else schema_type
    if schema_type not in {"number", "integer", "boolean", "string"}:
        raise ValueError(f"Parameters of schema type {schema_type!r} cannot be swept")
    if declared_type != expected_type:
        raise ValueError(f"Declared type {declared_type!r} does not match schema type {expected_type!r}")

    method = str(definition.get("method", ""))
    if method == "EXPLICIT_VALUES":
        raw_values = definition.get("values")
        if not isinstance(raw_values, list) or not raw_values:
            raise ValueError("Explicit parameter values must be a non-empty list")
        if len(raw_values) > MAX_VALUES_PER_PARAMETER:
            raise ValueError(f"A parameter may contain at most {MAX_VALUES_PER_PARAMETER} values")
        values = [_normalise_value(value, declared_type, schema) for value in raw_values]
    elif method == "FIXED":
        if "value" not in definition or definition.get("value") is None:
            raise ValueError("Fixed parameters require a value")
        values = [_normalise_value(definition["value"], declared_type, schema)]
    elif method == "NUMERIC_RANGE":
        if declared_type not in {"number", "integer"}:
            raise ValueError("Numeric ranges support only number or integer parameters")
        values = _numeric_range(definition, declared_type, schema)
    else:
        raise ValueError(f"Unknown parameter generation method {method!r}")
    return values


def _numeric_range(definition: Mapping[str, Any], declared_type: str, schema: Mapping[str, Any]) -> list[Any]:
    minimum = _decimal(definition.get("minimum"), "minimum")
    maximum = _decimal(definition.get("maximum"), "maximum")
    step = _decimal(definition.get("step"), "step")
    if step <= 0:
        raise ValueError("Numeric range step must be greater than zero")
    if minimum > maximum:
        raise ValueError("Numeric range minimum must not exceed maximum")
    if declared_type == "integer" and any(value != value.to_integral_value() for value in (minimum, maximum, step)):
        raise ValueError("Integer ranges require whole-number minimum, maximum and step")

    values: list[Any] = []
    index = 0
    while True:
        current = minimum + step * index
        if current > maximum:
            break
        values.append(_normalise_value(current, declared_type, schema))
        if len(values) > MAX_VALUES_PER_PARAMETER:
            raise ValueError(f"A parameter may generate at most {MAX_VALUES_PER_PARAMETER} values")
        index += 1
    if not values:
        raise ValueError("Numeric range generated no values")
    return values


def _normalise_value(value: Any, declared_type: str, schema: Mapping[str, Any]) -> Any:
    schema_type = str(schema.get("type", ""))
    value_type = schema_type if declared_type == "enum" else declared_type
    if value_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("Boolean parameters accept only true or false")
        normalised: Any = value
    elif value_type in {"number", "integer"}:
        numeric = _decimal(value, "parameter value")
        if value_type == "integer":
            if numeric != numeric.to_integral_value():
                raise ValueError("Integer parameter values must be whole numbers")
            normalised = int(numeric)
        else:
            normalised = float(numeric)
            if normalised == 0:
                normalised = 0.0
    elif value_type == "string":
        if not isinstance(value, str):
            raise ValueError("Enum parameter values must be strings")
        normalised = value
    else:
        raise ValueError(f"Unsupported declared parameter type {declared_type!r}")

    if "minimum" in schema and Decimal(str(normalised)) < Decimal(str(schema["minimum"])):
        raise ValueError(f"Parameter value must be at least {schema['minimum']}")
    if "maximum" in schema and Decimal(str(normalised)) > Decimal(str(schema["maximum"])):
        raise ValueError(f"Parameter value must be at most {schema['maximum']}")
    if schema.get("enum") is not None and canonical_json(normalised) not in {
        canonical_json(candidate) for candidate in schema["enum"]
    }:
        raise ValueError("Parameter value is not in the published enum")
    return normalised


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"Numeric range {label} must be a finite number")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"Numeric range {label} must be a finite number") from error
    if not result.is_finite():
        raise ValueError(f"Numeric range {label} must be a finite number")
    return result


def _resolved_variant(
    *,
    strategy: Any,
    market: str,
    timeframe: str,
    name: str,
    configuration: Mapping[str, Any],
    execution: Mapping[str, Any],
) -> GeneratedVariant:
    resolved = (
        strategy.resolve(configuration)
        if callable(getattr(strategy, "resolve", None))
        else resolve_config(strategy.config_schema, configuration)
    )
    strategy.validate_config(resolved)
    canonical_json(resolved)
    execution_settings = ExecutionSettings.from_mapping(execution, whole_units=market == "NSE")
    execution_snapshot = execution_settings.public()
    execution_snapshot["executionTimeframe"] = "5m" if market == "NSE" and timeframe == "1d" else timeframe
    canonical_json(execution_snapshot)
    return GeneratedVariant(
        name=name,
        configuration=dict(resolved),
        execution=execution_snapshot,
        execution_settings=execution_settings,
    )


def _reject_duplicate_configurations(variants: Sequence[GeneratedVariant]) -> None:
    names = [item.name for item in variants]
    if len(names) != len(set(names)):
        raise ValueError("The experiment generates duplicate variant names")
    keys = [canonical_json({"configuration": item.configuration, "execution": item.execution}) for item in variants]
    if len(keys) != len(set(keys)):
        raise ValueError("The experiment generates duplicate resolved configurations")


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        decimal = Decimal(str(value)).normalize()
        return format(decimal, "f")
    return str(value)


def _variant_name(labels: Sequence[str]) -> str:
    full = " · ".join(labels)
    if len(full) <= 80:
        return full
    suffix = hashlib.sha256(full.encode("utf-8")).hexdigest()[:8]
    return f"{full[:68].rstrip()} · {suffix}"
