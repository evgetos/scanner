"""Entry point for running the scanner locally (e.g. from PyCharm).

Open this file in PyCharm and click ▶ Run (or right-click → Run 'main').
Equivalent to ``uvicorn app.main:app --host 127.0.0.1 --port 8000``.

Environment variables (optional, set them in the PyCharm Run Configuration):
  HOST       bind address (default: 127.0.0.1)
  PORT       port (default: 8000)
  RELOAD     "1" / "true" to enable hot-reload (default: off)
  LOG_LEVEL  uvicorn log level (default: info)
"""

from __future__ import annotations

import os


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    reload = _env_bool("RELOAD", False)
    log_level = os.environ.get("LOG_LEVEL", "info")

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level=log_level,
    )


if __name__ == "__main__":
    main()
