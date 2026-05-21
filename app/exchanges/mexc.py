from __future__ import annotations

from typing import List

from .base import BaseExchange, Ticker


class MEXC(BaseExchange):
    """MEXC USDT-margined perpetual contracts.

    Docs: https://mexcdevelop.github.io/apidocs/contract_v1_en/
    """

    name = "mexc"

    async def fetch_tickers(self) -> List[Ticker]:
        url = "https://contract.mexc.com/api/v1/contract/ticker"
        async with self._client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.json()

        if not payload.get("success", False):
            raise RuntimeError(f"MEXC: {payload.get('message') or payload.get('code')}")

        out: List[Ticker] = []
        for item in payload.get("data", []) or []:
            raw = item.get("symbol", "")
            if "_USDT" not in raw:
                continue
            base = raw.split("_USDT")[0]
            if not base:
                continue
            last = self._safe_float(item.get("lastPrice"))
            fair = self._safe_float(item.get("fairPrice"))
            volume_usdt = self._safe_float(item.get("amount24"))
            if last <= 0 or fair <= 0:
                continue
            out.append(
                Ticker(
                    symbol=f"{base}/USDT",
                    last_price=last,
                    fair_price=fair,
                    volume_24h_usdt=volume_usdt,
                )
            )
        return out
