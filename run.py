"""Entry point for running the scanner web app from PyCharm.

Right-click this file in PyCharm and choose "Run 'run'" — it will start
uvicorn on http://127.0.0.1:8000 with auto-reload enabled.

A `.env` file located next to this script is loaded automatically before
uvicorn starts, so API keys and proxy settings can be kept out of source
control. See `.env.example` for the supported variables.

Equivalent CLI command:
    uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

import uvicorn


PROJECT_ROOT = Path(__file__).resolve().parent
DOTENV_PATH = PROJECT_ROOT / ".env"


def main() -> None:
    if DOTENV_PATH.is_file():
        load_dotenv(DOTENV_PATH, override=False)
        print(f"[run.py] loaded environment from {DOTENV_PATH}")
    else:
        print(f"[run.py] no .env at {DOTENV_PATH}, using process environment only")

    host = os.environ.get("SCANNER_HOST", "127.0.0.1")
    port = int(os.environ.get("SCANNER_PORT", "8000"))
    reload = os.environ.get("SCANNER_RELOAD", "1") == "1"
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
