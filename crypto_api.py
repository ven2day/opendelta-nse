from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from crypto_engine import CryptoMarketService
from crypto_providers import MarketProviderError
from crypto_strategy import CryptoPullbackConfig


class AddCryptoInstrumentRequest(BaseModel):
    provider: Literal["OKX", "VALR"]
    providerSymbol: str = Field(min_length=1, max_length=80)


class CryptoStrategyConfigurationRequest(BaseModel):
    rsiLength: int = Field(default=14, ge=2, le=500)
    buyArmLow: float = Field(default=40, ge=0, le=100)
    buyArmHigh: float = Field(default=50, ge=0, le=100)
    sellArmLow: float = Field(default=50, ge=0, le=100)
    sellArmHigh: float = Field(default=60, ge=0, le=100)
    recoveryLevel: float = Field(default=50, ge=0, le=100)
    emaFast: int = Field(default=20, ge=1, le=1_000)
    emaSlow: int = Field(default=50, ge=2, le=1_000)
    atrLength: int = Field(default=14, ge=2, le=500)
    volumePeriod: int = Field(default=20, ge=2, le=500)
    minimumRvol: float = Field(default=1.2, gt=0, le=100)
    setupExpiryBars: int = Field(default=6, ge=1, le=1_000)
    stopAtrMultiplier: float = Field(default=1.0, gt=0, le=100)
    rewardRiskRatio: float = Field(default=1.5, gt=0, le=100)
    maximumHoldingBars: int = Field(default=6, ge=1, le=10_000)
    side: Literal["BOTH", "BUY", "SELL"] = "BOTH"
    makerTakerCostBps: float = Field(default=8, ge=0, le=10_000)
    slippageBps: float = Field(default=2, ge=0, le=10_000)

    @model_validator(mode="after")
    def validate_relationships(self) -> CryptoStrategyConfigurationRequest:
        self.config().validate()
        return self

    def config(self) -> CryptoPullbackConfig:
        return CryptoPullbackConfig(
            rsi_length=self.rsiLength,
            buy_arm_low=self.buyArmLow,
            buy_arm_high=self.buyArmHigh,
            sell_arm_low=self.sellArmLow,
            sell_arm_high=self.sellArmHigh,
            recovery_level=self.recoveryLevel,
            ema_fast=self.emaFast,
            ema_slow=self.emaSlow,
            atr_length=self.atrLength,
            volume_period=self.volumePeriod,
            minimum_rvol=self.minimumRvol,
            setup_expiry_bars=self.setupExpiryBars,
            stop_atr_multiplier=self.stopAtrMultiplier,
            reward_risk_ratio=self.rewardRiskRatio,
            maximum_holding_bars=self.maximumHoldingBars,
            side=self.side,
            maker_taker_cost_bps=self.makerTakerCostBps,
            slippage_bps=self.slippageBps,
        )


class CryptoBacktestRequest(BaseModel):
    instrumentId: str = Field(min_length=5, max_length=80)
    timeframe: Literal["1m", "5m", "15m", "30m", "1h", "6h", "1d"] = "5m"
    durationDays: int = Field(default=30, ge=1, le=730)
    configuration: CryptoStrategyConfigurationRequest = Field(default_factory=CryptoStrategyConfigurationRequest)


def create_crypto_router(service_factory: Callable[[], CryptoMarketService]) -> APIRouter:
    router = APIRouter(prefix="/crypto", tags=["crypto-market"])

    @router.get("/providers")
    def providers() -> dict[str, Any]:
        service = service_factory()
        return {
            "providers": service.provider_names(),
            "paperOnly": True,
            "liveOrdersEnabled": False,
        }

    @router.get("/catalog")
    def catalog(
        provider: Literal["OKX", "VALR"],
        query: str = Query(default="", max_length=80),
        instrument_type: Literal["SPOT", "PERPETUAL"] | None = Query(default=None, alias="instrumentType"),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        try:
            rows = service_factory().search_catalog(provider, query, instrument_type, limit)
        except MarketProviderError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"provider": provider, "rows": rows, "count": len(rows)}

    @router.get("/instruments")
    def instruments() -> dict[str, Any]:
        rows = [item.public() for item in service_factory().list_instruments()]
        return {"rows": rows, "count": len(rows)}

    @router.post("/instruments")
    def add_instrument(request: AddCryptoInstrumentRequest) -> dict[str, Any]:
        try:
            instrument = service_factory().add_instrument(request.provider, request.providerSymbol)
        except MarketProviderError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"instrument": instrument.public()}

    @router.delete("/instruments/{instrument_id}")
    def remove_instrument(instrument_id: str) -> dict[str, Any]:
        if not service_factory().remove_instrument(instrument_id):
            raise HTTPException(status_code=404, detail="Instrument was not found")
        return {"removed": True, "instrumentId": instrument_id}

    @router.get("/signals/status")
    def signal_status() -> dict[str, Any]:
        return service_factory().status()

    @router.get("/signals")
    def signals(limit: int = Query(default=200, ge=1, le=2_000)) -> dict[str, Any]:
        rows = service_factory().signals(limit)
        return {"rows": rows, "count": len(rows)}

    @router.post("/signals/scan")
    def scan_signals(
        timeframe: Literal["1m", "5m", "15m", "30m", "1h", "6h", "1d"] = "5m",
    ) -> dict[str, Any]:
        try:
            return service_factory().scan(timeframe)
        except MarketProviderError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/backtest")
    def backtest(request: CryptoBacktestRequest) -> dict[str, Any]:
        try:
            return service_factory().backtest(
                request.instrumentId,
                request.timeframe,
                request.durationDays,
                request.configuration.config(),
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Instrument was not found") from error
        except MarketProviderError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return router
