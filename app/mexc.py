"""Клиент для публичных и приватных эндпоинтов биржи MEXC.

Используются:
- Spot (https://api.mexc.com):
  * GET /api/v3/exchangeInfo — список спот-символов, полное имя, contractAddress;
  * GET /api/v3/ticker/24hr — последняя цена, bid/ask, объём за 24ч;
  * GET /api/v3/capital/config/getall — статусы депозита/вывода и адреса в сетях
    (подписанный приватный эндпоинт, нужны API-ключи).
- Futures (https://contract.mexc.com):
  * GET /api/v1/contract/detail — список бессрочных контрактов;
  * GET /api/v1/contract/ticker — цена, bid1/ask1, 24ч объём, fundingRate;
  * GET /api/v1/contract/funding_rate — фандинг по всем контрактам.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Any
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

SPOT_BASE_URL = "https://api.mexc.com"
FUTURES_BASE_URL = "https://contract.mexc.com"

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=10.0)


class MexcClient:
    """Тонкая обёртка над HTTP-API MEXC."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = api_key or os.getenv("MEXC_API_KEY") or None
        self.api_secret = api_secret or os.getenv("MEXC_API_SECRET") or None
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": "mexc-scanner/0.1 (+https://github.com/evgetos/scanner)"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)

    # ---------- public spot ----------

    async def get_spot_exchange_info(self) -> dict[str, Any]:
        r = await self._client.get(f"{SPOT_BASE_URL}/api/v3/exchangeInfo")
        r.raise_for_status()
        return r.json()

    async def get_spot_24h_tickers(self) -> list[dict[str, Any]]:
        r = await self._client.get(f"{SPOT_BASE_URL}/api/v3/ticker/24hr")
        r.raise_for_status()
        return r.json()

    # ---------- public futures ----------

    async def get_futures_detail(self) -> list[dict[str, Any]]:
        r = await self._client.get(f"{FUTURES_BASE_URL}/api/v1/contract/detail")
        r.raise_for_status()
        payload = r.json()
        return payload.get("data", []) if isinstance(payload, dict) else []

    async def get_futures_tickers(self) -> list[dict[str, Any]]:
        r = await self._client.get(f"{FUTURES_BASE_URL}/api/v1/contract/ticker")
        r.raise_for_status()
        payload = r.json()
        return payload.get("data", []) if isinstance(payload, dict) else []

    async def get_futures_funding_rates(self) -> list[dict[str, Any]]:
        r = await self._client.get(f"{FUTURES_BASE_URL}/api/v1/contract/funding_rate")
        r.raise_for_status()
        payload = r.json()
        return payload.get("data", []) if isinstance(payload, dict) else []

    # ---------- private spot ----------

    def _sign(self, params: dict[str, Any]) -> str:
        assert self.api_secret is not None
        query = urlencode(params, doseq=True)
        sig = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return sig

    async def get_capital_config(self) -> list[dict[str, Any]]:
        """Возвращает список монет с информацией о сетях, депозите/выводе и адресах.

        Требует MEXC_API_KEY/MEXC_API_SECRET. Если ключи не заданы — кидает RuntimeError.
        """
        if not self.has_credentials:
            raise RuntimeError("MEXC API credentials are required for capital/config/getall")

        params: dict[str, Any] = {
            "timestamp": int(time.time() * 1000),
            "recvWindow": 5000,
        }
        params["signature"] = self._sign(params)
        headers = {"X-MEXC-APIKEY": self.api_key or ""}
        r = await self._client.get(
            f"{SPOT_BASE_URL}/api/v3/capital/config/getall",
            params=params,
            headers=headers,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data
        # MEXC иногда возвращает {"code":..., "msg":...} при ошибках
        raise RuntimeError(f"Unexpected capital/config response: {data}")
