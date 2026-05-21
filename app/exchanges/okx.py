from __future__ import annotations

import asyncio
from typing import List

from .base import BaseExchange, Ticker


class OKX(BaseExchange):
    """OKX USDT-margined perpetual swaps.

    Docs:
      - https://www.okx.com/docs-v5/en/#public-data-rest-api-get-tickers
      - https://www.okx.com/docs-v5/en/#public-data-rest-api-get-mark-price

    Note: OKX doesn't return ``markPx`` in the tickers endpoint, so we fetch
    mark prices in parallel and join on ``instId``. Volume is reported in
    the base currency for SWAP, so we multiply by last price to get USDT.
    """

    name = "okx"

    async def fetch_tickers(self) -> List[Ticker]:
        async with self._client() as client:
            tickers_resp, mark_resp = await asyncio.gather(
                client.get(
                    "https://www.okx.com/api/v5/market/tickers",
                    params={"instType": "SWAP"},
                ),
                client.get(
                    "https://www.okx.com/api/v5/public/mark-price",
                    params={"instType": "SWAP"},
                ),
            )
            tickers_resp.raise_for_status()
            mark_resp.raise_for_status()
            tickers_payload = tickers_resp.json()
            mark_payload = mark_resp.json()

        if tickers_payload.get("code") not in ("0", 0, None):
            raise RuntimeError(
                f"OKX tickers: code={tickers_payload.get('code')} {tickers_payload.get('msg')}"
            )
        if mark_payload.get("code") not in ("0", 0, None):
            raise RuntimeError(
                f"OKX mark: code={mark_payload.get('code')} {mark_payload.get('msg')}"
            )

        marks = {
            m["instId"]: self._safe_float(m.get("markPx"))
            for m in mark_payload.get("data", []) or []
        }

        out: List[Ticker] = []
        for item in tickers_payload.get("data", []) or []:
            inst_id = item.get("instId", "")
            if not inst_id.endswith("-USDT-SWAP"):
                continue
            base = inst_id[: -len("-USDT-SWAP")]
            if not base:
                continue
            last = self._safe_float(item.get("last"))
            mark = marks.get(inst_id, 0.0)
            # volCcy24h on USDT-SWAP is in the base currency; convert to USDT.
            volume_base = self._safe_float(item.get("volCcy24h"))
            volume_usdt = volume_base * last
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
