from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Literal


Market = Literal["NSE", "CRYPTO"]


@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    market: Market
    symbol: str
    exchange: str
    provider: str
    provider_symbol: str
    market_type: str
    active: bool
    trading_status: str
    company_name: str | None = None
    sector: str | None = None
    industry: str | None = None
    tick_size: float | None = None
    lot_size: float | None = None
    price_band: str | None = None
    benchmark: str | None = None

    def public(self) -> dict[str, Any]:
        return asdict(self)


class InstrumentRepository:
    def __init__(
        self,
        symbols_file: Path,
        crypto_loader: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.symbols_file = symbols_file
        self.crypto_loader = crypto_loader

    def nse(self) -> list[Instrument]:
        rows: list[Instrument] = []
        with self.symbols_file.open(newline="", encoding="utf-8-sig") as handle:
            for raw in csv.DictReader(handle):
                symbol = str(raw.get("symbol") or raw.get("SEM_TRADING_SYMBOL") or "").strip().upper()
                if not symbol:
                    continue
                company = str(raw.get("company_name") or raw.get("SEM_CUSTOM_SYMBOL") or "").strip() or None
                rows.append(
                    Instrument(
                        instrument_id=f"NSE:{symbol}",
                        market="NSE",
                        symbol=symbol,
                        exchange="NSE",
                        provider="DHAN",
                        provider_symbol=symbol,
                        market_type="EQUITY",
                        active=True,
                        trading_status="ACTIVE",
                        company_name=company,
                        benchmark="NIFTY50",
                    )
                )
        return sorted(rows, key=lambda item: item.symbol)

    def crypto(self) -> list[Instrument]:
        if self.crypto_loader is None:
            return []
        rows = []
        for raw in self.crypto_loader():
            provider = str(raw.get("provider", "")).upper()
            provider_symbol = str(raw.get("providerSymbol", "")).upper()
            if not provider or not provider_symbol:
                continue
            rows.append(
                Instrument(
                    instrument_id=str(raw.get("instrumentId") or f"{provider}:{provider_symbol}"),
                    market="CRYPTO",
                    symbol=str(raw.get("displaySymbol") or provider_symbol),
                    exchange=provider,
                    provider=provider,
                    provider_symbol=provider_symbol,
                    market_type=str(raw.get("instrumentType") or "SPOT"),
                    active=bool(raw.get("active", True)),
                    trading_status="ACTIVE" if raw.get("active", True) else "INACTIVE",
                    tick_size=float(raw["tickSize"]) if raw.get("tickSize") is not None else None,
                    lot_size=float(raw["lotSize"]) if raw.get("lotSize") is not None else None,
                )
            )
        return sorted(rows, key=lambda item: (item.provider, item.provider_symbol))

    def list(self, market: Market | None = None) -> list[Instrument]:
        if market == "NSE":
            return self.nse()
        if market == "CRYPTO":
            return self.crypto()
        return [*self.nse(), *self.crypto()]


class InstrumentService:
    def __init__(self, repository: InstrumentRepository) -> None:
        self.repository = repository

    def list(self, market: Market | None, offset: int, limit: int) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("Instrument pagination is invalid")
        rows = self.repository.list(market)
        return {
            "rows": [item.public() for item in rows[offset : offset + limit]],
            "count": len(rows),
            "offset": offset,
            "limit": limit,
            "market": market or "ALL",
        }
