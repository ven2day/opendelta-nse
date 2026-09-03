from __future__ import annotations

import csv
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pandas as pd

from backend.collector import instrument_company_name, load_symbols, normalize_symbol

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows development fallback.
    fcntl = None


class SymbolAlreadyExistsError(ValueError):
    """Raised when a symbol is already present in the market-data universe."""


class SymbolNotFoundError(ValueError):
    """Raised when Dhan's instrument master has no matching NSE equity."""


@dataclass(frozen=True)
class SymbolAddition:
    symbol: str
    company_name: str
    symbol_count: int


_process_lock = threading.Lock()


@contextmanager
def _registry_lock(target: Path) -> Iterator[None]:
    lock_path = target.with_name(f".{target.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _process_lock, lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _write_symbols_atomically(symbols: list[str], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["symbol"])
            writer.writerows([symbol] for symbol in symbols)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, target)
    finally:
        temporary_path.unlink(missing_ok=True)


class MarketSymbolRegistry:
    """Persistent, atomically updated NSE equity symbol registry."""

    def __init__(self, target: Path, seed: Path) -> None:
        self.target = target.expanduser()
        self.seed = seed.expanduser()
        if not self.target.is_absolute():
            raise ValueError("Market symbol registry path must be absolute")

    def _ensure_seeded_unlocked(self) -> None:
        if self.target.is_file():
            return
        if not self.seed.is_file():
            raise FileNotFoundError("The default NSE symbol list is unavailable")
        _write_symbols_atomically(load_symbols(self.seed), self.target)

    def symbols(self) -> list[str]:
        with _registry_lock(self.target):
            self._ensure_seeded_unlocked()
            return load_symbols(self.target)

    def add(self, requested_symbol: str, instruments: pd.DataFrame) -> SymbolAddition:
        symbol = normalize_symbol(requested_symbol)
        if not symbol:
            raise SymbolNotFoundError("Enter an NSE equity symbol")

        matches = instruments[instruments["symbol"] == symbol]
        if matches.empty:
            raise SymbolNotFoundError(
                f"{symbol} is not an active NSE equity in Dhan's instrument master"
            )
        instrument = matches.iloc[0]
        company_name = instrument_company_name(instrument) or symbol

        with _registry_lock(self.target):
            self._ensure_seeded_unlocked()
            symbols = load_symbols(self.target)
            if symbol in symbols:
                raise SymbolAlreadyExistsError(f"{symbol} is already in the symbol list")
            symbols.append(symbol)
            _write_symbols_atomically(symbols, self.target)

        return SymbolAddition(
            symbol=symbol,
            company_name=company_name,
            symbol_count=len(symbols),
        )
