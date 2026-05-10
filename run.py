"""Run the scanner web app from any IDE without touching the command line.

Open this file in PyCharm (or VS Code, IDLE, etc.) and press the "Run" button.
The shared PyCharm run configuration in `.run/Scanner.run.xml` also points
here, so the green play button at the top of PyCharm will pick it up
automatically once the project is opened.

Behavior:
- Starts a local uvicorn server on http://127.0.0.1:8000.
- Opens the dashboard in the default browser after a short warm-up delay.
- Prints status to the IDE Run console.

Stop the server by pressing the red "Stop" button in PyCharm (or Ctrl+C in
the Run console). Override host/port with the SCANNER_HOST and SCANNER_PORT
environment variables if 8000 is already in use.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Make sure the repo root (the directory containing this file *and* the
# `app/` package) is on sys.path before we import anything from `app`.
# Without this, running `run.py` from a different working directory --
# for example PyCharm's run configurations that pick a non-root cwd --
# can fail with `ModuleNotFoundError: No module named 'app'`.
_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import uvicorn  # noqa: E402  (import after sys.path tweak is intentional)

from app.main import app  # noqa: E402

DEFAULT_HOST = os.environ.get("SCANNER_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("SCANNER_PORT", "8000"))
OPEN_BROWSER = os.environ.get("SCANNER_OPEN_BROWSER", "1") not in {"0", "false", "False", ""}


def _open_browser_when_ready(url: str, delay: float = 1.5) -> None:
    """Open *url* in the default browser after a short delay.

    The delay gives uvicorn time to bind the port; otherwise the browser may
    race the server and load before it is ready.
    """
    time.sleep(delay)
    try:
        webbrowser.open_new_tab(url)
    except Exception as exc:  # pragma: no cover - best-effort UX nicety
        print(f"[run.py] Could not open browser automatically: {exc}")


def main() -> None:
    url = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/"
    print(f"[run.py] Starting Crypto Situation Scanner on {url}")
    print("[run.py] Press the red Stop button (or Ctrl+C) to shut it down.")
    if OPEN_BROWSER:
        threading.Thread(
            target=_open_browser_when_ready,
            args=(url,),
            daemon=True,
        ).start()
    # Pass the FastAPI instance directly (instead of the "app.main:app"
    # import string) so uvicorn does not have to re-resolve the module
    # through its own import machinery -- which would fail again if
    # PyCharm's working directory does not include the project root.
    uvicorn.run(
        app,
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
