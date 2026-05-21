from __future__ import annotations

from typing import List

from .base import BaseExchange, Ticker


class Bitget(BaseExchange):
    """Bitget USDT-margined mix (perpetual) futures.

    Docs: https://www.bitget.com/api-doc/contract/market/Get-All-Symbol-Ticker
    """

    name = "bitget"

    async def fetch_tickers(self) -> List[Ticker]:
        url = "https://api.bitget.com/api/v2/mix/market/tickers"
        async with self._client() as client:
            resp = await client.get(url, params={"productType": "USDT-FUTURES"})
            resp.raise_for_status()
            payload = resp.json()

        if payload.get("code") not in ("00000", 0, "0", None):
            raise RuntimeError(
                f"Bitget: code={payload.get('code')} {payload.get('msg')}"
            )

        out: List[Ticker] = []
        for item in payload.get("data", []) or []:
            raw = item.get("symbol", "")
            if not raw.endswith("USDT"):
                continue
            base = raw[:-4]
            if not base:
                continue
            last = self._safe_float(item.get("lastPr"))
            mark = self._safe_float(item.get("markPrice"))
            volume_usdt = self._safe_float(
                item.get("usdtVolume") or item.get("quoteVolume")
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
