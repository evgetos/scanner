"""FastAPI entry point for the crypto arbitrage scanner web UI."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

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


class ScannerState:
    """Mutable in-memory state for the latest scan + background loop."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.scanning: bool = False
        self.last_started_at: Optional[float] = None
        self.last_finished_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self.last_result: Optional[Dict[str, Any]] = None
        self.last_config: Optional[Dict[str, Any]] = None
        self._task: Optional[asyncio.Task[Any]] = None
        self._overrides: Dict[str, Any] = {}

    def set_overrides(self, overrides: Dict[str, Any]) -> None:
        self._overrides = {k: v for k, v in overrides.items() if v is not None}

    def build_config(self) -> ScannerConfig:
        cfg = ScannerConfig()
        for key, value in self._overrides.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        return cfg

    async def trigger(self) -> None:
        """Start a scan unless one is already running."""
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
            self.last_result = scan_result_to_dict(result)
            self.last_finished_at = time.time()
            logger.info(
                "Scan complete: %d pairs, %d arbitrage rows in %.1fs",
                result.total_pairs,
                len(result.arbitrage),
                result.duration_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Scan failed: %s", exc)
            self.last_error = str(exc)
        finally:
            async with self._lock:
                self.scanning = False

    async def background_loop(self) -> None:
        """Continuously refresh the scan every ``refresh_interval`` seconds."""
        while True:
            try:
                await self.trigger()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Background scan iteration failed")
            await asyncio.sleep(max(5.0, APP_CONFIG.refresh_interval))

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
    max_spread: Optional[float] = Field(default=None, ge=0)


@app.get("/healthz")
async def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/state")
async def get_state() -> JSONResponse:
    payload: Dict[str, Any] = {
        "scanning": state.scanning,
        "last_started_at": state.last_started_at,
        "last_finished_at": state.last_finished_at,
        "last_error": state.last_error,
        "config": state.last_config,
        "refresh_interval": APP_CONFIG.refresh_interval,
        "exchanges_order": EXCHANGES_ORDER,
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


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
