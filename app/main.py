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
        # Runtime-mutable refresh interval (seconds). Initialized from the
        # env-driven default but can be changed at runtime via /api/scan.
        self.refresh_interval: float = float(APP_CONFIG.refresh_interval)

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
        for row in result_dict.get("arbitrage", []) or []:
            self.history.append(_row_to_history_event(row, ts))

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


@app.delete("/api/stats")
async def clear_stats() -> Dict[str, Any]:
    state.history.clear()
    logger.info("History cleared by user")
    return {"cleared": True}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
