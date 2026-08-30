from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Literal


PLATFORM_VERSION = "1.0.0"
UNSUPPORTED_DATA_REQUIREMENT = "UNSUPPORTED_DATA_REQUIREMENT"


def canonical_json(value: Any) -> str:
    if is_dataclass(value):
        value = asdict(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def stable_id(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:20]}"


def utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class PlatformSettings:
    data_root: Path
    database_path: Path
    market_data_file: Path
    symbols_file: Path
    maximum_workers: int = 2
    maximum_pending_jobs: int = 20
    job_retry_limit: int = 2
    data_stale_seconds: int = 86_400
    environment: Literal["production", "development", "test"] = "production"

    @classmethod
    def from_environment(cls) -> "PlatformSettings":
        default_data_root = "/var/lib/vento-nse/backtest/platform"
        default_market_data = "/var/lib/vento-nse/data/nse_symbols_rsi_volume.csv"
        if os.name == "nt":
            default_data_root = str(Path(tempfile.gettempdir()) / "opendelta-platform")
            default_market_data = str(Path.cwd() / "nse_symbols_rsi_volume.csv")
        backtest_root = os.environ.get("BACKTEST_CACHE_DIR")
        if backtest_root:
            default_data_root = str(Path(backtest_root).expanduser() / "platform")
        data_root = Path(
            os.environ.get("PLATFORM_DATA_DIR", default_data_root)
        ).expanduser()
        if not data_root.is_absolute():
            raise RuntimeError("PLATFORM_DATA_DIR must be an absolute path")
        database_path = Path(
            os.environ.get("PLATFORM_DATABASE_PATH", str(data_root / "platform.sqlite3"))
        ).expanduser()
        market_data_file = Path(
            os.environ.get(
                "LIVE_MARKET_DATA_FILE",
                default_market_data,
            )
        ).expanduser()
        symbols_file = Path(os.environ.get("SYMBOLS_FILE", "symbols.csv")).expanduser()
        if not database_path.is_absolute() or not market_data_file.is_absolute():
            raise RuntimeError("Platform database and market-data paths must be absolute")
        maximum_workers = int(os.environ.get("PLATFORM_MAXIMUM_WORKERS", "2"))
        maximum_pending_jobs = int(os.environ.get("PLATFORM_MAXIMUM_PENDING_JOBS", "20"))
        retry_limit = int(os.environ.get("PLATFORM_JOB_RETRY_LIMIT", "2"))
        if not 1 <= maximum_workers <= 8:
            raise RuntimeError("PLATFORM_MAXIMUM_WORKERS must be between 1 and 8")
        if not 1 <= maximum_pending_jobs <= 100:
            raise RuntimeError("PLATFORM_MAXIMUM_PENDING_JOBS must be between 1 and 100")
        if not 0 <= retry_limit <= 3:
            raise RuntimeError("PLATFORM_JOB_RETRY_LIMIT must be between 0 and 3")
        environment = os.environ.get("APP_ENVIRONMENT", "production").strip().lower()
        if environment not in {"production", "development", "test"}:
            raise RuntimeError("APP_ENVIRONMENT is invalid")
        return cls(
            data_root=data_root,
            database_path=database_path,
            market_data_file=market_data_file,
            symbols_file=symbols_file,
            maximum_workers=maximum_workers,
            maximum_pending_jobs=maximum_pending_jobs,
            job_retry_limit=retry_limit,
            data_stale_seconds=int(os.environ.get("PLATFORM_DATA_STALE_SECONDS", "86400")),
            environment=environment,  # type: ignore[arg-type]
        )


class StructuredLogger:
    """JSON logger that deliberately accepts metadata, never credentials."""

    _blocked = {"password", "passwd", "secret", "token", "cookie", "authorization"}

    def __init__(self, name: str = "opendelta.platform") -> None:
        self.logger = logging.getLogger(name)

    def event(self, event: str, **metadata: Any) -> None:
        safe = {
            key: value
            for key, value in metadata.items()
            if not any(blocked in key.casefold() for blocked in self._blocked)
        }
        self.logger.info(canonical_json({"event": event, "at": utc_now_iso(), **safe}))


class MetricsRegistry:
    def __init__(self) -> None:
        self.started_at = time.monotonic()
        self.counters: dict[str, int] = {}
        self.durations: dict[str, list[float]] = {}

    def increment(self, key: str, amount: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + amount

    def observe(self, key: str, seconds: float) -> None:
        values = self.durations.setdefault(key, [])
        values.append(max(0.0, float(seconds)))
        if len(values) > 1_000:
            del values[:-1_000]

    def snapshot(self) -> dict[str, Any]:
        summaries = {}
        for key, values in self.durations.items():
            ordered = sorted(values)
            summaries[key] = {
                "count": len(values),
                "averageSeconds": round(sum(values) / len(values), 6),
                "p95Seconds": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 6),
            }
        return {
            "uptimeSeconds": round(time.monotonic() - self.started_at, 3),
            "counters": dict(self.counters),
            "durations": summaries,
        }


def request_id(value: str | None = None) -> str:
    candidate = (value or "").strip()
    if candidate and len(candidate) <= 100 and all(ch.isalnum() or ch in "-_." for ch in candidate):
        return candidate
    return f"req-{uuid.uuid4()}"
