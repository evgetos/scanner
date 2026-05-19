from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.exchanges import EXCHANGE_DISPLAY_NAMES, EXCHANGE_FEATURES
from app.models import DensityResult, ExchangeInfo, ScanSettings, ScanStatus
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
    logger.info("Shutting down, closing exchange connections")
    await scanner.close()


app = FastAPI(title="Order Book Density Scanner", lifespan=lifespan)


@app.get("/api/exchanges")
async def get_exchanges() -> list[ExchangeInfo]:
    result = []
    for ex_id, name in EXCHANGE_DISPLAY_NAMES.items():
        features = EXCHANGE_FEATURES.get(ex_id, {})
        result.append(
            ExchangeInfo(
                id=ex_id,
                name=name,
                spot=features.get("spot", False),
                futures=features.get("futures", False),
            )
        )
    return result


@app.post("/api/scan")
async def run_scan(settings: ScanSettings) -> list[DensityResult]:
    return await scanner.scan(settings)


@app.get("/api/status")
async def get_status() -> ScanStatus:
    return scanner.status


@app.get("/api/results")
async def get_results() -> list[DensityResult]:
    return scanner.results


@app.get("/")
async def index() -> FileResponse:
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")
