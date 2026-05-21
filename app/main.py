from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .exchanges import EXCHANGE_LABELS, EXCHANGES
from .history import HistoryStore
from .models import HistoryPage, ScanResult, ScannerSettings, Situation
from .scanner import Scanner
from .settings import SettingsStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scanner")

STATIC_DIR = Path(__file__).parent / "static"
HISTORY_PATH = Path(os.environ.get("SCANNER_HISTORY", "data/history.db"))

settings_store = SettingsStore()
history_store = HistoryStore(HISTORY_PATH)
scanner = Scanner(settings_store, history_store)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await scanner.start()
    try:
        yield
    finally:
        await scanner.stop()


app = FastAPI(title="Fair vs Last Price Scanner", lifespan=lifespan)


@app.get("/api/exchanges")
def list_exchanges():
    return [
        {"id": name, "label": EXCHANGE_LABELS.get(name, name.title())}
        for name in EXCHANGES
    ]


@app.get("/api/settings", response_model=ScannerSettings)
def get_settings() -> ScannerSettings:
    return settings_store.get()


@app.post("/api/settings", response_model=ScannerSettings)
def update_settings(payload: ScannerSettings) -> ScannerSettings:
    saved = settings_store.update(payload)
    scanner.kick()
    return saved


@app.get("/api/scan", response_model=ScanResult)
def get_scan() -> ScanResult:
    return scanner.last_result


@app.post("/api/scan/run", response_model=ScanResult)
async def run_scan() -> ScanResult:
    return await scanner.scan_once()


@app.get("/api/history", response_model=HistoryPage)
def get_history(
    exchange: Optional[str] = None,
    symbol: Optional[str] = None,
    status: Optional[str] = Query(None, pattern="^(open|closed)$"),
    from_ts: Optional[float] = None,
    to_ts: Optional[float] = None,
    min_abs_spread: Optional[float] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> HistoryPage:
    rows, total = history_store.query(
        exchange=exchange,
        symbol=symbol,
        status=status,
        from_ts=from_ts,
        to_ts=to_ts,
        min_abs_spread=min_abs_spread,
        limit=limit,
        offset=offset,
    )
    items = []
    for r in rows:
        d = asdict(r)
        d["duration_sec"] = (r.closed_at - r.opened_at) if r.closed_at else None
        items.append(Situation(**d))
    return HistoryPage(items=items, total=total, limit=limit, offset=offset)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
