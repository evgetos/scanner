from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple

from .exchanges import EXCHANGES
from .exchanges.base import Ticker
from .models import ExchangeStatus, ScanResult, ScannerSettings, TickerAnomaly
from .settings import SettingsStore

logger = logging.getLogger(__name__)


class Scanner:
    """Background scanner that periodically compares last vs fair price."""

    def __init__(self, settings_store: SettingsStore):
        self.settings_store = settings_store
        self._last_result = ScanResult(
            last_scan_at=None,
            anomalies=[],
            exchange_status=[
                ExchangeStatus(name=name, enabled=True) for name in EXCHANGES
            ],
        )
        self._result_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    # -- public API ----------------------------------------------------------

    @property
    def last_result(self) -> ScanResult:
        return self._last_result

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_forever(), name="scanner-loop")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        task = self._task
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
        self._task = None

    def kick(self) -> None:
        """Wake the loop early (used after settings update)."""
        self._wake.set()

    async def scan_once(self) -> ScanResult:
        settings = self.settings_store.get()
        return await self._do_scan(settings)

    # -- internals -----------------------------------------------------------

    async def _run_forever(self) -> None:
        while not self._stop.is_set():
            settings = self.settings_store.get()
            try:
                await self._do_scan(settings)
            except Exception:
                logger.exception("Scan iteration failed unexpectedly")

            interval = max(1, settings.scan_interval_sec)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            # Clear after waiting so a kick() that fires during _do_scan() is
            # preserved and consumed by the next wait_for (which returns
            # immediately), instead of being erased.
            self._wake.clear()

    async def _do_scan(self, settings: ScannerSettings) -> ScanResult:
        statuses: Dict[str, ExchangeStatus] = {
            name: ExchangeStatus(
                name=name,
                enabled=settings.exchanges[name].enabled,
            )
            for name in EXCHANGES
        }

        enabled = [n for n in EXCHANGES if settings.exchanges[n].enabled]

        async def run_one(name: str) -> Tuple[str, List[Ticker], Optional[str]]:
            cls = EXCHANGES[name]
            client = cls(proxy_url=settings.proxy_url)
            try:
                tickers = await client.fetch_tickers()
                return name, tickers, None
            except Exception as exc:  # noqa: BLE001 — propagate via status
                logger.warning("Exchange %s failed: %s", name, exc)
                return name, [], f"{type(exc).__name__}: {exc}"

        if enabled:
            gathered = await asyncio.gather(*(run_one(n) for n in enabled))
        else:
            gathered = []

        anomalies: List[TickerAnomaly] = []
        now = time.time()

        for name, tickers, err in gathered:
            status = statuses[name]
            status.last_scan_at = now
            status.last_error = err
            status.ticker_count = len(tickers)
            local_anom = 0
            for t in tickers:
                if t.fair_price <= 0:
                    continue
                if t.volume_24h_usdt < settings.min_volume_usdt:
                    continue
                spread = (t.last_price - t.fair_price) / t.fair_price * 100.0
                if abs(spread) < settings.min_spread_pct:
                    continue
                anomalies.append(
                    TickerAnomaly(
                        exchange=name,
                        symbol=t.symbol,
                        last_price=t.last_price,
                        fair_price=t.fair_price,
                        spread_pct=spread,
                        volume_24h_usdt=t.volume_24h_usdt,
                    )
                )
                local_anom += 1
            status.anomaly_count = local_anom

        anomalies.sort(key=lambda a: abs(a.spread_pct), reverse=True)

        result = ScanResult(
            last_scan_at=now,
            anomalies=anomalies,
            exchange_status=[statuses[n] for n in EXCHANGES],
        )
        async with self._result_lock:
            self._last_result = result
        return result
