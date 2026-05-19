from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.models import DensityCard, ExchangeInfo, ScanSettings, ScanStatus
from app.scanner import DensityScanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

scanner = DensityScanner()


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    logger.info("Density Scanner starting up")
    yield
    logger.info("Shutting down")
    await scanner.close()


app = FastAPI(title="Order Book Density Scanner", lifespan=lifespan)


@app.get("/api/exchanges")
async def get_exchanges() -> list[ExchangeInfo]:
    infos = scanner.exchange_manager.get_all_exchange_info()
    return [ExchangeInfo(**i) for i in infos]


@app.post("/api/scan")
async def run_scan(settings: ScanSettings) -> list[DensityCard]:
    return await scanner.scan(settings)


@app.post("/api/auto-scan/start")
async def start_auto_scan(settings: ScanSettings) -> ScanStatus:
    scanner.settings = settings
    scanner.start_auto_scan()
    return scanner.status


@app.post("/api/auto-scan/stop")
async def stop_auto_scan() -> ScanStatus:
    scanner.stop_auto_scan()
    return scanner.status


@app.get("/api/status")
async def get_status() -> ScanStatus:
    return scanner.status


@app.get("/api/results")
async def get_results() -> list[DensityCard]:
    return scanner.cards


@app.get("/")
async def index() -> FileResponse:
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")
