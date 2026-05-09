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

The project uses [Poetry](https://python-poetry.org/) and Python 3.10+.

```bash
poetry install
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then open http://localhost:8000.

## Configuration

The scanner is configured from sensible defaults in `app/scanner.py`. Adjust
`MarketScanner` constructor arguments in `app/main.py` to tune the poll
interval, the number of tracked symbols, or detection thresholds.

## Layout

```
app/
  main.py          # FastAPI app, lifespan-managed scanner
  scanner.py       # Background market poller and situation detector
  static/
    index.html     # Dashboard markup
    styles.css     # Dashboard styling
    app.js         # 1Hz polling + DOM updates
```
