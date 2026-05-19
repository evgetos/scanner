from __future__ import annotations

from pydantic import BaseModel, Field


class ScanSettings(BaseModel):
    min_volume_spot: float = Field(default=100_000)
    min_volume_futures: float = Field(default=100_000)
    max_distance_pct: float = Field(default=5.0)
    min_density_usd: float = Field(default=50_000)
    enabled_exchanges: list[str] = Field(
        default_factory=lambda: [
            "gate", "bybit", "mexc", "hyperliquid",
            "kucoin", "okx", "bitget",
        ]
    )
    market_types: list[str] = Field(default_factory=lambda: ["spot", "futures"])
    favorites: list[str] = Field(default_factory=list)
    max_symbols_per_exchange: int = Field(default=50)
    auto_scan: bool = Field(default=False)
    scan_interval: int = Field(default=30)


class DensityItem(BaseModel):
    exchange: str
    exchange_id: str
    symbol: str
    market_type: str
    side: str
    price: float
    volume_usd: float
    amount: float
    distance_pct: float
    volume_ratio: float
    volume_24h_usd: float
    age_seconds: int = 0
    is_favorite: bool = False


class DensityCard(BaseModel):
    symbol: str
    market_type: str
    densities: list[DensityItem] = Field(default_factory=list)
    max_volume: float = 0
    is_favorite: bool = False


class ExchangeInfo(BaseModel):
    id: str
    name: str
    spot: bool = True
    futures: bool = True


class ScanStatus(BaseModel):
    scanning: bool = False
    last_scan_time: str | None = None
    total_densities: int = 0
    exchanges_scanned: int = 0
    symbols_scanned: int = 0
    auto_scan: bool = False
    errors: list[str] = Field(default_factory=list)
