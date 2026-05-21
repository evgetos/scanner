from __future__ import annotations

import asyncio
from typing import List

from .base import BaseExchange, Ticker


class Binance(BaseExchange):
    """Binance USDT-margined futures (perpetual).

    Docs:
      - https://binance-docs.github.io/apidocs/futures/en/#24hr-ticker-price-change-statistics
      - https://binance-docs.github.io/apidocs/futures/en/#mark-price

    No combined ticker endpoint includes both ``lastPrice`` and ``markPrice``,
    so we hit /ticker/24hr and /premiumIndex in parallel and join on symbol.
    """

    name = "binance"

    async def fetch_tickers(self) -> List[Ticker]:
        async with self._client() as client:
            tickers_resp, mark_resp = await asyncio.gather(
                client.get("https://fapi.binance.com/fapi/v1/ticker/24hr"),
                client.get("https://fapi.binance.com/fapi/v1/premiumIndex"),
            )
            tickers_resp.raise_for_status()
            mark_resp.raise_for_status()
            tickers_data = tickers_resp.json()
            mark_data = mark_resp.json()

        # Binance occasionally returns HTTP 200 with a {code, msg} error
        # envelope (e.g. geo-restriction, rate-limit warnings). Surface those
        # as exchange errors instead of parsing into empty results.
        if isinstance(tickers_data, dict):
            raise RuntimeError(
                f"Binance tickers: {tickers_data.get('msg') or tickers_data.get('code')}"
            )
        if isinstance(mark_data, dict):
            raise RuntimeError(
                f"Binance mark: {mark_data.get('msg') or mark_data.get('code')}"
            )

        marks = {
            m["symbol"]: self._safe_float(m.get("markPrice"))
            for m in mark_data
            if "symbol" in m
        }

        out: List[Ticker] = []
        for item in tickers_data:
            raw = item.get("symbol", "")
            # Skip dated delivery futures (e.g. BTCUSDT_240329) — only perpetuals.
            if "_" in raw:
                continue
            if not raw.endswith("USDT"):
                continue
            base = raw[:-4]
            if not base:
                continue
            last = self._safe_float(item.get("lastPrice"))
            mark = marks.get(raw, 0.0)
            volume_usdt = self._safe_float(item.get("quoteVolume"))
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
