"""FastAPI-приложение MEXC Scanner."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.aggregator import build_futures_rows, build_spot_rows
from app.cache import TTLCache
from app.mexc import MexcClient

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CACHE_TTL = float(os.getenv("CACHE_TTL", "30"))

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    client = MexcClient()
    cache: TTLCache[list[dict[str, Any]]] = TTLCache(ttl=CACHE_TTL)
    app.state.mexc = client
    app.state.cache = cache
    logger.info(
        "MEXC client initialised. credentials=%s, cache_ttl=%ss",
        client.has_credentials,
        CACHE_TTL,
    )
    try:
        yield
    finally:
        await client.close()


app = FastAPI(title="MEXC Scanner", version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "credentials": app.state.mexc.has_credentials,
        "cache_ttl": CACHE_TTL,
    }


@app.get("/api/spot")
async def api_spot() -> JSONResponse:
    client: MexcClient = app.state.mexc
    cache: TTLCache[list[dict[str, Any]]] = app.state.cache
    try:
        rows = await cache.get_or_set("spot", lambda: build_spot_rows(client))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to build spot rows")
        raise HTTPException(status_code=502, detail=f"MEXC spot fetch failed: {exc}") from exc
    return JSONResponse(
        {
            "credentials": client.has_credentials,
            "count": len(rows),
            "rows": rows,
        }
    )


@app.get("/api/futures")
async def api_futures() -> JSONResponse:
    client: MexcClient = app.state.mexc
    cache: TTLCache[list[dict[str, Any]]] = app.state.cache
    try:
        rows = await cache.get_or_set("futures", lambda: build_futures_rows(client))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to build futures rows")
        raise HTTPException(status_code=502, detail=f"MEXC futures fetch failed: {exc}") from exc
    return JSONResponse(
        {
            "credentials": client.has_credentials,
            "count": len(rows),
            "rows": rows,
        }
    )


@app.post("/api/refresh")
async def api_refresh() -> dict[str, str]:
    cache: TTLCache[list[dict[str, Any]]] = app.state.cache
    cache.invalidate()
    return {"status": "ok", "message": "cache invalidated"}


# ---------- Статика и корневая страница ----------

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(index_path)


def run() -> None:
    """Удобная точка входа: `python -m app.main`."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    run()
