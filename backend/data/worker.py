from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from typing import Sequence

from backend.markets.crypto.providers import OkxPublicProvider
from backend.collector import DhanClient, DhanConfig, download_instrument_master, historical_payload_to_frame
from backend.markets.common import MarketCandle, MarketInstrument
from backend.data.timescale import (
    BackfillWorker,
    CandleProvider,
    CanonicalCandle,
    TimescaleMarketDataStore,
    canonical_candles_from_dhan_frame,
    utc,
)


DHAN_INTERVALS = {"1m": "1", "5m": "5", "15m": "15", "1h": "60"}


class DhanCanonicalProvider:
    def __init__(
        self,
        client: DhanClient,
        symbols_by_security_id: dict[str, str] | None = None,
    ) -> None:
        self.client = client
        self.symbols_by_security_id = symbols_by_security_id

    @classmethod
    def from_environment(cls) -> "DhanCanonicalProvider":
        config = DhanConfig.from_environment()
        return cls(DhanClient(config))

    def _symbol(self, instrument_id: str) -> str:
        if self.symbols_by_security_id is None:
            instruments = download_instrument_master(self.client.config.instrument_master_url)
            self.symbols_by_security_id = {
                str(row["SEM_SMST_SECURITY_ID"]): str(row["symbol"])
                for _, row in instruments.iterrows()
            }
        try:
            return self.symbols_by_security_id[str(instrument_id)]
        except KeyError as error:
            raise ValueError(
                f"Dhan security ID is not in the current instrument master: {instrument_id}"
            ) from error

    def candles(
        self,
        instrument_id: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> Sequence[CanonicalCandle]:
        symbol = self._symbol(instrument_id)
        lower, upper = utc(start), utc(end)
        interval = DHAN_INTERVALS.get(timeframe)
        if interval is None:
            raise ValueError(f"Dhan canonical backfill does not support {timeframe}")
        payload = self.client.historical_intraday(
            str(instrument_id), interval, lower, upper
        )
        frame = historical_payload_to_frame(payload)
        return canonical_candles_from_dhan_frame(
            frame,
            instrument_id=str(instrument_id),
            symbol=symbol,
            timeframe=timeframe,
            completed_before=upper,
        )


def canonical_crypto_candles(
    instrument: MarketInstrument,
    candles: Sequence[MarketCandle],
) -> list[CanonicalCandle]:
    if instrument.market != "CRYPTO":
        raise ValueError("Only crypto instruments belong in the CRYPTO canonical market")
    return [
        CanonicalCandle(
            market="CRYPTO",
            provider=item.provider,
            instrument_id=instrument.instrument_id,
            symbol=instrument.display_symbol,
            timeframe=item.timeframe,
            open_time=item.open_time,
            close_time=item.close_time,
            open=item.open,
            high=item.high,
            low=item.low,
            close=item.close,
            volume=item.base_volume,
            quote_volume=item.quote_volume,
            complete=item.complete,
        )
        for item in candles
        if item.complete
    ]


class OkxCanonicalProvider:
    def __init__(self, provider: OkxPublicProvider) -> None:
        self.provider = provider
        self._instruments: dict[str, MarketInstrument] | None = None

    @classmethod
    def from_environment(cls) -> "OkxCanonicalProvider":
        return cls(OkxPublicProvider(base_url=os.environ.get("OKX_PUBLIC_API_URL", "https://www.okx.com")))

    def _instrument(self, instrument_id: str) -> MarketInstrument:
        if self._instruments is None:
            self._instruments = {
                item.instrument_id: item
                for item in self.provider.instruments()
                if item.market == "CRYPTO"
            }
        try:
            return self._instruments[instrument_id]
        except KeyError as error:
            raise ValueError(f"OKX instrument is unavailable: {instrument_id}") from error

    def candles(
        self,
        instrument_id: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> Sequence[CanonicalCandle]:
        instrument = self._instrument(instrument_id)
        return canonical_crypto_candles(
            instrument,
            self.provider.candles(instrument, timeframe, utc(start), utc(end)),
        )


def providers_from_environment(names: Sequence[str]) -> dict[str, CandleProvider]:
    requested = {name.strip().upper() for name in names if name.strip()}
    unknown = requested.difference({"DHAN", "OKX"})
    if unknown:
        raise ValueError("Unsupported market-data providers: " + ", ".join(sorted(unknown)))
    providers: dict[str, CandleProvider] = {}
    if "DHAN" in requested:
        providers["DHAN"] = DhanCanonicalProvider.from_environment()
    if "OKX" in requested:
        providers["OKX"] = OkxCanonicalProvider.from_environment()
    return providers


def database_url() -> str:
    value = os.environ.get("MARKET_DATA_DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("MARKET_DATA_DATABASE_URL is required")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Run resumable OpenDelta market-data jobs")
    parser.add_argument(
        "--providers",
        default=os.environ.get("MARKET_DATA_WORKER_PROVIDERS", "DHAN,OKX"),
        help="Comma-separated providers; supported values are DHAN and OKX",
    )
    parser.add_argument(
        "--maximum-chunks",
        type=int,
        default=int(os.environ.get("MARKET_DATA_WORKER_MAXIMUM_CHUNKS", "100")),
    )
    args = parser.parse_args()
    store = TimescaleMarketDataStore(database_url())
    store.open()
    try:
        worker = BackfillWorker(
            store,
            providers_from_environment(args.providers.split(",")),
            worker_id=os.environ.get("MARKET_DATA_WORKER_ID") or None,
        )
        results = worker.run_pending(maximum_chunks=args.maximum_chunks)
        print(json.dumps({"processedChunks": len(results), "results": results}, separators=(",", ":")))
    finally:
        store.close()


if __name__ == "__main__":
    main()
