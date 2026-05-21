from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .exchanges import EXCHANGE_LABELS, EXCHANGES
from .models import ScanResult, ScannerSettings
from .scanner import Scanner
from .settings import SettingsStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scanner")

STATIC_DIR = Path(__file__).parent / "static"

settings_store = SettingsStore()
scanner = Scanner(settings_store)


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


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
