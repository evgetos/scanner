"""Async crypto arbitrage scanner library.

This module is a refactor of the original ``scanner.py`` console script into a
reusable async library. Network/parsing logic is kept identical; printing and
formatting was replaced with structured dataclasses suitable for serving over
HTTP.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

try:  # pragma: no cover - optional dep
    from aiohttp_socks import ProxyConnector
except ImportError:  # pragma: no cover
    ProxyConnector = None  # type: ignore[assignment]

from .config import ScannerConfig

logger = logging.getLogger(__name__)

HTTP_HEADERS = {
    "accept": "application/json",
    "user-agent": "Mozilla/5.0",
}

EXCHANGES_ORDER: List[str] = [
    "Binance", "Bybit", "OKX", "Bitget", "BingX",
    "MEXC", "KuCoin", "Huobi", "Gateio",
    "Blofin", "Hyperliquid", "XT", "Asterdex",
]

# Maps exchange name → (bid_key, ask_key) on its ticker payload.
BID_ASK_KEYS: Dict[str, Tuple[str, str]] = {
    "Binance":  ("bidPrice", "askPrice"),
    "MEXC":     ("bidPrice", "askPrice"),
    "BingX":    ("bidPrice", "askPrice"),
    "Bitget":   ("bidPr", "askPr"),
    "Bybit":    ("bid1Price", "ask1Price"),
    "OKX":      ("bidPx", "askPx"),
    "KuCoin":   ("buy", "sell"),
    "Huobi":    ("bid", "ask"),
    "Gateio":   ("highest_bid", "lowest_ask"),
    "XT":       ("bp", "ap"),
    "Blofin":   ("bidPrice", "askPrice"),
    "Asterdex": ("bidPrice", "askPrice"),
}

_CHAIN_ALIASES = {
    "ERC20": "ETH", "ETHEREUM": "ETH", "ERC-20": "ETH",
    "BEP20": "BSC", "BNB SMART CHAIN": "BSC", "BEP-20": "BSC",
    "TRC20": "TRX", "TRON": "TRX", "TRC-20": "TRX",
    "ARBITRUMONE": "ARBITRUM", "ARBITRUM ONE": "ARBITRUM",
    "ARB": "ARBITRUM", "ARBEVM": "ARBITRUM",
    "SOLANA": "SOL",
    "POLYGON": "MATIC", "POLYGON POS": "MATIC",
    "AVAXC": "AVAX_C", "AVAXC-CHAIN": "AVAX_C", "AVAX C-CHAIN": "AVAX_C",
    "AVAXCCHAIN": "AVAX_C", "AVALANCHE C-CHAIN": "AVAX_C",
    "OPTIMISM": "OP", "OPETH": "OP",
    "THE OPEN NETWORK": "TON",
    "APTOS": "APT",
    "ZKSYNCERA": "ZKSYNC", "ZKSYNC ERA": "ZKSYNC",
    "CAP20": "CHZ2", "CHZ": "CHZ2", "CHILIZ CHAIN(CHZ2)": "CHZ2",
    "COSMOS": "ATOM",
    "FANTOM": "FTM",
    "NEAR PROTOCOL": "NEAR",
    "CRONOS": "CRO",
}


# ─── Dataclasses ─────────────────────────────────────────────────────────


@dataclass
class ChainMatch:
    chain: str
    contract: str
    buy_withdraw: bool
    sell_deposit: bool
    transfer_ok: bool


@dataclass
class OrderbookAnalysis:
    volume_usdt: float
    avg_spread: float
    profit_usdt: float


@dataclass
class ArbitrageRow:
    pair: str
    coin: str
    buy_exchange: str
    buy_price: float
    buy_volume: float
    sell_exchange: str
    sell_price: float
    sell_volume: float
    spread: float
    exchanges_count: int
    buy_deposit: str
    buy_withdraw: str
    sell_deposit: str
    sell_withdraw: str
    common_chains: List[ChainMatch] = field(default_factory=list)
    has_transfer: bool = False
    orderbook: Optional[OrderbookAnalysis] = None


@dataclass
class ExchangeSummary:
    name: str
    pairs_count: int
    dw_count: int


@dataclass
class ScanResult:
    finished_at: float
    duration_seconds: float
    proxy_used: Optional[str]
    min_volume: float
    min_spread: float
    max_spread: float
    total_pairs: int
    multi_exchange_pairs: int
    exchanges: List[ExchangeSummary]
    arbitrage: List[ArbitrageRow]


# ─── Utility helpers ─────────────────────────────────────────────────────


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def coin_from_pair(pair: str) -> str:
    return pair[:-4] if pair.endswith("USDT") else pair


def normalize_chain(chain_name: str) -> str:
    name = chain_name.strip().upper()
    if name in _CHAIN_ALIASES:
        return _CHAIN_ALIASES[name]
    for suffix in (" MAINNET", " MAIN NET", " NETWORK", " CHAIN", " NET"):
        if name.endswith(suffix):
            short = name[: -len(suffix)]
            if short in _CHAIN_ALIASES:
                return _CHAIN_ALIASES[short]
            return short
    return name


def _create_session(proxy_url: Optional[str]) -> aiohttp.ClientSession:
    if not proxy_url:
        return aiohttp.ClientSession(headers=HTTP_HEADERS)

    scheme = proxy_url.split("://", 1)[0].lower() if "://" in proxy_url else ""

    if scheme in ("socks5", "socks4", "socks5h", "socks4a"):
        if ProxyConnector is None:
            raise RuntimeError(
                "SOCKS proxies require the aiohttp-socks package "
                "(pip install aiohttp-socks)."
            )
        connector = ProxyConnector.from_url(proxy_url)
        return aiohttp.ClientSession(headers=HTTP_HEADERS, connector=connector)

    return aiohttp.ClientSession(headers=HTTP_HEADERS)


def _http_proxy(proxy: Optional[str]) -> Optional[str]:
    """Return proxy only if it is suitable for aiohttp's ``proxy=`` kwarg."""
    if not proxy:
        return None
    if proxy.startswith("socks"):
        return None
    return proxy


async def fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    path: Optional[List[str]] = None,
    method: str = "GET",
    json_body: Optional[Dict[str, Any]] = None,
    proxy: Optional[str] = None,
) -> Any:
    try:
        kwargs: Dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=15)}
        if proxy:
            kwargs["proxy"] = proxy

        if method == "POST":
            req = session.post(url, json=json_body, **kwargs)
        else:
            req = session.get(url, **kwargs)

        async with req as response:
            data = await response.json(content_type=None)
            if path:
                for key in path:
                    if not isinstance(data, dict):
                        return []
                    data = data.get(key, {})
            return data
    except Exception as exc:  # noqa: BLE001 - keep parity with original script
        logger.debug("fetch_json failed for %s: %s", url, exc)
        return []


# ─── Ticker parsing ──────────────────────────────────────────────────────


def parse(
    data: Any,
    pair_key: str,
    price_key: str,
    vol_key: Optional[str] = None,
    bid_key: Optional[str] = None,
    ask_key: Optional[str] = None,
    min_volume: float = 0.0,
) -> List[Tuple[str, float, float, float, float]]:
    out: List[Tuple[str, float, float, float, float]] = []

    if not data or not isinstance(data, (list, dict)):
        return out

    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list):
                data = value
                break
        else:
            return out

    for item in data:
        try:
            raw_pair = str(item[pair_key])
            pair = raw_pair.replace("-", "").replace("_", "").upper()
            price = safe_float(item[price_key])
            volume = safe_float(item.get(vol_key, 0)) if vol_key else 0.0

            bid = safe_float(item.get(bid_key, 0)) if bid_key else 0.0
            ask = safe_float(item.get(ask_key, 0)) if ask_key else 0.0

            if not pair.endswith("USDT"):
                continue
            if bid == 0 and ask == 0:
                continue
            if price <= 0 or volume < min_volume:
                continue

            out.append((pair, price, volume, bid, ask))
        except (KeyError, TypeError, ValueError):
            continue

    return out


def parse_blofin(data: Any, min_volume: float) -> List[Tuple[str, float, float, float, float]]:
    out: List[Tuple[str, float, float, float, float]] = []
    if not isinstance(data, list):
        return out

    for item in data:
        try:
            pair = str(item["instId"]).replace("-", "").replace("_", "").upper()
            price = safe_float(item["last"])
            base_vol = safe_float(item.get("volCurrency24h", 0))
            volume = base_vol * price

            bid = safe_float(item.get("bidPrice", 0))
            ask = safe_float(item.get("askPrice", 0))

            if not pair.endswith("USDT"):
                continue
            if price <= 0 or volume < min_volume:
                continue

            out.append((pair, price, volume, bid, ask))
        except (KeyError, TypeError, ValueError):
            continue

    return out


def parse_hyperliquid(
    data: Any, min_volume: float
) -> List[Tuple[str, float, float, float, float]]:
    out: List[Tuple[str, float, float, float, float]] = []
    if not isinstance(data, list) or len(data) < 2:
        return out

    meta, ctxs = data[0], data[1]
    if not isinstance(meta, dict) or "universe" not in meta:
        return out

    universe = meta["universe"]

    for idx, ctx in enumerate(ctxs):
        try:
            coin = universe[idx]["name"]
            pair = coin.upper() + "USDT"
            price = safe_float(ctx.get("markPx", 0))
            volume = safe_float(ctx.get("dayNtlVlm", 0))

            impact = ctx.get("impactPxs", [])
            bid = safe_float(impact[0]) if len(impact) > 0 else price
            ask = safe_float(impact[1]) if len(impact) > 1 else price

            if price <= 0 or volume < min_volume:
                continue

            out.append((pair, price, volume, bid, ask))
        except (KeyError, TypeError, ValueError, IndexError):
            continue

    return out


# ─── Deposit / withdraw parsing ─────────────────────────────────────────


def parse_dw_bitget(data: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if not isinstance(data, list):
        return result
    for item in data:
        coin = str(item.get("coin", "")).upper()
        chain_list = []
        for chain in item.get("chains", []) or []:
            chain_list.append({
                "chain": normalize_chain(chain.get("chain", "")),
                "contract": str(chain.get("contractAddress", "")).strip(),
                "deposit": str(chain.get("rechargeable", "false")).lower() == "true",
                "withdraw": str(chain.get("withdrawable", "false")).lower() == "true",
            })
        result[coin] = _summarize_chain_list(chain_list)
    return result


def parse_dw_kucoin(data: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if not isinstance(data, list):
        return result
    for item in data:
        coin = str(item.get("currency", "")).upper()
        chain_list = []
        for chain in item.get("chains") or []:
            chain_list.append({
                "chain": normalize_chain(chain.get("chainName", "")),
                "contract": str(chain.get("contractAddress", "")).strip(),
                "deposit": bool(chain.get("isDepositEnabled", False)),
                "withdraw": bool(chain.get("isWithdrawEnabled", False)),
            })
        result[coin] = _summarize_chain_list(chain_list)
    return result


def parse_dw_huobi(data: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if not isinstance(data, list):
        return result
    for item in data:
        coin = str(item.get("currency", "")).upper()
        chain_list = []
        for chain in item.get("chains", []) or []:
            raw_chain = chain.get("displayName") or chain.get("baseChain") or ""
            chain_list.append({
                "chain": normalize_chain(raw_chain),
                "contract": str(chain.get("contractAddress", "")).strip(),
                "deposit": chain.get("depositStatus") == "allowed",
                "withdraw": chain.get("withdrawStatus") == "allowed",
            })
        result[coin] = _summarize_chain_list(chain_list)
    return result


def parse_dw_gateio(data: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if not isinstance(data, list):
        return result
    for item in data:
        coin = str(item.get("currency", "")).upper()
        chains_raw = item.get("chains", []) or []
        chain_list = []
        if chains_raw:
            for chain in chains_raw:
                chain_list.append({
                    "chain": normalize_chain(chain.get("name", "")),
                    "contract": str(chain.get("addr", "")).strip(),
                    "deposit": not chain.get("deposit_disabled", True),
                    "withdraw": not chain.get("withdraw_disabled", True),
                })
        else:
            chain_list.append({
                "chain": normalize_chain(item.get("chain", "")),
                "contract": "",
                "deposit": not item.get("deposit_disabled", True),
                "withdraw": not item.get("withdraw_disabled", True),
            })
        result[coin] = _summarize_chain_list(chain_list)
    return result


def parse_dw_xt(data: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if not isinstance(data, list):
        return result
    for item in data:
        coin = str(item.get("currency", "")).upper()
        chain_list = []
        for chain in item.get("supportChains", []) or []:
            chain_list.append({
                "chain": normalize_chain(chain.get("chain", "")),
                "contract": str(chain.get("contract", "")).strip(),
                "deposit": bool(chain.get("depositEnabled", False)),
                "withdraw": bool(chain.get("withdrawEnabled", False)),
            })
        result[coin] = _summarize_chain_list(chain_list)
    return result


def parse_dw_mexc(data: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if not isinstance(data, list):
        return result
    for item in data:
        coin = str(item.get("coin", "")).upper()
        chain_list = []
        for chain in item.get("networkList", []) or []:
            raw_chain = chain.get("netWork") or chain.get("network") or ""
            chain_list.append({
                "chain": normalize_chain(raw_chain),
                "contract": str(chain.get("contract", "")).strip(),
                "deposit": bool(chain.get("depositEnable", False)),
                "withdraw": bool(chain.get("withdrawEnable", False)),
            })
        result[coin] = _summarize_chain_list(chain_list)
    return result


def parse_dw_binance(data: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if not isinstance(data, list):
        return result
    for item in data:
        coin = str(item.get("coin", "")).upper()
        chain_list = []
        for chain in item.get("networkList", []) or []:
            chain_list.append({
                "chain": normalize_chain(chain.get("network", "")),
                "contract": str(chain.get("contractAddress", "")).strip(),
                "deposit": bool(chain.get("depositEnable", False)),
                "withdraw": bool(chain.get("withdrawEnable", False)),
            })
        result[coin] = _summarize_chain_list(chain_list)
    return result


def parse_dw_bybit(data: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if not isinstance(data, dict):
        return result
    for item in data.get("result", {}).get("rows", []) or []:
        coin = str(item.get("coin", "")).upper()
        chain_list = []
        for chain in item.get("chains", []) or []:
            chain_list.append({
                "chain": normalize_chain(chain.get("chain", "")),
                "contract": str(chain.get("contractAddress", "")).strip(),
                "deposit": str(chain.get("chainDeposit", "0")) == "1",
                "withdraw": str(chain.get("chainWithdraw", "0")) == "1",
            })
        result[coin] = _summarize_chain_list(chain_list)
    return result


def _summarize_chain_list(chain_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    deposit = any(ch["deposit"] for ch in chain_list)
    withdraw = any(ch["withdraw"] for ch in chain_list)
    return {"deposit": deposit, "withdraw": withdraw, "chains": chain_list}


# ─── Authenticated D/W fetchers ─────────────────────────────────────────


async def _fetch_server_time(
    session: aiohttp.ClientSession, url: str, proxy: Optional[str]
) -> int:
    http_proxy = _http_proxy(proxy)
    try:
        kwargs: Dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=10)}
        if http_proxy:
            kwargs["proxy"] = http_proxy
        async with session.get(url, **kwargs) as response:
            data = await response.json(content_type=None)
            return int(data.get("serverTime", int(time.time() * 1000)))
    except Exception:  # noqa: BLE001
        return int(time.time() * 1000)


async def fetch_mexc_dw(
    session: aiohttp.ClientSession, config: ScannerConfig
) -> List[Dict[str, Any]]:
    if not config.mexc_api_key or not config.mexc_secret_key:
        logger.info("MEXC: API keys not provided, skipping D/W data")
        return []

    server_time = await _fetch_server_time(
        session, "https://api.mexc.com/api/v3/time", config.proxy
    )
    query = f"timestamp={server_time}"
    signature = hmac.new(
        config.mexc_secret_key.encode(), query.encode(), hashlib.sha256
    ).hexdigest()
    url = (
        f"https://api.mexc.com/api/v3/capital/config/getall"
        f"?{query}&signature={signature}"
    )

    http_proxy = _http_proxy(config.proxy)
    try:
        kwargs: Dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=15)}
        if http_proxy:
            kwargs["proxy"] = http_proxy
        headers = {"X-MEXC-APIKEY": config.mexc_api_key, "Content-Type": "application/json"}
        async with session.get(url, headers=headers, **kwargs) as response:
            data = await response.json(content_type=None)
            if isinstance(data, dict) and "code" in data:
                logger.warning("MEXC API error: %s", data.get("msg", data))
                return []
            return data if isinstance(data, list) else []
    except Exception as exc:  # noqa: BLE001
        logger.warning("MEXC request failed: %s", exc)
        return []


async def fetch_binance_dw(
    session: aiohttp.ClientSession, config: ScannerConfig
) -> List[Dict[str, Any]]:
    if not config.binance_api_key or not config.binance_secret_key:
        logger.info("Binance: API keys not provided, skipping D/W data")
        return []

    server_time = await _fetch_server_time(
        session, "https://api.binance.com/api/v3/time", config.proxy
    )
    query = f"timestamp={server_time}"
    signature = hmac.new(
        config.binance_secret_key.encode(), query.encode(), hashlib.sha256
    ).hexdigest()
    url = (
        f"https://api.binance.com/sapi/v1/capital/config/getall"
        f"?{query}&signature={signature}"
    )

    http_proxy = _http_proxy(config.proxy)
    try:
        kwargs: Dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=15)}
        if http_proxy:
            kwargs["proxy"] = http_proxy
        headers = {"X-MBX-APIKEY": config.binance_api_key}
        async with session.get(url, headers=headers, **kwargs) as response:
            data = await response.json(content_type=None)
            if isinstance(data, dict) and ("code" in data or "msg" in data):
                logger.warning("Binance API error: %s", data.get("msg", data))
                return []
            return data if isinstance(data, list) else []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Binance request failed: %s", exc)
        return []


async def fetch_bybit_dw(
    session: aiohttp.ClientSession, config: ScannerConfig
) -> Dict[str, Any]:
    if not config.bybit_api_key or not config.bybit_secret_key:
        logger.info("Bybit: API keys not provided, skipping D/W data")
        return {}

    http_proxy = _http_proxy(config.proxy)

    # Bybit-specific server time fetch (returns string)
    timestamp_str = str(int(time.time() * 1000))
    try:
        kwargs: Dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=10)}
        if http_proxy:
            kwargs["proxy"] = http_proxy
        async with session.get(
            "https://api.bybit.com/v5/market/time", **kwargs
        ) as response:
            data = await response.json(content_type=None)
            timestamp_str = str(data["result"]["timeNano"])[:13]
    except Exception:  # noqa: BLE001
        pass

    recv_window = "5000"
    param_str = f"{timestamp_str}{config.bybit_api_key}{recv_window}"
    signature = hmac.new(
        config.bybit_secret_key.encode(), param_str.encode(), hashlib.sha256
    ).hexdigest()

    headers = {
        "X-BAPI-API-KEY": config.bybit_api_key,
        "X-BAPI-TIMESTAMP": timestamp_str,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": signature,
    }
    try:
        kwargs = {"timeout": aiohttp.ClientTimeout(total=15)}
        if http_proxy:
            kwargs["proxy"] = http_proxy
        async with session.get(
            "https://api.bybit.com/v5/asset/coin/query-info",
            headers=headers,
            **kwargs,
        ) as response:
            data = await response.json(content_type=None)
            if data.get("retCode") != 0:
                logger.warning("Bybit API error: %s", data)
                return {}
            return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("Bybit request failed: %s", exc)
        return {}


# ─── High-level loaders ─────────────────────────────────────────────────


async def load_deposit_withdraw(
    session: aiohttp.ClientSession, config: ScannerConfig
) -> Dict[str, Dict[str, Any]]:
    """Load deposit/withdraw status for every supported exchange."""
    http_proxy = _http_proxy(config.proxy)

    dw_sources = {
        "Bitget":  ("https://api.bitget.com/api/v2/spot/public/coins", ["data"]),
        "KuCoin":  ("https://api.kucoin.com/api/v3/currencies", ["data"]),
        "Huobi":   ("https://api.huobi.pro/v2/reference/currencies", ["data"]),
        "Gateio":  ("https://api.gateio.ws/api/v4/spot/currencies", None),
        "XT":      ("https://sapi.xt.com/v4/public/wallet/support/currency", ["result"]),
    }

    parsers = {
        "Bitget": parse_dw_bitget,
        "KuCoin": parse_dw_kucoin,
        "Huobi":  parse_dw_huobi,
        "Gateio": parse_dw_gateio,
        "XT":     parse_dw_xt,
    }

    tasks: Dict[str, "asyncio.Future[Any]"] = {}
    for name, (url, path) in dw_sources.items():
        tasks[name] = asyncio.ensure_future(
            fetch_json(session, url, path, proxy=http_proxy)
        )

    tasks["MEXC"] = asyncio.ensure_future(fetch_mexc_dw(session, config))
    tasks["Binance"] = asyncio.ensure_future(fetch_binance_dw(session, config))
    tasks["Bybit"] = asyncio.ensure_future(fetch_bybit_dw(session, config))

    names = list(tasks.keys())
    responses = await asyncio.gather(*tasks.values())

    auth_parsers = {
        "MEXC": parse_dw_mexc,
        "Binance": parse_dw_binance,
        "Bybit": parse_dw_bybit,
    }

    dw_info: Dict[str, Dict[str, Any]] = {}
    for name, data in zip(names, responses):
        parser = auth_parsers.get(name) or parsers.get(name)
        dw_info[name] = parser(data) if parser else {}
        logger.debug("%s: %d coins with D/W info", name, len(dw_info[name]))

    for ex in ("BingX", "OKX", "Blofin", "Asterdex", "Hyperliquid"):
        dw_info.setdefault(ex, {})

    return dw_info


async def load_tickers(
    session: aiohttp.ClientSession, config: ScannerConfig
) -> Dict[str, Dict[str, Dict[str, float]]]:
    """Load tickers from all supported exchanges in parallel."""

    sources = {
        "Binance":  ("https://api.binance.com/api/v3/ticker/24hr",
                     None, ("symbol", "lastPrice", "quoteVolume")),
        "MEXC":     ("https://api.mexc.com/api/v3/ticker/24hr",
                     None, ("symbol", "lastPrice", "quoteVolume")),
        "BingX":    ("https://open-api.bingx.com/openApi/spot/v1/ticker/24hr",
                     ["data"], ("symbol", "lastPrice", "quoteVolume")),
        "Bitget":   ("https://api.bitget.com/api/v2/spot/market/tickers",
                     ["data"], ("symbol", "lastPr", "quoteVolume")),
        "Bybit":    ("https://api.bybit.com/v5/market/tickers?category=spot",
                     ["result", "list"], ("symbol", "lastPrice", "turnover24h")),
        "OKX":      ("https://www.okx.com/api/v5/market/tickers?instType=SPOT",
                     ["data"], ("instId", "last", "volCcy24h")),
        "KuCoin":   ("https://api.kucoin.com/api/v1/market/allTickers",
                     ["data", "ticker"], ("symbol", "last", "volValue")),
        "Huobi":    ("https://api.huobi.pro/market/tickers",
                     ["data"], ("symbol", "close", "vol")),
        "Gateio":   ("https://api.gateio.ws/api/v4/spot/tickers",
                     None, ("currency_pair", "last", "quote_volume")),
        "Asterdex": ("https://sapi.asterdex.com/api/v3/ticker/24hr",
                     None, ("symbol", "lastPrice", "quoteVolume")),
        "XT":       ("https://sapi.xt.com/v4/public/ticker",
                     ["result"], ("s", "c", "v")),
    }

    http_proxy = _http_proxy(config.proxy)

    tasks: Dict[str, "asyncio.Future[Any]"] = {}
    for name, (url, path, _) in sources.items():
        tasks[name] = asyncio.ensure_future(
            fetch_json(session, url, path, proxy=http_proxy)
        )

    tasks["Blofin"] = asyncio.ensure_future(
        fetch_json(
            session,
            "https://openapi.blofin.com/api/v1/market/tickers",
            ["data"],
            proxy=http_proxy,
        )
    )
    tasks["Hyperliquid"] = asyncio.ensure_future(
        fetch_json(
            session,
            "https://api.hyperliquid.xyz/info",
            method="POST",
            json_body={"type": "metaAndAssetCtxs"},
            proxy=http_proxy,
        )
    )

    names = list(tasks.keys())
    responses = await asyncio.gather(*tasks.values())

    result: Dict[str, Dict[str, Dict[str, float]]] = {}

    for name, data in zip(names, responses):
        if name == "Blofin":
            parsed = parse_blofin(data, config.min_volume)
        elif name == "Hyperliquid":
            parsed = parse_hyperliquid(data, config.min_volume)
        else:
            _, _, keys = sources[name]
            bid_key, ask_key = BID_ASK_KEYS.get(name, (None, None))
            parsed = parse(
                data, *keys, bid_key=bid_key, ask_key=ask_key,
                min_volume=config.min_volume,
            )

        prices = {pair: price for pair, price, _, _, _ in parsed}
        volumes = {pair: vol for pair, _, vol, _, _ in parsed}
        bids = {pair: bid for pair, _, _, bid, _ in parsed}
        asks = {pair: ask for pair, _, _, _, ask in parsed}
        result[name] = {
            "prices": prices,
            "volumes": volumes,
            "bids": bids,
            "asks": asks,
        }
        logger.info("%s: %d USDT pairs (volume >= %.0f)", name, len(parsed), config.min_volume)

    return result


def build_table(
    all_data: Dict[str, Dict[str, Dict[str, float]]]
) -> Dict[str, Dict[str, Dict[str, float]]]:
    table: Dict[str, Dict[str, Dict[str, float]]] = {}
    for exch, data in all_data.items():
        for pair, price in data["prices"].items():
            row = table.setdefault(pair, {})
            row[exch] = {
                "price": price,
                "volume": data["volumes"].get(pair, 0),
                "bid": data["bids"].get(pair, 0),
                "ask": data["asks"].get(pair, 0),
            }
    return table


def find_arbitrage(
    table: Dict[str, Dict[str, Dict[str, float]]],
    min_spread: float,
    max_spread: float,
) -> List[Dict[str, Any]]:
    opportunities: List[Dict[str, Any]] = []
    for pair, data in table.items():
        if len(data) < 2:
            continue

        asks: Dict[str, float] = {}
        bids: Dict[str, float] = {}
        for exchange, info in data.items():
            ask = info.get("ask", 0)
            bid = info.get("bid", 0)
            if ask > 0:
                asks[exchange] = ask
            if bid > 0:
                bids[exchange] = bid

        if not asks or not bids:
            continue

        buy_ex = min(asks, key=asks.get)
        sell_ex = max(bids, key=bids.get)
        buy_ask = asks[buy_ex]
        sell_bid = bids[sell_ex]

        if buy_ex == sell_ex or buy_ask <= 0:
            continue

        spread = ((sell_bid - buy_ask) / buy_ask) * 100.0
        if spread < min_spread:
            continue
        # max_spread <= 0 disables the upper bound entirely.
        if max_spread > 0 and spread > max_spread:
            continue

        opportunities.append({
            "pair": pair,
            "buy_exchange": buy_ex,
            "buy_price": buy_ask,
            "buy_volume": data[buy_ex]["volume"],
            "sell_exchange": sell_ex,
            "sell_price": sell_bid,
            "sell_volume": data[sell_ex]["volume"],
            "spread": spread,
            "exchanges": len(data),
        })

    opportunities.sort(key=lambda x: x["spread"], reverse=True)
    return opportunities


def find_common_chains(
    dw_info: Dict[str, Dict[str, Any]],
    buy_exchange: str,
    sell_exchange: str,
    coin: str,
) -> List[ChainMatch]:
    buy_data = dw_info.get(buy_exchange, {}).get(coin)
    sell_data = dw_info.get(sell_exchange, {}).get(coin)
    if not buy_data or not sell_data:
        return []

    buy_chains = buy_data.get("chains", [])
    sell_chains = sell_data.get("chains", [])
    if not buy_chains or not sell_chains:
        return []

    matched: List[ChainMatch] = []
    used_sell: set[int] = set()

    # Pass 1: match by contract address
    for bc in buy_chains:
        bc_contract = bc["contract"].lower()
        if not bc_contract:
            continue
        for idx, sc in enumerate(sell_chains):
            if idx in used_sell:
                continue
            sc_contract = sc["contract"].lower()
            if sc_contract and bc_contract == sc_contract:
                matched.append(_make_match(bc, sc, bc["chain"] or sc["chain"], bc["contract"]))
                used_sell.add(idx)
                break

    # Pass 2: match by normalised chain name (when contracts disagree we skip)
    matched_buy_contracts = {m.contract.lower() for m in matched if m.contract}
    for bc in buy_chains:
        if bc["contract"] and bc["contract"].lower() in matched_buy_contracts:
            continue
        for idx, sc in enumerate(sell_chains):
            if idx in used_sell:
                continue
            if bc["chain"] and sc["chain"] and bc["chain"] == sc["chain"]:
                bc_c = bc["contract"].lower().strip()
                sc_c = sc["contract"].lower().strip()
                if bc_c and sc_c and bc_c != sc_c:
                    continue
                matched.append(
                    _make_match(bc, sc, bc["chain"], bc["contract"] or sc["contract"])
                )
                used_sell.add(idx)
                break

    return matched


def _make_match(
    buy_chain: Dict[str, Any],
    sell_chain: Dict[str, Any],
    chain_name: str,
    contract: str,
) -> ChainMatch:
    buy_wd = bool(buy_chain.get("withdraw"))
    sell_dep = bool(sell_chain.get("deposit"))
    return ChainMatch(
        chain=chain_name,
        contract=contract,
        buy_withdraw=buy_wd,
        sell_deposit=sell_dep,
        transfer_ok=buy_wd and sell_dep,
    )


def get_dw_status(
    dw_info: Dict[str, Dict[str, Any]],
    exchange: str,
    coin: str,
) -> Tuple[str, str]:
    ex_data = dw_info.get(exchange, {})
    if not ex_data:
        return "?", "?"
    coin_info = ex_data.get(coin)
    if coin_info is None:
        return "?", "?"
    deposit = "D+" if coin_info["deposit"] else "D-"
    withdraw = "W+" if coin_info["withdraw"] else "W-"
    return deposit, withdraw


# ─── Orderbook analysis ─────────────────────────────────────────────────


def pair_to_ob_symbol(pair: str, exchange: str) -> str:
    if not pair.endswith("USDT"):
        return pair
    base = pair[:-4]
    fmt = {
        "Binance":    f"{base}USDT",
        "MEXC":       f"{base}USDT",
        "Bitget":     f"{base}USDT",
        "Bybit":      f"{base}USDT",
        "Asterdex":   f"{base}USDT",
        "BingX":      f"{base}-USDT",
        "OKX":        f"{base}-USDT",
        "KuCoin":     f"{base}-USDT",
        "Blofin":     f"{base}-USDT",
        "Huobi":      f"{base.lower()}usdt",
        "Gateio":     f"{base}_USDT",
        "XT":         f"{base.lower()}_usdt",
        "Hyperliquid": base,
    }
    return fmt.get(exchange, f"{base}USDT")


def _ob_config(
    exchange: str, symbol: str, limit: int
) -> Optional[Tuple[str, str, Optional[Dict[str, Any]], Any]]:
    def parse_std(data: Dict[str, Any]) -> Tuple[List[List[float]], List[List[float]]]:
        asks = [[safe_float(x[0]), safe_float(x[1])] for x in data.get("asks", [])]
        bids = [[safe_float(x[0]), safe_float(x[1])] for x in data.get("bids", [])]
        return asks, bids

    configs = {
        "Binance": (
            f"https://api.binance.com/api/v3/depth?symbol={symbol}&limit={limit}",
            "GET", None, parse_std),
        "MEXC": (
            f"https://api.mexc.com/api/v3/depth?symbol={symbol}&limit={limit}",
            "GET", None, parse_std),
        "Asterdex": (
            f"https://sapi.asterdex.com/api/v3/depth?symbol={symbol}&limit={limit}",
            "GET", None, parse_std),
        "BingX": (
            f"https://open-api.bingx.com/openApi/spot/v1/market/depth"
            f"?symbol={symbol}&limit={limit}",
            "GET", None,
            lambda d: parse_std(d.get("data", {}))),
        "Bitget": (
            f"https://api.bitget.com/api/v2/spot/market/orderbook"
            f"?symbol={symbol}&limit={limit}",
            "GET", None,
            lambda d: parse_std(d.get("data", {}))),
        "Bybit": (
            f"https://api.bybit.com/v5/market/orderbook"
            f"?category=spot&symbol={symbol}&limit={limit}",
            "GET", None,
            lambda d: (
                [[safe_float(x[0]), safe_float(x[1])]
                 for x in d.get("result", {}).get("a", [])],
                [[safe_float(x[0]), safe_float(x[1])]
                 for x in d.get("result", {}).get("b", [])],
            )),
        "OKX": (
            f"https://www.okx.com/api/v5/market/books?instId={symbol}&sz={limit}",
            "GET", None,
            lambda d: parse_std(d.get("data", [{}])[0]) if d.get("data") else ([], [])),
        "KuCoin": (
            f"https://api.kucoin.com/api/v1/market/orderbook/level2_20?symbol={symbol}",
            "GET", None,
            lambda d: parse_std(d.get("data", {}))),
        "Huobi": (
            f"https://api.huobi.pro/market/depth"
            f"?symbol={symbol}&type=step0&depth={limit}",
            "GET", None,
            lambda d: parse_std(d.get("tick", {}))),
        "Gateio": (
            f"https://api.gateio.ws/api/v4/spot/order_book"
            f"?currency_pair={symbol}&limit={limit}",
            "GET", None, parse_std),
        "XT": (
            f"https://sapi.xt.com/v4/public/depth?symbol={symbol}&limit={limit}",
            "GET", None,
            lambda d: parse_std(d.get("result", {}))),
        "Blofin": (
            f"https://openapi.blofin.com/api/v1/market/books"
            f"?instId={symbol}&sz={limit}",
            "GET", None,
            lambda d: parse_std(d.get("data", [{}])[0]) if d.get("data") else ([], [])),
        "Hyperliquid": (
            "https://api.hyperliquid.xyz/info",
            "POST", {"type": "l2Book", "coin": symbol},
            lambda d: (
                [[safe_float(x["px"]), safe_float(x["sz"])]
                 for x in d.get("levels", [[], []])[1]],
                [[safe_float(x["px"]), safe_float(x["sz"])]
                 for x in d.get("levels", [[], []])[0]],
            )),
    }
    return configs.get(exchange)


async def fetch_orderbook(
    session: aiohttp.ClientSession,
    exchange: str,
    pair: str,
    proxy: Optional[str],
    limit: int,
) -> Tuple[List[List[float]], List[List[float]]]:
    symbol = pair_to_ob_symbol(pair, exchange)
    cfg = _ob_config(exchange, symbol, limit)
    if not cfg:
        return [], []

    url, method, json_body, parser = cfg
    http_proxy = _http_proxy(proxy)

    try:
        kwargs: Dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=10)}
        if http_proxy:
            kwargs["proxy"] = http_proxy
        if method == "POST":
            req = session.post(url, json=json_body, **kwargs)
        else:
            req = session.get(url, **kwargs)
        async with req as response:
            data = await response.json(content_type=None)
            if not isinstance(data, dict):
                return [], []
            return parser(data)
    except Exception:  # noqa: BLE001
        return [], []


def calc_arb_volume(
    buy_asks: List[List[float]],
    sell_bids: List[List[float]],
) -> Tuple[float, float]:
    """Walk the books and accumulate profitable arbitrage volume."""
    asks = sorted([a for a in buy_asks if a[0] > 0 and a[1] > 0], key=lambda x: x[0])
    bids = sorted([b for b in sell_bids if b[0] > 0 and b[1] > 0], key=lambda x: -x[0])

    if not asks or not bids:
        return 0.0, 0.0

    total_cost = 0.0
    total_revenue = 0.0
    ai = bi = 0
    ask_remain = asks[0][1]
    bid_remain = bids[0][1]

    while ai < len(asks) and bi < len(bids):
        ask_price = asks[ai][0]
        bid_price = bids[bi][0]
        if bid_price <= ask_price:
            break

        trade_qty = min(ask_remain, bid_remain)
        total_cost += trade_qty * ask_price
        total_revenue += trade_qty * bid_price

        ask_remain -= trade_qty
        bid_remain -= trade_qty

        if ask_remain <= 0:
            ai += 1
            if ai < len(asks):
                ask_remain = asks[ai][1]
        if bid_remain <= 0:
            bi += 1
            if bi < len(bids):
                bid_remain = bids[bi][1]

    if total_cost <= 0:
        return 0.0, 0.0
    avg_spread = ((total_revenue - total_cost) / total_cost) * 100.0
    return total_cost, avg_spread


async def analyze_orderbooks(
    session: aiohttp.ClientSession,
    opportunities: List[Dict[str, Any]],
    dw_info: Dict[str, Dict[str, Any]],
    config: ScannerConfig,
    limit: int,
) -> Dict[str, OrderbookAnalysis]:
    """Fetch orderbooks for transfer-able opportunities and compute volume."""
    results: Dict[str, OrderbookAnalysis] = {}

    pairs_to_fetch: List[Dict[str, Any]] = []
    for opportunity in opportunities[:limit]:
        coin = coin_from_pair(opportunity["pair"])
        common = find_common_chains(
            dw_info, opportunity["buy_exchange"], opportunity["sell_exchange"], coin
        )
        has_ok = any(match.transfer_ok for match in common)
        if has_ok:
            pairs_to_fetch.append(opportunity)

    if not pairs_to_fetch:
        return results

    logger.info(
        "Fetching orderbooks for %d transfer-able pairs", len(pairs_to_fetch)
    )

    tasks: List["asyncio.Future[Any]"] = []
    task_pairs: List[str] = []
    for opportunity in pairs_to_fetch:
        pair = opportunity["pair"]
        buy_ex = opportunity["buy_exchange"]
        sell_ex = opportunity["sell_exchange"]
        tasks.append(
            asyncio.ensure_future(
                fetch_orderbook(session, buy_ex, pair, config.proxy, config.orderbook_limit)
            )
        )
        tasks.append(
            asyncio.ensure_future(
                fetch_orderbook(session, sell_ex, pair, config.proxy, config.orderbook_limit)
            )
        )
        task_pairs.append(pair)

    responses = await asyncio.gather(*tasks)

    for index, pair in enumerate(task_pairs):
        buy_asks, _ = responses[2 * index]
        _, sell_bids = responses[2 * index + 1]
        volume_usdt, avg_spread = calc_arb_volume(buy_asks, sell_bids)
        profit = volume_usdt * avg_spread / 100.0 if volume_usdt > 0 else 0.0
        results[pair] = OrderbookAnalysis(
            volume_usdt=volume_usdt,
            avg_spread=avg_spread,
            profit_usdt=profit,
        )

    return results


# ─── Public entry point ──────────────────────────────────────────────────


async def run_scan(
    config: Optional[ScannerConfig] = None,
    arbitrage_limit: int = 50,
) -> ScanResult:
    """Run a full scan and return a ``ScanResult``."""
    cfg = config or ScannerConfig()
    started = time.time()

    session = _create_session(cfg.proxy)

    async with session:
        all_data, dw_info = await asyncio.gather(
            load_tickers(session, cfg),
            load_deposit_withdraw(session, cfg),
        )

        table = build_table(all_data)
        total_pairs = len(table)
        multi_exchange_pairs = sum(1 for d in table.values() if len(d) >= 2)

        opportunities = find_arbitrage(table, cfg.min_spread, cfg.max_spread)
        ob_results = await analyze_orderbooks(
            session, opportunities, dw_info, cfg, limit=arbitrage_limit
        )

    # Convert opportunities into structured rows
    rows: List[ArbitrageRow] = []
    for opportunity in opportunities[:arbitrage_limit]:
        pair = opportunity["pair"]
        coin = coin_from_pair(pair)
        buy_dep, buy_wd = get_dw_status(dw_info, opportunity["buy_exchange"], coin)
        sell_dep, sell_wd = get_dw_status(dw_info, opportunity["sell_exchange"], coin)
        common = find_common_chains(
            dw_info, opportunity["buy_exchange"], opportunity["sell_exchange"], coin
        )
        rows.append(
            ArbitrageRow(
                pair=pair,
                coin=coin,
                buy_exchange=opportunity["buy_exchange"],
                buy_price=opportunity["buy_price"],
                buy_volume=opportunity["buy_volume"],
                sell_exchange=opportunity["sell_exchange"],
                sell_price=opportunity["sell_price"],
                sell_volume=opportunity["sell_volume"],
                spread=opportunity["spread"],
                exchanges_count=opportunity["exchanges"],
                buy_deposit=buy_dep,
                buy_withdraw=buy_wd,
                sell_deposit=sell_dep,
                sell_withdraw=sell_wd,
                common_chains=common,
                has_transfer=any(m.transfer_ok for m in common),
                orderbook=ob_results.get(pair),
            )
        )

    exchanges_summary = [
        ExchangeSummary(
            name=name,
            pairs_count=len(all_data.get(name, {}).get("prices", {})),
            dw_count=len(dw_info.get(name, {})),
        )
        for name in EXCHANGES_ORDER
    ]

    return ScanResult(
        finished_at=time.time(),
        duration_seconds=time.time() - started,
        proxy_used=cfg.proxy,
        min_volume=cfg.min_volume,
        min_spread=cfg.min_spread,
        max_spread=cfg.max_spread,
        total_pairs=total_pairs,
        multi_exchange_pairs=multi_exchange_pairs,
        exchanges=exchanges_summary,
        arbitrage=rows,
    )


def scan_result_to_dict(result: ScanResult) -> Dict[str, Any]:
    """Convert a ``ScanResult`` into a JSON-serialisable dict."""
    return asdict(result)
