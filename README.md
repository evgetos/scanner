# Crypto Situation Scanner

A small FastAPI service that polls the public Binance REST API once per second
for the top USDT trading pairs by 24h volume, detects market "situations"
(short-term price spikes, volume surges, fresh 24h highs and lows), and serves a
self-contained web dashboard that refreshes the data every second.

## Features

- Background scanner that pulls the `/api/v3/ticker/24hr` endpoint each second
  and keeps a rolling in-memory history per symbol.
- Situation detector:
  - **Price spike (1m)** — symbol moved by more than ±1% in the last minute.
  - **Volume surge** — last-minute quote volume is at least 2× the 10-minute
    baseline.
  - **New 24h high / low** — last price touches the daily extreme.
- Web UI (`/`) with a live-updating situations panel and a sortable table of
  the tracked tickers. The page polls `/api/state` every second; price cells
  flash green/red when they change.
- JSON endpoint at `/api/state` for programmatic use; `/healthz` for liveness.

## Running locally

The project requires Python 3.10+. You can run it from an IDE without ever
touching a terminal, or from the command line — pick whichever you prefer.

### Option A — From PyCharm (no command line)

1. `File → Open…` and select the cloned `scanner/` folder.
2. When PyCharm asks about the interpreter, point it at any Python 3.10+
   environment (or let PyCharm create a fresh virtualenv for the project).
3. PyCharm should auto-detect `requirements.txt` and offer to install the
   dependencies — accept the prompt. If it doesn't, right-click
   `requirements.txt` → `Install all Packages`.
4. Open `run.py` and click the green ▶ next to `if __name__ == "__main__":`,
   or pick the bundled **Scanner** run configuration from the top toolbar
   (it's stored at `.run/Scanner.run.xml` and ships with the repo).

`run.py` starts uvicorn programmatically and opens
http://127.0.0.1:8000 in your default browser. Hit the red ■ Stop button to
shut it down.

Need a different port? Set `SCANNER_PORT` (and optionally `SCANNER_HOST`)
under `Run → Edit Configurations → Environment variables`. Set
`SCANNER_OPEN_BROWSER=0` if you don't want a browser tab to be opened
automatically.

### Option B — From the command line

With [Poetry](https://python-poetry.org/):

```bash
poetry install
poetry run python run.py
# or, equivalently:
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Or with plain pip:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Then open http://localhost:8000.

## Configuration

The scanner is configured from sensible defaults in `app/scanner.py`. Adjust
`MarketScanner` constructor arguments in `app/main.py` to tune the poll
interval, the number of tracked symbols, or detection thresholds.

## Layout

```
run.py             # IDE entry point (PyCharm / VS Code green Run button)
requirements.txt   # pip-friendly dependency list (mirrors pyproject.toml)
.run/
  Scanner.run.xml  # Shared PyCharm run configuration
app/
  main.py          # FastAPI app, lifespan-managed scanner
  scanner.py       # Background market poller and situation detector
  static/
    index.html     # Dashboard markup
    styles.css     # Dashboard styling
    app.js         # 1Hz polling + DOM updates
```
