from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class ExchangeSettings(BaseModel):
    enabled: bool = True


class ScannerSettings(BaseModel):
    exchanges: Dict[str, ExchangeSettings] = Field(default_factory=dict)
    min_volume_usdt: float = 1_000_000.0
    min_spread_pct: float = 0.5
    scan_interval_sec: int = 1
    proxy_url: Optional[str] = None
    sound_enabled: bool = False
    sound_threshold_pct: float = 1.0

    @field_validator("proxy_url", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("min_volume_usdt")
    @classmethod
    def _vol_non_neg(cls, v: float) -> float:
        return max(0.0, float(v))

    @field_validator("min_spread_pct")
    @classmethod
    def _spread_non_neg(cls, v: float) -> float:
        return max(0.0, float(v))

    @field_validator("scan_interval_sec")
    @classmethod
    def _interval_min(cls, v: int) -> int:
        return max(1, int(v))

    @field_validator("sound_threshold_pct")
    @classmethod
    def _sound_threshold_non_neg(cls, v: float) -> float:
        return max(0.0, float(v))


class TickerAnomaly(BaseModel):
    exchange: str
    symbol: str
    last_price: float
    fair_price: float
    spread_pct: float  # signed: (last - fair) / fair * 100
    volume_24h_usdt: float


class ExchangeStatus(BaseModel):
    name: str
    enabled: bool
    last_scan_at: Optional[float] = None
    last_error: Optional[str] = None
    ticker_count: int = 0
    anomaly_count: int = 0


class ScanResult(BaseModel):
    last_scan_at: Optional[float] = None
    anomalies: List[TickerAnomaly] = Field(default_factory=list)
    exchange_status: List[ExchangeStatus] = Field(default_factory=list)
