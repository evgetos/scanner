from __future__ import annotations

from typing import List

from .base import BaseExchange, Ticker


class KuCoin(BaseExchange):
    """KuCoin USDT-margined futures.

    Docs: https://www.kucoin.com/docs/rest/futures-trading/market-data/get-symbols-list
    """

    name = "kucoin"

    async def fetch_tickers(self) -> List[Ticker]:
        url = "https://api-futures.kucoin.com/api/v1/contracts/active"
        async with self._client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.json()

        if payload.get("code") != "200000":
            raise RuntimeError(
                f"KuCoin: code={payload.get('code')} {payload.get('msg')}"
            )

        out: List[Ticker] = []
        for item in payload.get("data", []) or []:
            if item.get("quoteCurrency") != "USDT":
                continue
            if item.get("isInverse"):
                continue
            base = item.get("baseCurrency") or ""
            # KuCoin uses XBT for bitcoin — normalise for cross-exchange comparison.
            display_base = "BTC" if base == "XBT" else base
            if not display_base:
                continue
            last = self._safe_float(item.get("lastTradePrice"))
            mark = self._safe_float(item.get("markPrice"))
            volume_usdt = self._safe_float(item.get("turnoverOf24h"))
            if last <= 0 or mark <= 0:
                continue
            out.append(
                Ticker(
                    symbol=f"{display_base}/USDT",
                    last_price=last,
                    fair_price=mark,
                    volume_24h_usdt=volume_usdt,
                )
            )
        return out
