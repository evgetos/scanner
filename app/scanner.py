"""Background scanner that polls a public crypto exchange and detects situations.

The scanner periodically fetches 24h ticker data from Binance, computes recent
price changes and volume surges, and produces a snapshot that the web UI can
poll once per second.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque

import httpx

logger = logging.getLogger(__name__)

# Binance's read-only data mirror. It exposes the same /api/v3/ticker/24hr
# response shape as the main API but is not subject to the same regional
# restrictions, which makes it a safer default for a read-only public scanner.
BINANCE_TICKER_URL = "https://data-api.binance.vision/api/v3/ticker/24hr"

# How many recent price points we keep per symbol for short-term analysis.
HISTORY_LEN = 120

# Default thresholds for situation detection.
SPIKE_PCT_1M = 1.0  # price move over the last ~60 seconds, in percent
VOLUME_SURGE_RATIO = 2.0  # ratio of current 1m volume to median of recent 1m volumes
TOP_N_BY_VOLUME = 30  # how many USDT pairs to track


@dataclass
class SymbolHistory:
    """Rolling history of recent observations for a single trading pair."""

    prices: Deque[tuple[float, float]] = field(
        default_factory=lambda: deque(maxlen=HISTORY_LEN)
    )
    quote_volumes: Deque[tuple[float, float]] = field(
        default_factory=lambda: deque(maxlen=HISTORY_LEN)
    )

    def add(self, ts: float, price: float, quote_volume: float) -> None:
        self.prices.append((ts, price))
        self.quote_volumes.append((ts, quote_volume))

    def price_change_pct(self, window_seconds: float) -> float | None:
        if not self.prices:
            return None
        latest_ts, latest_price = self.prices[-1]
        cutoff = latest_ts - window_seconds
        old_price: float | None = None
        for ts, price in self.prices:
            if ts <= cutoff:
                old_price = price
            else:
                break
        if old_price is None or old_price == 0:
            return None
        return (latest_price - old_price) / old_price * 100.0

    def recent_volume_delta(self, window_seconds: float) -> float | None:
        """Estimate the quote volume traded in the last *window_seconds* seconds.

        We look at the change in the cumulative 24h quote volume between the
        latest observation and the oldest one within the window.
        """
        if len(self.quote_volumes) < 2:
            return None
        latest_ts, latest_qv = self.quote_volumes[-1]
        cutoff = latest_ts - window_seconds
        old_qv: float | None = None
        for ts, qv in self.quote_volumes:
            if ts <= cutoff:
                old_qv = qv
            else:
                break
        if old_qv is None:
            return None
        delta = latest_qv - old_qv
        # When the 24h window rolls over the cumulative volume can decrease;
        # ignore those samples instead of returning bogus numbers.
        if delta < 0:
            return None
        return delta


@dataclass
class TickerSnapshot:
    """A single normalized snapshot of one symbol from the exchange."""

    symbol: str
    price: float
    change_pct_24h: float
    high_24h: float
    low_24h: float
    quote_volume_24h: float


@dataclass
class Situation:
    """A detected market situation."""

    symbol: str
    kind: str
    severity: str
    message: str
    value: float


@dataclass
class ScannerState:
    """Latest scanner output, served as JSON to the frontend."""

    last_updated: float = 0.0
    poll_interval_seconds: float = 1.0
    tickers: list[dict[str, float | str]] = field(default_factory=list)
    situations: list[dict[str, float | str]] = field(default_factory=list)
    error: str | None = None


class MarketScanner:
    """Polls Binance once per second and maintains rolling state in memory."""

    def __init__(
        self,
        poll_interval: float = 1.0,
        top_n: int = TOP_N_BY_VOLUME,
        spike_pct_1m: float = SPIKE_PCT_1M,
        volume_surge_ratio: float = VOLUME_SURGE_RATIO,
        ticker_url: str = BINANCE_TICKER_URL,
    ) -> None:
        self.poll_interval = poll_interval
        self.top_n = top_n
        self.spike_pct_1m = spike_pct_1m
        self.volume_surge_ratio = volume_surge_ratio
        self.ticker_url = ticker_url

        self._history: dict[str, SymbolHistory] = {}
        self._tracked_symbols: list[str] = []
        self._last_state = ScannerState(poll_interval_seconds=poll_interval)
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._client = httpx.AsyncClient(timeout=10.0)
        self._task = asyncio.create_task(self._run(), name="market-scanner")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_state(self) -> ScannerState:
        async with self._lock:
            return self._last_state

    async def _run(self) -> None:
        while True:
            started = time.monotonic()
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception("Scanner tick failed: %s", exc)
                async with self._lock:
                    self._last_state = ScannerState(
                        last_updated=time.time(),
                        poll_interval_seconds=self.poll_interval,
                        tickers=self._last_state.tickers,
                        situations=self._last_state.situations,
                        error=str(exc),
                    )
            elapsed = time.monotonic() - started
            sleep_for = max(0.0, self.poll_interval - elapsed)
            await asyncio.sleep(sleep_for)

    async def _tick(self) -> None:
        snapshots = await self._fetch_snapshots()
        if not snapshots:
            return
        now = time.time()
        if not self._tracked_symbols:
            ranked = sorted(
                snapshots, key=lambda s: s.quote_volume_24h, reverse=True
            )
            self._tracked_symbols = [s.symbol for s in ranked[: self.top_n]]
        tracked_set = set(self._tracked_symbols)
        tracked_snapshots = [s for s in snapshots if s.symbol in tracked_set]
        for snap in tracked_snapshots:
            history = self._history.setdefault(snap.symbol, SymbolHistory())
            history.add(now, snap.price, snap.quote_volume_24h)
        situations = self._detect_situations(tracked_snapshots)
        tracked_snapshots.sort(key=lambda s: s.quote_volume_24h, reverse=True)
        async with self._lock:
            self._last_state = ScannerState(
                last_updated=now,
                poll_interval_seconds=self.poll_interval,
                tickers=[self._ticker_to_dict(s) for s in tracked_snapshots],
                situations=[self._situation_to_dict(s) for s in situations],
                error=None,
            )

    async def _fetch_snapshots(self) -> list[TickerSnapshot]:
        assert self._client is not None
        response = await self._client.get(self.ticker_url)
        response.raise_for_status()
        payload = response.json()
        snapshots: list[TickerSnapshot] = []
        for entry in payload:
            symbol = entry.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            try:
                snapshots.append(
                    TickerSnapshot(
                        symbol=symbol,
                        price=float(entry["lastPrice"]),
                        change_pct_24h=float(entry["priceChangePercent"]),
                        high_24h=float(entry["highPrice"]),
                        low_24h=float(entry["lowPrice"]),
                        quote_volume_24h=float(entry["quoteVolume"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return snapshots

    def _detect_situations(
        self, snapshots: list[TickerSnapshot]
    ) -> list[Situation]:
        situations: list[Situation] = []
        for snap in snapshots:
            history = self._history.get(snap.symbol)
            if history is None:
                continue
            change_1m = history.price_change_pct(60.0)
            if change_1m is not None and abs(change_1m) >= self.spike_pct_1m:
                direction = "up" if change_1m > 0 else "down"
                severity = (
                    "high"
                    if abs(change_1m) >= self.spike_pct_1m * 2
                    else "medium"
                )
                situations.append(
                    Situation(
                        symbol=snap.symbol,
                        kind=f"price_spike_{direction}_1m",
                        severity=severity,
                        message=(
                            f"{snap.symbol} moved {change_1m:+.2f}% in the last minute"
                        ),
                        value=change_1m,
                    )
                )
            recent_qv = history.recent_volume_delta(60.0)
            baseline_qv = history.recent_volume_delta(600.0)
            if (
                recent_qv is not None
                and baseline_qv is not None
                and baseline_qv > 0
            ):
                # baseline_qv covers a 10-minute window, so the per-minute
                # baseline is baseline_qv / 10. Compare the last minute against
                # that to detect surges.
                per_minute_baseline = baseline_qv / 10.0
                if per_minute_baseline > 0:
                    ratio = recent_qv / per_minute_baseline
                    if ratio >= self.volume_surge_ratio:
                        severity = (
                            "high"
                            if ratio >= self.volume_surge_ratio * 2
                            else "medium"
                        )
                        situations.append(
                            Situation(
                                symbol=snap.symbol,
                                kind="volume_surge",
                                severity=severity,
                                message=(
                                    f"{snap.symbol} 1m volume is {ratio:.1f}x its 10m baseline"
                                ),
                                value=ratio,
                            )
                        )
            if snap.high_24h > 0 and snap.price >= snap.high_24h:
                situations.append(
                    Situation(
                        symbol=snap.symbol,
                        kind="new_24h_high",
                        severity="medium",
                        message=f"{snap.symbol} touched a new 24h high at {snap.price:g}",
                        value=snap.price,
                    )
                )
            if snap.low_24h > 0 and snap.price <= snap.low_24h:
                situations.append(
                    Situation(
                        symbol=snap.symbol,
                        kind="new_24h_low",
                        severity="medium",
                        message=f"{snap.symbol} touched a new 24h low at {snap.price:g}",
                        value=snap.price,
                    )
                )
        severity_rank = {"high": 0, "medium": 1, "low": 2}
        situations.sort(
            key=lambda s: (severity_rank.get(s.severity, 3), -abs(s.value))
        )
        return situations

    @staticmethod
    def _ticker_to_dict(snap: TickerSnapshot) -> dict[str, float | str]:
        return {
            "symbol": snap.symbol,
            "price": snap.price,
            "change_pct_24h": snap.change_pct_24h,
            "high_24h": snap.high_24h,
            "low_24h": snap.low_24h,
            "quote_volume_24h": snap.quote_volume_24h,
        }

    @staticmethod
    def _situation_to_dict(situation: Situation) -> dict[str, float | str]:
        return {
            "symbol": situation.symbol,
            "kind": situation.kind,
            "severity": situation.severity,
            "message": situation.message,
            "value": situation.value,
        }
