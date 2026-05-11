"""Агрегация данных MEXC в формат, удобный для веб-интерфейса."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.mexc import MexcClient

logger = logging.getLogger(__name__)


@dataclass
class CoinWalletInfo:
    """Сводка по монете из приватного capital/config.

    Содержит статусы депозита/вывода и список сетей с контрактами.
    """

    deposit_enable: bool
    withdraw_enable: bool
    networks: list[dict[str, Any]]

    @property
    def primary_contract(self) -> str:
        """Первый непустой адрес контракта (для отображения в таблице)."""
        for net in self.networks:
            contract = (net.get("contract") or "").strip()
            if contract:
                return contract
        return ""


def _coin_wallet_index(capital: list[dict[str, Any]]) -> dict[str, CoinWalletInfo]:
    """Сворачивает /capital/config/getall в индекс coin -> CoinWalletInfo."""
    index: dict[str, CoinWalletInfo] = {}
    for entry in capital:
        coin = entry.get("coin")
        if not coin:
            continue
        networks_raw = entry.get("networkList") or []
        deposit = any(bool(n.get("depositEnable")) for n in networks_raw)
        withdraw = any(bool(n.get("withdrawEnable")) for n in networks_raw)
        networks = [
            {
                "network": n.get("network") or n.get("netWork") or "",
                "contract": n.get("contract") or "",
                "depositEnable": bool(n.get("depositEnable")),
                "withdrawEnable": bool(n.get("withdrawEnable")),
                "withdrawFee": n.get("withdrawFee"),
            }
            for n in networks_raw
        ]
        index[coin] = CoinWalletInfo(
            deposit_enable=deposit,
            withdraw_enable=withdraw,
            networks=networks,
        )
    return index


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def build_spot_rows(client: MexcClient) -> list[dict[str, Any]]:
    """Возвращает строки таблицы для вкладки «Спот»."""
    exchange_info_task = client.get_spot_exchange_info()
    tickers_task = client.get_spot_24h_tickers()

    if client.has_credentials:
        capital_task: asyncio.Task[list[dict[str, Any]]] | None = asyncio.create_task(
            client.get_capital_config()
        )
    else:
        capital_task = None

    exchange_info, tickers = await asyncio.gather(exchange_info_task, tickers_task)

    wallet_index: dict[str, CoinWalletInfo] = {}
    if capital_task is not None:
        try:
            capital = await capital_task
            wallet_index = _coin_wallet_index(capital)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch capital/config/getall: %s", exc)

    ticker_by_symbol: dict[str, dict[str, Any]] = {t["symbol"]: t for t in tickers}

    rows: list[dict[str, Any]] = []
    symbols = exchange_info.get("symbols", []) if isinstance(exchange_info, dict) else []
    for sym in symbols:
        symbol = sym.get("symbol")
        if not symbol:
            continue
        base = sym.get("baseAsset") or ""
        quote = sym.get("quoteAsset") or ""

        ticker = ticker_by_symbol.get(symbol, {})
        wallet = wallet_index.get(base)

        contract_from_exchange = (sym.get("contractAddress") or "").strip()
        contract = contract_from_exchange or (wallet.primary_contract if wallet else "")

        deposit_enable = wallet.deposit_enable if wallet else None
        withdraw_enable = wallet.withdraw_enable if wallet else None

        rows.append(
            {
                "symbol": symbol,
                "base": base,
                "quote": quote,
                "fullName": sym.get("fullName") or "",
                "status": sym.get("status"),
                "lastPrice": _safe_float(ticker.get("lastPrice")),
                "bidPrice": _safe_float(ticker.get("bidPrice")),
                "askPrice": _safe_float(ticker.get("askPrice")),
                "volumeBase": _safe_float(ticker.get("volume")),
                "volumeQuote": _safe_float(ticker.get("quoteVolume")),
                "priceChangePercent": _safe_float(ticker.get("priceChangePercent")),
                "depositEnable": deposit_enable,
                "withdrawEnable": withdraw_enable,
                "contractAddress": contract,
                "networks": wallet.networks if wallet else [],
            }
        )

    rows.sort(key=lambda r: (r["volumeQuote"] or 0.0), reverse=True)
    return rows


async def build_futures_rows(client: MexcClient) -> list[dict[str, Any]]:
    """Возвращает строки таблицы для вкладки «Фьючерсы» (бессрочные)."""
    details_task = client.get_futures_detail()
    tickers_task = client.get_futures_tickers()
    funding_task = client.get_futures_funding_rates()

    if client.has_credentials:
        capital_task: asyncio.Task[list[dict[str, Any]]] | None = asyncio.create_task(
            client.get_capital_config()
        )
    else:
        capital_task = None

    details, tickers, funding = await asyncio.gather(details_task, tickers_task, funding_task)

    wallet_index: dict[str, CoinWalletInfo] = {}
    if capital_task is not None:
        try:
            capital = await capital_task
            wallet_index = _coin_wallet_index(capital)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch capital/config/getall for futures: %s", exc)

    ticker_by_symbol = {t["symbol"]: t for t in tickers}
    funding_by_symbol = {f["symbol"]: f for f in funding}

    rows: list[dict[str, Any]] = []
    for d in details:
        # MEXC возвращает в detail все контракты, включая выключенные/скрытые.
        if d.get("isHidden"):
            continue
        symbol = d.get("symbol")
        if not symbol:
            continue
        base = d.get("baseCoin") or ""
        quote = d.get("quoteCoin") or ""

        ticker = ticker_by_symbol.get(symbol, {})
        fund = funding_by_symbol.get(symbol, {})
        wallet = wallet_index.get(base)

        rows.append(
            {
                "symbol": symbol,
                "base": base,
                "quote": quote,
                "displayName": d.get("displayNameEn") or d.get("displayName") or "",
                "state": d.get("state"),
                "lastPrice": _safe_float(ticker.get("lastPrice")),
                "bidPrice": _safe_float(ticker.get("bid1")),
                "askPrice": _safe_float(ticker.get("ask1")),
                "indexPrice": _safe_float(ticker.get("indexPrice")),
                "fairPrice": _safe_float(ticker.get("fairPrice")),
                "volume24": _safe_float(ticker.get("volume24")),
                "amount24": _safe_float(ticker.get("amount24")),
                "riseFallRate": _safe_float(ticker.get("riseFallRate")),
                "fundingRate": _safe_float(
                    fund.get("fundingRate") if "fundingRate" in fund else ticker.get("fundingRate")
                ),
                "nextSettleTime": fund.get("nextSettleTime"),
                "collectCycle": fund.get("collectCycle"),
                "depositEnable": wallet.deposit_enable if wallet else None,
                "withdrawEnable": wallet.withdraw_enable if wallet else None,
                "contractAddress": wallet.primary_contract if wallet else "",
                "networks": wallet.networks if wallet else [],
            }
        )

    rows.sort(key=lambda r: (r["amount24"] or 0.0), reverse=True)
    return rows
