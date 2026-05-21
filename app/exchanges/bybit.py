from __future__ import annotations

from typing import List

from .base import BaseExchange, Ticker


class Bybit(BaseExchange):
    """Bybit V5 linear (USDT) perpetuals.

    Docs: https://bybit-exchange.github.io/docs/v5/market/tickers
    """

    name = "bybit"

    async def fetch_tickers(self) -> List[Ticker]:
        url = "https://api.bybit.com/v5/market/tickers"
        async with self._client() as client:
            resp = await client.get(url, params={"category": "linear"})
            resp.raise_for_status()
            payload = resp.json()

        if payload.get("retCode") not in (0, "0", None):
            raise RuntimeError(
                f"Bybit: retCode={payload.get('retCode')} {payload.get('retMsg')}"
            )

        out: List[Ticker] = []
        for item in payload.get("result", {}).get("list", []) or []:
            raw = item.get("symbol", "")
            if not raw.endswith("USDT"):
                continue
            base = raw[:-4]
            if not base:
                continue
            last = self._safe_float(item.get("lastPrice"))
            mark = self._safe_float(item.get("markPrice"))
            volume_usdt = self._safe_float(item.get("turnover24h"))
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
