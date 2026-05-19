from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.exchanges import (
    EXCHANGE_DISPLAY_NAMES,
    EXCHANGE_FEATURES,
    ExchangeManager,
)
from app.models import DensityResult, ScanSettings, ScanStatus

logger = logging.getLogger(__name__)


def _analyse_orderbook(
    orderbook: dict[str, Any],
    current_price: float,
    min_density_usd: float,
    max_distance_pct: float,
) -> list[dict[str, Any]]:
    """Find large density levels in an order book."""
    densities: list[dict[str, Any]] = []

    for side_name, key in [("bid", "bids"), ("ask", "asks")]:
        orders: list[list[float]] = orderbook.get(key, [])
        if not orders:
            continue

        volumes = [p * a for p, a in orders if p > 0 and a > 0]
        avg_volume = sum(volumes) / len(volumes) if volumes else 0

        for price, amount in orders:
            if price <= 0 or amount <= 0:
                continue

            volume_usd = price * amount
            distance_pct = abs(price - current_price) / current_price * 100

            if distance_pct > max_distance_pct:
                continue

            if volume_usd < min_density_usd:
                continue

            volume_ratio = volume_usd / avg_volume if avg_volume > 0 else 0

            densities.append(
                {
                    "side": side_name,
                    "price": price,
                    "volume_usd": volume_usd,
                    "amount": amount,
                    "distance_pct": round(distance_pct, 4),
                    "volume_ratio": round(volume_ratio, 2),
                }
            )

    return densities


class DensityScanner:
    """Scans order books across exchanges for large density clusters."""

    def __init__(self) -> None:
        self.exchange_manager = ExchangeManager()
        self.status = ScanStatus()
        self.results: list[DensityResult] = []
        self._scan_lock = asyncio.Lock()

    async def scan(self, settings: ScanSettings) -> list[DensityResult]:
        """Run a full scan across all enabled exchanges."""
        if self._scan_lock.locked():
            return self.results

        async with self._scan_lock:
            self.status.scanning = True
            self.status.errors = []
            all_densities: list[DensityResult] = []
            exchanges_scanned = 0
            symbols_scanned = 0

            tasks = []
            for ex_id in settings.enabled_exchanges:
                if ex_id not in EXCHANGE_FEATURES:
                    continue
                tasks.append(
                    self._scan_exchange(ex_id, settings)
                )

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for r in results:
                if isinstance(r, Exception):
                    self.status.errors.append(str(r))
                    continue
                densities, n_symbols = r
                all_densities.extend(densities)
                exchanges_scanned += 1
                symbols_scanned += n_symbols

            favorites_lower = {f.upper() for f in settings.favorites}
            for d in all_densities:
                base = d.symbol.split("/")[0] if "/" in d.symbol else d.symbol
                d.is_favorite = base.upper() in favorites_lower or d.symbol.upper() in favorites_lower

            all_densities.sort(key=lambda x: x.volume_usd, reverse=True)

            self.results = all_densities
            self.status.scanning = False
            self.status.last_scan_time = datetime.now(timezone.utc).isoformat()
            self.status.total_densities = len(all_densities)
            self.status.exchanges_scanned = exchanges_scanned
            self.status.symbols_scanned = symbols_scanned

            return all_densities

    async def _scan_exchange(
        self,
        exchange_id: str,
        settings: ScanSettings,
    ) -> tuple[list[DensityResult], int]:
        """Scan a single exchange for densities."""
        exchange = await self.exchange_manager.get_exchange(exchange_id)
        if not exchange:
            raise RuntimeError(f"Failed to initialize {exchange_id}")

        features = EXCHANGE_FEATURES.get(exchange_id, {})
        display_name = EXCHANGE_DISPLAY_NAMES.get(exchange_id, exchange_id)
        all_densities: list[DensityResult] = []
        total_symbols = 0

        for market_type in settings.market_types:
            if not features.get(market_type, False):
                continue

            min_volume = (
                settings.min_volume_spot
                if market_type == "spot"
                else settings.min_volume_futures
            )

            markets = self.exchange_manager.get_markets(exchange_id, market_type)
            if not markets:
                continue

            symbols_to_scan = await self._filter_by_volume(
                exchange_id, markets, min_volume, settings, market_type
            )
            total_symbols += len(symbols_to_scan)

            densities = await self._scan_symbols(
                exchange_id,
                display_name,
                market_type,
                symbols_to_scan,
                settings,
            )
            all_densities.extend(densities)

        return all_densities, total_symbols

    async def _filter_by_volume(
        self,
        exchange_id: str,
        markets: list[dict[str, Any]],
        min_volume: float,
        settings: ScanSettings,
        market_type: str,
    ) -> list[dict[str, Any]]:
        """Filter markets by 24h volume."""
        symbols = [m["symbol"] for m in markets if m.get("active", True)]

        favorites_upper = {f.upper() for f in settings.favorites}

        try:
            tickers = await self.exchange_manager.fetch_tickers(exchange_id)
        except Exception as e:
            logger.error("Ticker fetch failed for %s: %s", exchange_id, e)
            return []

        filtered: list[dict[str, Any]] = []
        for m in markets:
            sym = m["symbol"]
            if not m.get("active", True):
                continue

            ticker = tickers.get(sym)
            if not ticker:
                continue

            quote_vol = ticker.get("quoteVolume") or 0
            last_price = ticker.get("last") or ticker.get("close") or 0

            if last_price <= 0:
                continue

            base = sym.split("/")[0] if "/" in sym else sym
            is_fav = base.upper() in favorites_upper or sym.upper() in favorites_upper

            if quote_vol >= min_volume or is_fav:
                filtered.append(
                    {
                        "symbol": sym,
                        "last_price": last_price,
                        "volume_24h": quote_vol,
                        "market": m,
                    }
                )

        filtered.sort(key=lambda x: x["volume_24h"], reverse=True)
        return filtered[: settings.max_symbols_per_exchange]

    async def _scan_symbols(
        self,
        exchange_id: str,
        display_name: str,
        market_type: str,
        symbols_info: list[dict[str, Any]],
        settings: ScanSettings,
    ) -> list[DensityResult]:
        """Scan order books for a list of symbols on one exchange."""
        densities: list[DensityResult] = []

        batch_size = 5
        for i in range(0, len(symbols_info), batch_size):
            batch = symbols_info[i : i + batch_size]
            tasks = [
                self._scan_single_symbol(
                    exchange_id, display_name, market_type, info, settings
                )
                for info in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    continue
                densities.extend(r)

            if i + batch_size < len(symbols_info):
                await asyncio.sleep(0.2)

        return densities

    async def _scan_single_symbol(
        self,
        exchange_id: str,
        display_name: str,
        market_type: str,
        info: dict[str, Any],
        settings: ScanSettings,
    ) -> list[DensityResult]:
        """Scan a single symbol's order book for densities."""
        symbol = info["symbol"]
        current_price = info["last_price"]
        volume_24h = info["volume_24h"]

        orderbook = await self.exchange_manager.fetch_order_book(
            exchange_id, symbol, limit=50
        )
        if not orderbook:
            return []

        raw = _analyse_orderbook(
            orderbook,
            current_price,
            settings.min_density_usd,
            settings.max_distance_pct,
        )

        results = []
        for d in raw:
            results.append(
                DensityResult(
                    exchange=display_name,
                    symbol=symbol,
                    market_type=market_type,
                    side=d["side"],
                    price=d["price"],
                    volume_usd=round(d["volume_usd"], 2),
                    amount=d["amount"],
                    distance_pct=d["distance_pct"],
                    volume_ratio=d["volume_ratio"],
                    volume_24h_usd=round(volume_24h, 2),
                )
            )

        return results

    async def close(self) -> None:
        await self.exchange_manager.close_all()
