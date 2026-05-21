from __future__ import annotations

from typing import List

from .base import BaseExchange, Ticker


class Gate(BaseExchange):
    """Gate.io USDT-margined futures.

    Docs: https://www.gate.io/docs/developers/apiv4/#futures
    """

    name = "gate"

    async def fetch_tickers(self) -> List[Ticker]:
        url = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
        async with self._client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        out: List[Ticker] = []
        for item in data or []:
            contract = item.get("contract", "")
            if "_USDT" not in contract:
                continue
            base = contract.split("_USDT")[0]
            if not base:
                continue
            last = self._safe_float(item.get("last"))
            mark = self._safe_float(item.get("mark_price"))
            volume_usdt = self._safe_float(
                item.get("volume_24h_quote") or item.get("volume_24h_settle")
            )
            if last <= 0 or mark <= 0:
                continue
            out.append(
                Ticker(
                    symbol=f"{base}/USDT",
                    last_price=last,
                    fair_price=mark,
                    volume_24h_usdt=volume_usdt,
                )
            )
        return out
