"""FastAPI entrypoint for the crypto situation scanner web interface."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .scanner import MarketScanner

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

scanner = MarketScanner()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await scanner.start()
    try:
        yield
    finally:
        await scanner.stop()


app = FastAPI(title="Crypto Situation Scanner", lifespan=lifespan)


@app.get("/api/state")
async def get_state() -> JSONResponse:
    """Return the latest scanner snapshot.

    The web UI polls this endpoint once per second to refresh the dashboard.
    """
    state = await scanner.get_state()
    return JSONResponse(asdict(state))


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
