"""Entry point for running the scanner web app from PyCharm.

Right-click this file in PyCharm and choose "Run 'run'" — it will start
uvicorn on http://127.0.0.1:8000 with auto-reload enabled.

Equivalent CLI command:
    uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("SCANNER_HOST", "127.0.0.1")
    port = int(os.environ.get("SCANNER_PORT", "8000"))
    reload = os.environ.get("SCANNER_RELOAD", "1") == "1"
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
