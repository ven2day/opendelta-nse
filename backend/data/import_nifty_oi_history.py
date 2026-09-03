from __future__ import annotations

import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path

from backend.markets.nse.oi_history import HistoricalOiImportConfig, build_historical_oi_importer
from backend.collector import DhanConfig
from backend.markets.nse.oi_regime import OiRegimeRepository


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import causal five-minute NIFTY rolling option OI from Dhan")
    parser.add_argument("--from-date", type=date.fromisoformat, default=date.today() - timedelta(days=30))
    parser.add_argument("--to-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--strikes-each-side", type=int, default=5)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--expiry-schedule", type=Path)
    return parser.parse_args()


def _expiry_schedule(path: Path) -> tuple[tuple[date, int], ...]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("Expiry schedule must be a JSON array")
    return tuple(
        (date.fromisoformat(str(item["effectiveFrom"])), int(item["weekday"]))
        for item in value
        if isinstance(item, dict)
    )


def main() -> int:
    arguments = _arguments()
    workspace = Path(__file__).resolve().parents[2]
    if os.name == "nt":
        os.environ.setdefault("DHAN_TOKEN_CACHE_FILE", str(workspace / ".runtime" / "dhan" / "token_cache.json"))
        os.environ.setdefault("NSE_DATA_FILE", str(workspace / ".runtime" / "nse_data.csv"))
    output = (arguments.output or Path(os.environ.get("NIFTY_OI_DIR", workspace / ".runtime" / "nifty-oi"))).resolve()
    cache = (arguments.cache or output / "dhan-cache").resolve()
    schedule_path = arguments.expiry_schedule or (
        Path(os.environ["NIFTY_EXPIRY_SCHEDULE_FILE"])
        if os.environ.get("NIFTY_EXPIRY_SCHEDULE_FILE")
        else None
    )
    if schedule_path is None:
        raise ValueError("--expiry-schedule or NIFTY_EXPIRY_SCHEDULE_FILE is required")
    importer = build_historical_oi_importer(DhanConfig.from_environment(), OiRegimeRepository(output), cache)
    result = importer.run(HistoricalOiImportConfig(
        from_date=arguments.from_date,
        to_date=arguments.to_date,
        strikes_each_side=arguments.strikes_each_side,
        expiry_schedule=_expiry_schedule(schedule_path.resolve()),
    ))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
