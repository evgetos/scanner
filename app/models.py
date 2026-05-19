from __future__ import annotations

from pydantic import BaseModel, Field


class ScanSettings(BaseModel):
    min_volume_spot: float = Field(default=100_000, description="Min 24h volume for spot (USD)")
    min_volume_futures: float = Field(default=100_000, description="Min 24h volume for futures (USD)")
    max_distance_pct: float = Field(default=5.0, description="Max distance from current price (%)")
    min_density_usd: float = Field(default=50_000, description="Min volume for a density cluster (USD)")
    enabled_exchanges: list[str] = Field(
        default_factory=lambda: [
            "gate", "bybit", "mexc", "hyperliquid",
            "kucoin", "okx", "bitget",
        ]
    )
    market_types: list[str] = Field(default_factory=lambda: ["spot", "futures"])
    favorites: list[str] = Field(default_factory=list)
    max_symbols_per_exchange: int = Field(default=50, description="Max symbols to scan per exchange")


class DensityResult(BaseModel):
    exchange: str
    symbol: str
    market_type: str  # "spot" or "futures"
    side: str  # "bid" or "ask"
    price: float
    volume_usd: float
    amount: float
    distance_pct: float
    volume_ratio: float  # ratio vs avg level volume
    volume_24h_usd: float
    orders_count: int = 1
    is_favorite: bool = False


class ExchangeInfo(BaseModel):
    id: str
    name: str
    available: bool = True
    spot: bool = True
    futures: bool = True


class ScanStatus(BaseModel):
    scanning: bool = False
    last_scan_time: str | None = None
    total_densities: int = 0
    exchanges_scanned: int = 0
    symbols_scanned: int = 0
    errors: list[str] = Field(default_factory=list)
