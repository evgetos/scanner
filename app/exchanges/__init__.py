from __future__ import annotations

import asyncio
import logging
from typing import Any

import ccxt.async_support as ccxt

logger = logging.getLogger(__name__)

EXCHANGE_MAP: dict[str, type] = {
    "gate": ccxt.gateio,
    "bybit": ccxt.bybit,
    "mexc": ccxt.mexc,
    "kucoin": ccxt.kucoin,
    "okx": ccxt.okx,
    "bitget": ccxt.bitget,
    "hyperliquid": ccxt.hyperliquid,
}

EXCHANGE_DISPLAY_NAMES: dict[str, str] = {
    "gate": "Gate.io",
    "bybit": "Bybit",
    "mexc": "MEXC",
    "kucoin": "KuCoin",
    "okx": "OKX",
    "bitget": "Bitget",
    "hyperliquid": "HyperLiquid",
}

EXCHANGE_FEATURES: dict[str, dict[str, bool]] = {
    "gate": {"spot": True, "futures": True},
    "bybit": {"spot": True, "futures": True},
    "mexc": {"spot": True, "futures": True},
    "kucoin": {"spot": True, "futures": True},
    "okx": {"spot": True, "futures": True},
    "bitget": {"spot": True, "futures": True},
    "hyperliquid": {"spot": False, "futures": True},
}


class ExchangeManager:
    """Manages ccxt exchange instances with connection pooling."""

    def __init__(self) -> None:
        self._instances: dict[str, ccxt.Exchange] = {}
        self._markets_loaded: dict[str, bool] = {}
        self._lock = asyncio.Lock()

    async def get_exchange(self, exchange_id: str) -> ccxt.Exchange | None:
        if exchange_id not in EXCHANGE_MAP:
            return None

        async with self._lock:
            if exchange_id not in self._instances:
                cls = EXCHANGE_MAP[exchange_id]
                instance = cls(
                    {
                        "enableRateLimit": True,
                        "timeout": 30000,
                        "options": {"defaultType": "spot"},
                    }
                )
                self._instances[exchange_id] = instance
                self._markets_loaded[exchange_id] = False

        instance = self._instances[exchange_id]
        if not self._markets_loaded.get(exchange_id):
            try:
                await instance.load_markets()
                self._markets_loaded[exchange_id] = True
            except Exception as e:
                logger.error("Failed to load markets for %s: %s", exchange_id, e)
                return None

        return instance

    def get_markets(
        self, exchange_id: str, market_type: str
    ) -> list[dict[str, Any]]:
        instance = self._instances.get(exchange_id)
        if not instance or not instance.markets:
            return []

        results = []
        for symbol, market in instance.markets.items():
            if market_type == "spot" and market.get("spot"):
                results.append(market)
            elif market_type == "futures" and market.get("swap"):
                results.append(market)

        return results

    async def fetch_tickers(
        self, exchange_id: str, symbols: list[str] | None = None
    ) -> dict[str, Any]:
        instance = self._instances.get(exchange_id)
        if not instance:
            return {}
        try:
            if symbols and len(symbols) <= 100:
                return await instance.fetch_tickers(symbols)
            return await instance.fetch_tickers()
        except Exception as e:
            logger.error("Failed to fetch tickers for %s: %s", exchange_id, e)
            return {}

    async def fetch_order_book(
        self, exchange_id: str, symbol: str, limit: int = 50
    ) -> dict[str, Any] | None:
        instance = self._instances.get(exchange_id)
        if not instance:
            return None
        try:
            return await instance.fetch_order_book(symbol, limit=limit)
        except Exception as e:
            logger.debug("Failed to fetch order book %s/%s: %s", exchange_id, symbol, e)
            return None

    async def close_all(self) -> None:
        for exchange_id, instance in self._instances.items():
            try:
                await instance.close()
            except Exception as e:
                logger.error("Failed to close %s: %s", exchange_id, e)
        self._instances.clear()
        self._markets_loaded.clear()
