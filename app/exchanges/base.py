from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Ticker:
    symbol: str
    last_price: float
    fair_price: float
    volume_24h_usdt: float


class BaseExchange(ABC):
    """Base class for an exchange ticker source.

    Subclasses fetch futures/perpetual tickers and normalise them into the
    ``Ticker`` shape: a unified ``BASE/USDT`` symbol, the last traded price,
    the fair / mark price, and the 24h trading volume denominated in USDT.
    """

    name: str = "base"

    def __init__(self, proxy_url: Optional[str] = None, timeout: float = 30.0):
        self.proxy_url = proxy_url
        self.timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        kwargs = {
            "timeout": self.timeout,
            "headers": {
                "User-Agent": "FairLastScanner/1.0 (+https://github.com/evgetos/scanner)",
                "Accept": "application/json",
            },
        }
        if self.proxy_url:
            kwargs["proxy"] = self.proxy_url
        return httpx.AsyncClient(**kwargs)

    @abstractmethod
    async def fetch_tickers(self) -> List[Ticker]:
        ...

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            if value is None or value == "":
                return default
            return float(value)
        except (TypeError, ValueError):
            return default
