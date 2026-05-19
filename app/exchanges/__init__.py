from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=20)


@dataclass
class TickerInfo:
    symbol: str
    display_symbol: str
    last_price: float
    volume_24h_quote: float


@dataclass
class OrderBook:
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]


EXCHANGE_REGISTRY: dict[str, type[BaseExchange]] = {}


class BaseExchange:
    exchange_id: str = ""
    exchange_name: str = ""
    has_spot: bool = True
    has_futures: bool = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.exchange_id:
            EXCHANGE_REGISTRY[cls.exchange_id] = cls

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session

    async def _get(self, url: str, params: dict | None = None) -> Any:
        try:
            async with self.session.get(url, params=params, timeout=TIMEOUT) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.debug("%s GET %s failed: %s", self.exchange_id, url, e)
        return None

    async def _post(self, url: str, json_data: dict | None = None) -> Any:
        try:
            async with self.session.post(url, json=json_data, timeout=TIMEOUT) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.debug("%s POST %s failed: %s", self.exchange_id, url, e)
        return None

    async def get_tickers(self, market_type: str) -> list[TickerInfo]:
        raise NotImplementedError

    async def get_orderbook(self, symbol: str, market_type: str, limit: int = 50) -> OrderBook | None:
        raise NotImplementedError

    def _normalize_symbol(self, raw: str) -> str:
        return raw.replace("/", "").replace("-", "").replace("_", "").upper()


class GateExchange(BaseExchange):
    exchange_id = "gate"
    exchange_name = "Gate.io"

    async def get_tickers(self, market_type: str) -> list[TickerInfo]:
        tickers: list[TickerInfo] = []
        if market_type == "spot":
            data = await self._get("https://api.gateio.ws/api/v4/spot/tickers")
            if not data:
                return []
            for t in data:
                try:
                    last = float(t.get("last", 0))
                    vol = float(t.get("quote_volume", 0))
                    pair = t["currency_pair"]
                    if last > 0:
                        tickers.append(TickerInfo(pair, pair.replace("_", ""), last, vol))
                except (ValueError, KeyError):
                    continue
        else:
            data = await self._get("https://api.gateio.ws/api/v4/futures/usdt/tickers")
            if not data:
                return []
            for t in data:
                try:
                    last = float(t.get("last", 0))
                    vol = float(t.get("volume_24h_quote", 0))
                    contract = t["contract"]
                    if last > 0:
                        tickers.append(TickerInfo(contract, contract.replace("_", ""), last, vol))
                except (ValueError, KeyError):
                    continue
        return tickers

    async def get_orderbook(self, symbol: str, market_type: str, limit: int = 50) -> OrderBook | None:
        if market_type == "spot":
            data = await self._get(
                "https://api.gateio.ws/api/v4/spot/order_book",
                {"currency_pair": symbol, "limit": str(limit)},
            )
        else:
            data = await self._get(
                "https://api.gateio.ws/api/v4/futures/usdt/order_book",
                {"contract": symbol, "limit": str(limit)},
            )
        if not data:
            return None
        bids = [(float(p), float(a)) for p, a in data.get("bids", [])]
        asks = [(float(p), float(a)) for p, a in data.get("asks", [])]
        return OrderBook(bids=bids, asks=asks)


class BybitExchange(BaseExchange):
    exchange_id = "bybit"
    exchange_name = "Bybit"

    async def get_tickers(self, market_type: str) -> list[TickerInfo]:
        category = "spot" if market_type == "spot" else "linear"
        data = await self._get(
            "https://api.bybit.com/v5/market/tickers", {"category": category}
        )
        if not data or data.get("retCode") != 0:
            return []
        tickers: list[TickerInfo] = []
        for t in data.get("result", {}).get("list", []):
            try:
                sym = t["symbol"]
                last = float(t.get("lastPrice", 0))
                vol = float(t.get("turnover24h", 0))
                if last > 0:
                    tickers.append(TickerInfo(sym, sym, last, vol))
            except (ValueError, KeyError):
                continue
        return tickers

    async def get_orderbook(self, symbol: str, market_type: str, limit: int = 50) -> OrderBook | None:
        category = "spot" if market_type == "spot" else "linear"
        data = await self._get(
            "https://api.bybit.com/v5/market/orderbook",
            {"category": category, "symbol": symbol, "limit": str(limit)},
        )
        if not data or data.get("retCode") != 0:
            return None
        result = data.get("result", {})
        bids = [(float(p), float(a)) for p, a in result.get("b", [])]
        asks = [(float(p), float(a)) for p, a in result.get("a", [])]
        return OrderBook(bids=bids, asks=asks)


class MexcExchange(BaseExchange):
    exchange_id = "mexc"
    exchange_name = "MEXC"

    async def get_tickers(self, market_type: str) -> list[TickerInfo]:
        tickers: list[TickerInfo] = []
        if market_type == "spot":
            data = await self._get("https://api.mexc.com/api/v3/ticker/24hr")
            if not data:
                return []
            for t in data:
                try:
                    sym = t["symbol"]
                    last = float(t.get("lastPrice", 0))
                    vol = float(t.get("quoteVolume", 0))
                    if last > 0:
                        tickers.append(TickerInfo(sym, sym, last, vol))
                except (ValueError, KeyError):
                    continue
        else:
            data = await self._get("https://contract.mexc.com/api/v1/contract/ticker")
            if not data or not data.get("success"):
                return []
            for t in data.get("data", []):
                try:
                    sym = t["symbol"]
                    last = float(t.get("lastPrice", 0))
                    vol = float(t.get("volume24", 0)) * last
                    if last > 0:
                        display = sym.replace("_", "")
                        tickers.append(TickerInfo(sym, display, last, vol))
                except (ValueError, KeyError):
                    continue
        return tickers

    async def get_orderbook(self, symbol: str, market_type: str, limit: int = 50) -> OrderBook | None:
        if market_type == "spot":
            data = await self._get(
                "https://api.mexc.com/api/v3/depth",
                {"symbol": symbol, "limit": str(limit)},
            )
        else:
            data = await self._get(
                f"https://contract.mexc.com/api/v1/contract/depth/{symbol}",
                {"limit": str(limit)},
            )
            if data and data.get("success"):
                data = data.get("data", {})
        if not data:
            return None
        bids = [(float(p), float(a)) for p, a in data.get("bids", [])]
        asks = [(float(p), float(a)) for p, a in data.get("asks", [])]
        return OrderBook(bids=bids, asks=asks)


class HyperliquidExchange(BaseExchange):
    exchange_id = "hyperliquid"
    exchange_name = "HyperLiquid"
    has_spot = False

    async def get_tickers(self, market_type: str) -> list[TickerInfo]:
        if market_type == "spot":
            return []
        data = await self._post(
            "https://api.hyperliquid.xyz/info",
            {"type": "metaAndAssetCtxs"},
        )
        if not data or not isinstance(data, list) or len(data) < 2:
            return []
        meta = data[0]
        ctxs = data[1]
        universe = meta.get("universe", [])
        tickers: list[TickerInfo] = []
        for i, asset in enumerate(universe):
            if i >= len(ctxs):
                break
            try:
                coin = asset["name"]
                ctx = ctxs[i]
                last = float(ctx.get("markPx", 0))
                vol = float(ctx.get("dayNtlVlm", 0))
                if last > 0:
                    tickers.append(TickerInfo(coin, f"{coin}USDT", last, vol))
            except (ValueError, KeyError, IndexError):
                continue
        return tickers

    async def get_orderbook(self, symbol: str, market_type: str, limit: int = 50) -> OrderBook | None:
        data = await self._post(
            "https://api.hyperliquid.xyz/info",
            {"type": "l2Book", "coin": symbol},
        )
        if not data:
            return None
        levels = data.get("levels", [])
        if len(levels) < 2:
            return None
        bids = [(float(lv["px"]), float(lv["sz"])) for lv in levels[0][:limit]]
        asks = [(float(lv["px"]), float(lv["sz"])) for lv in levels[1][:limit]]
        return OrderBook(bids=bids, asks=asks)


class KucoinExchange(BaseExchange):
    exchange_id = "kucoin"
    exchange_name = "KuCoin"

    async def get_tickers(self, market_type: str) -> list[TickerInfo]:
        tickers: list[TickerInfo] = []
        if market_type == "spot":
            data = await self._get("https://api.kucoin.com/api/v1/market/allTickers")
            if not data or data.get("code") != "200000":
                return []
            for t in data.get("data", {}).get("ticker", []):
                try:
                    sym = t["symbol"]
                    last = float(t.get("last", 0))
                    vol = float(t.get("volValue", 0))
                    if last > 0:
                        display = sym.replace("-", "")
                        tickers.append(TickerInfo(sym, display, last, vol))
                except (ValueError, KeyError):
                    continue
        else:
            data = await self._get("https://api-futures.kucoin.com/api/v1/contracts/active")
            if not data or data.get("code") != "200000":
                return []
            for t in data.get("data", []):
                try:
                    sym = t["symbol"]
                    last = float(t.get("lastTradePrice", 0) or t.get("markPrice", 0))
                    vol = float(t.get("turnoverOf24h", 0))
                    if last > 0:
                        display = sym.replace("-", "").replace("M", "")
                        tickers.append(TickerInfo(sym, display, last, vol))
                except (ValueError, KeyError):
                    continue
        return tickers

    async def get_orderbook(self, symbol: str, market_type: str, limit: int = 50) -> OrderBook | None:
        if market_type == "spot":
            data = await self._get(
                f"https://api.kucoin.com/api/v1/market/orderbook/level2_100",
                {"symbol": symbol},
            )
            if not data or data.get("code") != "200000":
                return None
            book = data.get("data", {})
        else:
            data = await self._get(
                f"https://api-futures.kucoin.com/api/v1/level2/depth100",
                {"symbol": symbol},
            )
            if not data or data.get("code") != "200000":
                return None
            book = data.get("data", {})
        bids = [(float(p), float(a)) for p, a in book.get("bids", [])[:limit]]
        asks = [(float(p), float(a)) for p, a in book.get("asks", [])[:limit]]
        return OrderBook(bids=bids, asks=asks)


class OkxExchange(BaseExchange):
    exchange_id = "okx"
    exchange_name = "OKX"

    async def get_tickers(self, market_type: str) -> list[TickerInfo]:
        inst_type = "SPOT" if market_type == "spot" else "SWAP"
        data = await self._get(
            "https://www.okx.com/api/v5/market/tickers",
            {"instType": inst_type},
        )
        if not data or data.get("code") != "0":
            return []
        tickers: list[TickerInfo] = []
        for t in data.get("data", []):
            try:
                inst_id = t["instId"]
                last = float(t.get("last", 0))
                vol = float(t.get("volCcy24h", 0))
                if market_type != "spot":
                    vol = float(t.get("volCcy24h", 0)) * last
                if last > 0:
                    display = inst_id.replace("-", "").replace("SWAP", "")
                    tickers.append(TickerInfo(inst_id, display, last, vol))
            except (ValueError, KeyError):
                continue
        return tickers

    async def get_orderbook(self, symbol: str, market_type: str, limit: int = 50) -> OrderBook | None:
        data = await self._get(
            "https://www.okx.com/api/v5/market/books",
            {"instId": symbol, "sz": str(limit)},
        )
        if not data or data.get("code") != "0":
            return None
        books = data.get("data", [])
        if not books:
            return None
        book = books[0]
        bids = [(float(row[0]), float(row[1])) for row in book.get("bids", [])]
        asks = [(float(row[0]), float(row[1])) for row in book.get("asks", [])]
        return OrderBook(bids=bids, asks=asks)


class BitgetExchange(BaseExchange):
    exchange_id = "bitget"
    exchange_name = "Bitget"

    async def get_tickers(self, market_type: str) -> list[TickerInfo]:
        tickers: list[TickerInfo] = []
        if market_type == "spot":
            data = await self._get("https://api.bitget.com/api/v2/spot/market/tickers")
            if not data or data.get("code") != "00000":
                return []
            for t in data.get("data", []):
                try:
                    sym = t["symbol"]
                    last = float(t.get("lastPr", 0))
                    vol = float(t.get("quoteVolume", 0))
                    if last > 0:
                        tickers.append(TickerInfo(sym, sym, last, vol))
                except (ValueError, KeyError):
                    continue
        else:
            data = await self._get(
                "https://api.bitget.com/api/v2/mix/market/tickers",
                {"productType": "USDT-FUTURES"},
            )
            if not data or data.get("code") != "00000":
                return []
            for t in data.get("data", []):
                try:
                    sym = t["symbol"]
                    last = float(t.get("lastPr", 0))
                    vol = float(t.get("quoteVolume", 0))
                    if last > 0:
                        tickers.append(TickerInfo(sym, sym, last, vol))
                except (ValueError, KeyError):
                    continue
        return tickers

    async def get_orderbook(self, symbol: str, market_type: str, limit: int = 50) -> OrderBook | None:
        if market_type == "spot":
            data = await self._get(
                "https://api.bitget.com/api/v2/spot/market/orderbook",
                {"symbol": symbol, "limit": str(limit)},
            )
        else:
            data = await self._get(
                "https://api.bitget.com/api/v2/mix/market/merge-depth",
                {"symbol": symbol, "productType": "USDT-FUTURES", "limit": str(limit)},
            )
        if not data or data.get("code") != "00000":
            return None
        book = data.get("data", {})
        bids = [(float(p), float(a)) for p, a in book.get("bids", [])[:limit]]
        asks = [(float(p), float(a)) for p, a in book.get("asks", [])[:limit]]
        return OrderBook(bids=bids, asks=asks)


class ExchangeManager:
    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None
        self._instances: dict[str, BaseExchange] = {}

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def get_exchange(self, exchange_id: str) -> BaseExchange | None:
        if exchange_id not in EXCHANGE_REGISTRY:
            return None
        if exchange_id not in self._instances:
            session = await self._ensure_session()
            cls = EXCHANGE_REGISTRY[exchange_id]
            self._instances[exchange_id] = cls(session)
        return self._instances[exchange_id]

    def get_all_exchange_info(self) -> list[dict[str, Any]]:
        result = []
        for eid, cls in EXCHANGE_REGISTRY.items():
            result.append({
                "id": eid,
                "name": cls.exchange_name,
                "spot": cls.has_spot,
                "futures": cls.has_futures,
            })
        return result

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()
        self._instances.clear()
