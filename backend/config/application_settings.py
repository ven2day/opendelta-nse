from __future__ import annotations

import csv
import math
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = 1
DEFAULT_MINIMUM_PRICE = 0.0
DEFAULT_MAXIMUM_PRICE = 10_000_000.0


@dataclass(frozen=True)
class GlobalPriceSettings:
    minimum_price: float = DEFAULT_MINIMUM_PRICE
    maximum_price: float = DEFAULT_MAXIMUM_PRICE
    updated_at: str | None = None

    def validate(self) -> "GlobalPriceSettings":
        if not math.isfinite(self.minimum_price) or not math.isfinite(self.maximum_price):
            raise ValueError("Prices must be finite numbers")
        if self.minimum_price < 0:
            raise ValueError("Minimum price must be at least 0")
        if self.maximum_price > DEFAULT_MAXIMUM_PRICE:
            raise ValueError(f"Maximum price must not exceed {DEFAULT_MAXIMUM_PRICE:g}")
        if self.minimum_price >= self.maximum_price:
            raise ValueError("Minimum price must be less than maximum price")
        return self

    def contains(self, price: float | int | None) -> bool:
        if price is None:
            return False
        try:
            numeric = float(price)
        except (TypeError, ValueError):
            return False
        return math.isfinite(numeric) and self.minimum_price <= numeric <= self.maximum_price

    def public(self) -> dict[str, object]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "priceRange": {
                "minimumPrice": self.minimum_price,
                "maximumPrice": self.maximum_price,
            },
            "updatedAt": self.updated_at,
        }


class ApplicationSettingsRepository:
    """Persist application-wide presentation settings using the existing SQLite stack."""

    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "application-settings.sqlite3"
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS global_settings (
                    settings_key TEXT PRIMARY KEY,
                    minimum_price REAL NOT NULL,
                    maximum_price REAL NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def get(self) -> GlobalPriceSettings:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT minimum_price, maximum_price, updated_at FROM global_settings WHERE settings_key = ?",
                ("global",),
            ).fetchone()
        if row is None:
            return GlobalPriceSettings()
        return GlobalPriceSettings(
            minimum_price=float(row["minimum_price"]),
            maximum_price=float(row["maximum_price"]),
            updated_at=str(row["updated_at"]),
        ).validate()

    def update(self, minimum_price: float, maximum_price: float) -> GlobalPriceSettings:
        updated_at = datetime.now(timezone.utc).isoformat()
        settings = GlobalPriceSettings(
            minimum_price=float(minimum_price),
            maximum_price=float(maximum_price),
            updated_at=updated_at,
        ).validate()
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO global_settings(settings_key, minimum_price, maximum_price, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(settings_key) DO UPDATE SET
                    minimum_price = excluded.minimum_price,
                    maximum_price = excluded.maximum_price,
                    updated_at = excluded.updated_at
                """,
                ("global", settings.minimum_price, settings.maximum_price, updated_at),
            )
            connection.commit()
        return settings


def prices_by_symbol(csv_path: Path) -> dict[str, float]:
    if not csv_path.is_file():
        return {}
    result: dict[str, float] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            symbol = str(row.get("symbol", "")).strip().upper().removesuffix(".NS")
            try:
                price = float(row.get("entry_price", ""))
            except (TypeError, ValueError):
                continue
            if symbol and math.isfinite(price):
                result[symbol] = price
    return result


def filter_symbols_by_price(
    symbols: Iterable[str], prices: dict[str, float], settings: GlobalPriceSettings
) -> tuple[list[str], int]:
    filtered: list[str] = []
    missing = 0
    for raw_symbol in symbols:
        symbol = raw_symbol.strip().upper().removesuffix(".NS")
        price = prices.get(symbol)
        if price is None:
            missing += 1
        elif settings.contains(price):
            filtered.append(symbol)
    return filtered, missing
