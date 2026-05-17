"""FastAPI entry point for the crypto arbitrage scanner web UI."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Deque, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import AppConfig, ScannerConfig
from .history_store import HistoryStore
from .max_notifier import MaxNotifier
from .scanner_core import run_scan, scan_result_to_dict, EXCHANGES_ORDER

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).resolve().parent / "static"

APP_CONFIG = AppConfig()


def _row_to_history_event(row: Dict[str, Any], timestamp: float) -> Dict[str, Any]:
    """Project an arbitrage row dict to a compact history event."""
    ob = row.get("orderbook") or {}
    return {
        "timestamp": timestamp,
        "pair": row.get("pair"),
        "coin": row.get("coin"),
        "buy_exchange": row.get("buy_exchange"),
        "sell_exchange": row.get("sell_exchange"),
        "buy_price": row.get("buy_price"),
        "sell_price": row.get("sell_price"),
        "spread": row.get("spread"),
        "buy_volume": row.get("buy_volume"),
        "sell_volume": row.get("sell_volume"),
        "ob_volume_usdt": ob.get("volume_usdt") if ob else None,
        "ob_profit_usdt": ob.get("profit_usdt") if ob else None,
        # Budget-capped variant ("profit if I had spent exactly $N").
        "ob_budget_usdt": ob.get("budget_usdt") if ob else None,
        "ob_profit_at_budget_usdt": ob.get("profit_at_budget_usdt") if ob else None,
        "ob_filled_usdt": ob.get("filled_usdt") if ob else None,
        "has_transfer": row.get("has_transfer", False),
    }


class ScannerState:
    """Mutable in-memory state for the latest scan + background loop."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.scanning: bool = False
        self.paused: bool = False
        self.last_started_at: Optional[float] = None
        self.last_finished_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self.last_result: Optional[Dict[str, Any]] = None
        self.last_config: Optional[Dict[str, Any]] = None
        self._task: Optional[asyncio.Task[Any]] = None
        self._overrides: Dict[str, Any] = {}
        self.history: Deque[Dict[str, Any]] = deque(maxlen=APP_CONFIG.history_limit)
        self.history_store = HistoryStore(APP_CONFIG.history_file or None)
        # The user-controlled stats filter is the gatekeeper for the
        # persistent history: only events that pass these thresholds get
        # written to disk / kept in the ring buffer. Defaults mirror the
        # "Stats" tab defaults so behaviour is sensible out of the box.
        self.stats_filter: Dict[str, Any] = {
            "min_spread": 2.0,
            "min_profit": 20.0,
            "require_transfer": False,
            "pair": "",
        }
        # Restore the most recent events from disk so the Stats tab is
        # populated immediately after a restart.
        if self.history_store.enabled:
            restored = self.history_store.load_tail(APP_CONFIG.history_limit)
            for ev in restored:
                self.history.append(ev)
            if restored:
                logger.info(
                    "history: restored %d events from %s",
                    len(restored),
                    self.history_store.path,
                )
        # Runtime-mutable refresh interval (seconds). Initialized from the
        # env-driven default but can be changed at runtime via /api/scan.
        self.refresh_interval: float = float(APP_CONFIG.refresh_interval)
        # MAX notifier. The fallback_proxy_provider returns the scanner's
        # current effective proxy (override or env), so a single SCANNER_PROXY
        # is enough for both market data and MAX messages.
        self.max = MaxNotifier(
            bot_token=APP_CONFIG.max_bot_token,
            chat_id=APP_CONFIG.max_chat_id,
            recipient_kind=APP_CONFIG.max_recipient_kind,
            enabled=APP_CONFIG.max_enabled,
            proxy=APP_CONFIG.max_proxy,
            fallback_proxy_provider=self._current_scanner_proxy,
        )

    def _current_scanner_proxy(self) -> Optional[str]:
        """Used as the MAX notifier's fallback proxy when MAX_PROXY is empty."""
        override = self._overrides.get("proxy")
        if isinstance(override, str) and override:
            return override
        env_proxy = ScannerConfig().proxy
        return env_proxy or None

    def set_stats_filter(self, filt: Dict[str, Any]) -> None:
        """Update the persistence filter. Unknown / None values are ignored."""
        if not isinstance(filt, dict):
            return
        if filt.get("min_spread") is not None:
            try:
                self.stats_filter["min_spread"] = max(0.0, float(filt["min_spread"]))
            except (TypeError, ValueError):
                pass
        if filt.get("min_profit") is not None:
            try:
                self.stats_filter["min_profit"] = max(0.0, float(filt["min_profit"]))
            except (TypeError, ValueError):
                pass
        if filt.get("require_transfer") is not None:
            self.stats_filter["require_transfer"] = bool(filt["require_transfer"])
        if filt.get("pair") is not None:
            self.stats_filter["pair"] = str(filt["pair"]).strip()

    def event_passes_stats_filter(self, event: Dict[str, Any]) -> bool:
        """Return True if ``event`` satisfies the active stats filter."""
        f = self.stats_filter
        spread = event.get("spread") or 0.0
        # ``ob_profit_usdt`` is ``None`` when orderbook analysis didn't run
        # (low-volume rows, etc.). Treat that as 0 so positive min_profit
        # thresholds correctly exclude such rows.
        profit = event.get("ob_profit_usdt") or 0.0
        if spread < f["min_spread"]:
            return False
        if profit < f["min_profit"]:
            return False
        if f["require_transfer"] and not event.get("has_transfer"):
            return False
        pq = f["pair"].upper()
        if pq:
            pair = (event.get("pair") or "").upper()
            coin = (event.get("coin") or "").upper()
            if pq not in pair and pq not in coin:
                return False
        return True

    def set_overrides(self, overrides: Dict[str, Any]) -> None:
        # ``refresh_interval`` is a state-level setting, not a per-scan config,
        # so consume it here instead of forwarding to ScannerConfig.
        ri = overrides.pop("refresh_interval", None) if isinstance(overrides, dict) else None
        if ri is not None:
            try:
                self.refresh_interval = max(5.0, float(ri))
            except (TypeError, ValueError):
                pass
        self._overrides = {k: v for k, v in overrides.items() if v is not None}

    def build_config(self) -> ScannerConfig:
        cfg = ScannerConfig()
        for key, value in self._overrides.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        return cfg

    def _record_history(self, result_dict: Dict[str, Any]) -> None:
        ts = result_dict.get("finished_at") or time.time()
        new_events: List[Dict[str, Any]] = []
        skipped = 0
        for row in result_dict.get("arbitrage", []) or []:
            event = _row_to_history_event(row, ts)
            if not self.event_passes_stats_filter(event):
                skipped += 1
                continue
            self.history.append(event)
            new_events.append(event)
        if new_events and self.history_store.enabled:
            self.history_store.append_many(new_events)
        if skipped:
            logger.info(
                "history: kept %d / %d arbitrage rows (skipped %d below stats filter %s)",
                len(new_events),
                len(new_events) + skipped,
                skipped,
                self.stats_filter,
            )
        # Fire-and-forget MAX notification. Errors are swallowed by the
        # notifier itself and surfaced via /api/state. We don't await so
        # a slow MAX server can't stall the scan loop.
        if new_events and self.max.enabled and self.max.ready:
            asyncio.create_task(self.max.notify_events(new_events))

    async def trigger(self) -> None:
        """Start a scan unless one is already running.

        Manual triggers always run; only the background loop respects ``paused``.
        """
        async with self._lock:
            if self.scanning:
                return
            self.scanning = True
            self.last_started_at = time.time()
            self.last_error = None

        try:
            cfg = self.build_config()
            self.last_config = {
                "proxy": cfg.proxy,
                "min_volume": cfg.min_volume,
                "min_spread": cfg.min_spread,
                "max_spread": cfg.max_spread,
                "orderbook_limit": cfg.orderbook_limit,
                "orderbook_budget_usdt": cfg.orderbook_budget_usdt,
            }
            result = await run_scan(cfg, arbitrage_limit=APP_CONFIG.arbitrage_limit)
            result_dict = scan_result_to_dict(result)
            self.last_result = result_dict
            self.last_finished_at = time.time()
            self._record_history(result_dict)
            logger.info(
                "Scan complete: %d pairs, %d arbitrage rows in %.1fs (history: %d)",
                result.total_pairs,
                len(result.arbitrage),
                result.duration_seconds,
                len(self.history),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Scan failed: %s", exc)
            self.last_error = str(exc)
        finally:
            async with self._lock:
                self.scanning = False

    async def background_loop(self) -> None:
        """Continuously refresh the scan every ``refresh_interval`` seconds.

        When ``paused`` is True, the loop sleeps without scanning. Manual scans
        triggered via the API still run.
        """
        while True:
            try:
                if not self.paused:
                    await self.trigger()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Background scan iteration failed")
            await asyncio.sleep(max(5.0, self.refresh_interval))

    def start_background(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.background_loop())

    async def stop_background(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    def filter_history(
        self,
        min_spread: float,
        min_profit: float,
        limit: int,
        require_transfer: bool = False,
        pair_query: str = "",
    ) -> List[Dict[str, Any]]:
        pq = pair_query.strip().upper()
        events: List[Dict[str, Any]] = []
        # Iterate newest first.
        for event in reversed(self.history):
            spread = event.get("spread") or 0.0
            profit = event.get("ob_profit_usdt") or 0.0
            if spread < min_spread:
                continue
            if profit < min_profit:
                continue
            if require_transfer and not event.get("has_transfer"):
                continue
            if pq:
                pair = event.get("pair", "") or ""
                coin = event.get("coin", "") or ""
                if pq not in pair.upper() and pq not in coin.upper():
                    continue
            events.append(event)
            if len(events) >= limit:
                break
        return events


state = ScannerState()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if APP_CONFIG.auto_start:
        state.start_background()
    try:
        yield
    finally:
        await state.stop_background()


app = FastAPI(title="Crypto Arbitrage Scanner", lifespan=lifespan)


class ScanOverrides(BaseModel):
    proxy: Optional[str] = Field(
        default=None, description="Proxy URL (http/https/socks5). Use empty string to disable."
    )
    min_volume: Optional[float] = Field(default=None, ge=0)
    min_spread: Optional[float] = Field(default=None, ge=0)
    # max_spread = 0 means "no upper limit".
    max_spread: Optional[float] = Field(default=None, ge=0)
    refresh_interval: Optional[float] = Field(
        default=None, ge=5, description="Background loop period in seconds (min 5)."
    )
    orderbook_limit: Optional[int] = Field(
        default=None,
        ge=5,
        le=500,
        description="How many orderbook levels to fetch per side (5..500).",
    )
    orderbook_budget_usdt: Optional[float] = Field(
        default=None,
        ge=0,
        description="USDT budget used to compute the 'profit at $N' column. 0 disables.",
    )


class StatsFilterPayload(BaseModel):
    """Filter applied at scan time to decide which arbitrage rows are kept.

    These thresholds gate both the in-memory ring buffer AND the on-disk
    JSONL file — rows that don't pass are silently dropped from history.
    """

    min_spread: Optional[float] = Field(default=None, ge=0)
    min_profit: Optional[float] = Field(default=None, ge=0)
    require_transfer: Optional[bool] = None
    pair: Optional[str] = None


@app.get("/healthz")
async def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/state")
async def get_state() -> JSONResponse:
    payload: Dict[str, Any] = {
        "scanning": state.scanning,
        "paused": state.paused,
        "last_started_at": state.last_started_at,
        "last_finished_at": state.last_finished_at,
        "last_error": state.last_error,
        "config": state.last_config,
        "refresh_interval": state.refresh_interval,
        "exchanges_order": EXCHANGES_ORDER,
        "history_size": len(state.history),
        "history_limit": APP_CONFIG.history_limit,
        "history_file": str(state.history_store.path) if state.history_store.enabled else None,
        "history_file_size": state.history_store.size_bytes(),
        "stats_filter": state.stats_filter,
        "max": state.max.public_state(),
        "result": state.last_result,
    }
    return JSONResponse(payload)


@app.post("/api/scan")
async def trigger_scan(overrides: Optional[ScanOverrides] = None) -> Dict[str, Any]:
    if state.scanning:
        raise HTTPException(status_code=409, detail="Scan already in progress")

    payload = overrides.model_dump() if overrides else {}
    if payload.get("proxy") == "":
        payload["proxy"] = None
    state.set_overrides(payload)
    asyncio.create_task(state.trigger())
    return {"status": "started"}


@app.post("/api/settings")
async def update_settings(overrides: Optional[ScanOverrides] = None) -> Dict[str, Any]:
    """Update scanner overrides without triggering a scan.

    Used by the UI to auto-apply parameter edits — the next background-loop
    tick (or an explicit scan request) picks up the new values.
    """
    payload = overrides.model_dump() if overrides else {}
    if payload.get("proxy") == "":
        payload["proxy"] = None
    state.set_overrides(payload)
    return {
        "status": "ok",
        "overrides": state._overrides,
        "refresh_interval": state.refresh_interval,
    }


@app.post("/api/pause")
async def pause_scanner() -> Dict[str, Any]:
    state.paused = True
    logger.info("Background scanner paused")
    return {"paused": True}


@app.post("/api/resume")
async def resume_scanner() -> Dict[str, Any]:
    state.paused = False
    logger.info("Background scanner resumed")
    return {"paused": False}


@app.get("/api/stats")
async def get_stats(
    min_spread: float = 2.0,
    min_profit: float = 20.0,
    limit: int = 500,
    require_transfer: bool = False,
    pair: str = "",
) -> JSONResponse:
    events = state.filter_history(
        min_spread=min_spread,
        min_profit=min_profit,
        limit=limit,
        require_transfer=require_transfer,
        pair_query=pair,
    )
    return JSONResponse(
        {
            "events": events,
            "total_in_history": len(state.history),
            "history_limit": APP_CONFIG.history_limit,
            "filter": {
                "min_spread": min_spread,
                "min_profit": min_profit,
                "limit": limit,
                "require_transfer": require_transfer,
                "pair": pair,
            },
        }
    )


@app.post("/api/stats/filter")
async def update_stats_filter(payload: Optional[StatsFilterPayload] = None) -> Dict[str, Any]:
    """Update the persistence filter (thresholds for what gets saved).

    Applied immediately to the next scan's recorded events. Does NOT
    retroactively delete anything already on disk.
    """
    body = payload.model_dump() if payload else {}
    state.set_stats_filter(body)
    return {"status": "ok", "stats_filter": state.stats_filter}


@app.delete("/api/stats")
async def clear_stats() -> Dict[str, Any]:
    state.history.clear()
    if state.history_store.enabled:
        state.history_store.clear()
    # Also wipe MAX dedup so previously-notified situations can re-trigger.
    state.max.reset_dedup()
    logger.info("History cleared by user")
    return {"cleared": True}


class MaxSettingsPayload(BaseModel):
    enabled: Optional[bool] = None
    bot_token: Optional[str] = Field(
        default=None,
        description="Bot token from @MasterBot. Pass empty string to clear.",
    )
    chat_id: Optional[str] = Field(
        default=None, description="Numeric chat ID (or user ID, see recipient_kind)."
    )
    recipient_kind: Optional[str] = Field(
        default=None,
        description="'chat' (default) sends to a chat, 'user' sends DM.",
    )
    proxy: Optional[str] = Field(
        default=None,
        description="Per-MAX proxy override. Empty falls back to SCANNER_PROXY.",
    )


@app.post("/api/max/settings")
async def update_max_settings(payload: Optional[MaxSettingsPayload] = None) -> Dict[str, Any]:
    """Update MAX notifier settings at runtime. None fields are ignored."""
    body = payload.model_dump() if payload else {}
    state.max.update_config(**{k: v for k, v in body.items() if v is not None})
    return {"status": "ok", "max": state.max.public_state()}


class MaxTestPayload(BaseModel):
    message: Optional[str] = None


@app.post("/api/max/test")
async def test_max(payload: Optional[MaxTestPayload] = None) -> Dict[str, Any]:
    """Send a probe message; bypasses ``enabled`` and dedup."""
    message = payload.message if payload else None
    result = await state.max.send_test(message)
    return {**result, "max": state.max.public_state()}


@app.post("/api/max/reset_dedup")
async def reset_max_dedup() -> Dict[str, Any]:
    state.max.reset_dedup()
    return {"status": "ok", "max": state.max.public_state()}


@app.get("/api/history/download")
async def download_history() -> FileResponse:
    """Stream the full JSONL history file to the client.

    If persistence is disabled or the file is empty, returns 404.
    """
    store = state.history_store
    if not store.enabled or store.path is None or not store.path.exists() or store.size_bytes() == 0:
        raise HTTPException(status_code=404, detail="History file is empty or persistence is disabled.")
    return FileResponse(
        path=store.path,
        media_type="application/x-ndjson",
        filename="scanner_history.jsonl",
    )


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
