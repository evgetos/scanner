from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.exchanges import ExchangeManager, OrderBook, TickerInfo
from app.models import DensityCard, DensityItem, ScanSettings, ScanStatus

logger = logging.getLogger(__name__)


def _analyse_orderbook(
    orderbook: OrderBook,
    current_price: float,
    min_density_usd: float,
    max_distance_pct: float,
) -> list[dict[str, Any]]:
    densities: list[dict[str, Any]] = []

    for side_name, orders in [("bid", orderbook.bids), ("ask", orderbook.asks)]:
        if not orders:
            continue
        volumes = [p * a for p, a in orders if p > 0 and a > 0]
        avg_volume = sum(volumes) / len(volumes) if volumes else 0

        for price, amount in orders:
            if price <= 0 or amount <= 0:
                continue
            volume_usd = price * amount
            distance_pct = abs(price - current_price) / current_price * 100
            if distance_pct > max_distance_pct or volume_usd < min_density_usd:
                continue
            volume_ratio = volume_usd / avg_volume if avg_volume > 0 else 0
            densities.append({
                "side": side_name,
                "price": price,
                "volume_usd": volume_usd,
                "amount": amount,
                "distance_pct": round(distance_pct, 4),
                "volume_ratio": round(volume_ratio, 2),
            })
    return densities


def _price_key(price: float) -> str:
    if price >= 1000:
        return f"{price:.1f}"
    if price >= 1:
        return f"{price:.3f}"
    if price >= 0.01:
        return f"{price:.5f}"
    return f"{price:.8f}"


class DensityScanner:
    def __init__(self) -> None:
        self.exchange_manager = ExchangeManager()
        self.status = ScanStatus()
        self.cards: list[DensityCard] = []
        self.settings = ScanSettings()
        self._scan_lock = asyncio.Lock()
        self._density_times: dict[str, float] = {}
        self._auto_scan_task: asyncio.Task | None = None  # type: ignore[type-arg]

    def start_auto_scan(self) -> None:
        if self._auto_scan_task and not self._auto_scan_task.done():
            return
        self.settings.auto_scan = True
        self.status.auto_scan = True
        self._auto_scan_task = asyncio.create_task(self._auto_scan_loop())

    def stop_auto_scan(self) -> None:
        self.settings.auto_scan = False
        self.status.auto_scan = False
        if self._auto_scan_task:
            self._auto_scan_task.cancel()
            self._auto_scan_task = None

    async def _auto_scan_loop(self) -> None:
        while self.settings.auto_scan:
            try:
                await self.scan(self.settings)
            except Exception as e:
                logger.error("Auto-scan error: %s", e)
            await asyncio.sleep(self.settings.scan_interval)

    async def scan(self, settings: ScanSettings) -> list[DensityCard]:
        if self._scan_lock.locked():
            return self.cards

        async with self._scan_lock:
            self.settings = settings
            self.status.scanning = True
            self.status.errors = []
            all_items: list[DensityItem] = []
            exchanges_ok = 0
            symbols_total = 0

            tasks = []
            for ex_id in settings.enabled_exchanges:
                tasks.append(self._scan_exchange(ex_id, settings))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    self.status.errors.append(str(r))
                    continue
                items, n_sym = r
                all_items.extend(items)
                exchanges_ok += 1
                symbols_total += n_sym

            now = time.time()
            new_times: dict[str, float] = {}
            for item in all_items:
                key = f"{item.exchange_id}|{item.symbol}|{item.side}|{_price_key(item.price)}"
                first = self._density_times.get(key, now)
                new_times[key] = first
                item.age_seconds = int(now - first)

            self._density_times = new_times

            favorites_upper = {f.upper() for f in settings.favorites}
            for item in all_items:
                base = item.symbol.split("/")[0] if "/" in item.symbol else item.symbol
                base_clean = base.replace("USDT", "").replace("USD", "").replace("BUSD", "")
                item.is_favorite = (
                    base.upper() in favorites_upper
                    or base_clean.upper() in favorites_upper
                    or item.symbol.upper() in favorites_upper
                )

            cards = self._group_into_cards(all_items)

            self.cards = cards
            self.status.scanning = False
            self.status.last_scan_time = datetime.now(timezone.utc).isoformat()
            self.status.total_densities = len(all_items)
            self.status.exchanges_scanned = exchanges_ok
            self.status.symbols_scanned = symbols_total
            return cards

    def _group_into_cards(self, items: list[DensityItem]) -> list[DensityCard]:
        groups: dict[str, list[DensityItem]] = defaultdict(list)
        for item in items:
            key = f"{item.symbol}|{item.market_type}"
            groups[key].append(item)

        cards: list[DensityCard] = []
        for key, group in groups.items():
            asks = sorted(
                [d for d in group if d.side == "ask"],
                key=lambda x: x.volume_usd, reverse=True,
            )
            bids = sorted(
                [d for d in group if d.side == "bid"],
                key=lambda x: x.volume_usd, reverse=True,
            )
            ordered = asks + bids
            sym, mtype = key.split("|", 1)
            max_vol = max(d.volume_usd for d in ordered) if ordered else 0
            is_fav = any(d.is_favorite for d in ordered)
            cards.append(DensityCard(
                symbol=sym,
                market_type=mtype,
                densities=ordered,
                max_volume=max_vol,
                is_favorite=is_fav,
            ))

        cards.sort(key=lambda c: c.max_volume, reverse=True)
        return cards

    async def _scan_exchange(
        self, exchange_id: str, settings: ScanSettings
    ) -> tuple[list[DensityItem], int]:
        exchange = await self.exchange_manager.get_exchange(exchange_id)
        if not exchange:
            raise RuntimeError(f"Unknown exchange: {exchange_id}")

        all_items: list[DensityItem] = []
        total_symbols = 0

        for market_type in settings.market_types:
            if market_type == "spot" and not exchange.has_spot:
                continue
            if market_type == "futures" and not exchange.has_futures:
                continue

            min_vol = (
                settings.min_volume_spot
                if market_type == "spot"
                else settings.min_volume_futures
            )

            try:
                tickers = await exchange.get_tickers(market_type)
            except Exception as e:
                logger.error("Tickers failed %s/%s: %s", exchange_id, market_type, e)
                continue

            usdt_tickers = [
                t for t in tickers
                if t.display_symbol.upper().endswith("USDT") and t.last_price > 0
            ]

            favorites_upper = {f.upper() for f in settings.favorites}
            filtered = [
                t for t in usdt_tickers
                if t.volume_24h_quote >= min_vol
                or any(
                    f in t.display_symbol.upper()
                    for f in favorites_upper
                )
            ]
            filtered.sort(key=lambda t: t.volume_24h_quote, reverse=True)
            filtered = filtered[: settings.max_symbols_per_exchange]
            total_symbols += len(filtered)

            batch_size = 5
            for i in range(0, len(filtered), batch_size):
                batch = filtered[i: i + batch_size]
                tasks = [
                    self._scan_symbol(exchange, market_type, t, settings)
                    for t in batch
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for r in results:
                    if isinstance(r, Exception):
                        continue
                    all_items.extend(r)
                if i + batch_size < len(filtered):
                    await asyncio.sleep(0.15)

        return all_items, total_symbols

    async def _scan_symbol(
        self,
        exchange: Any,
        market_type: str,
        ticker: TickerInfo,
        settings: ScanSettings,
    ) -> list[DensityItem]:
        orderbook = await exchange.get_orderbook(ticker.symbol, market_type, limit=50)
        if not orderbook:
            return []

        raw = _analyse_orderbook(
            orderbook, ticker.last_price,
            settings.min_density_usd, settings.max_distance_pct,
        )

        items = []
        for d in raw:
            items.append(DensityItem(
                exchange=exchange.exchange_name,
                exchange_id=exchange.exchange_id,
                symbol=ticker.display_symbol,
                market_type=market_type,
                side=d["side"],
                price=d["price"],
                volume_usd=round(d["volume_usd"], 2),
                amount=d["amount"],
                distance_pct=d["distance_pct"],
                volume_ratio=d["volume_ratio"],
                volume_24h_usd=round(ticker.volume_24h_quote, 2),
            ))
        return items

    async def close(self) -> None:
        self.stop_auto_scan()
        await self.exchange_manager.close()
