from __future__ import annotations

from typing import Dict, Type

from .base import BaseExchange, Ticker
from .binance import Binance
from .bitget import Bitget
from .bybit import Bybit
from .gate import Gate
from .kucoin import KuCoin
from .mexc import MEXC
from .okx import OKX

EXCHANGES: Dict[str, Type[BaseExchange]] = {
    "binance": Binance,
    "mexc": MEXC,
    "gate": Gate,
    "bybit": Bybit,
    "okx": OKX,
    "bitget": Bitget,
    "kucoin": KuCoin,
}

EXCHANGE_LABELS: Dict[str, str] = {
    "binance": "Binance",
    "mexc": "MEXC",
    "gate": "Gate.io",
    "bybit": "Bybit",
    "okx": "OKX",
    "bitget": "Bitget",
    "kucoin": "KuCoin",
}

__all__ = ["BaseExchange", "Ticker", "EXCHANGES", "EXCHANGE_LABELS"]
