"""Runtime configuration for the scanner web app.

All values can be overridden via environment variables so secrets stay out of
source control. The defaults mirror the original ``scanner.py`` script.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return value if value is not None else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class ScannerConfig:
    """Tunable parameters for a single scan run."""

    proxy: Optional[str] = field(default_factory=lambda: _env("SCANNER_PROXY") or None)
    min_volume: float = field(default_factory=lambda: _env_float("SCANNER_MIN_VOLUME", 0.0))
    min_spread: float = field(default_factory=lambda: _env_float("SCANNER_MIN_SPREAD", 0.0))
    # 0 (or any non-positive value) means "no upper limit".
    max_spread: float = field(default_factory=lambda: _env_float("SCANNER_MAX_SPREAD", 0.0))
    orderbook_limit: int = field(default_factory=lambda: _env_int("SCANNER_OB_LIMIT", 50))

    mexc_api_key: str = field(default_factory=lambda: _env("MEXC_API_KEY"))
    mexc_secret_key: str = field(default_factory=lambda: _env("MEXC_SECRET_KEY"))
    binance_api_key: str = field(default_factory=lambda: _env("BINANCE_API_KEY"))
    binance_secret_key: str = field(default_factory=lambda: _env("BINANCE_SECRET_KEY"))
    bybit_api_key: str = field(default_factory=lambda: _env("BYBIT_API_KEY"))
    bybit_secret_key: str = field(default_factory=lambda: _env("BYBIT_SECRET_KEY"))


@dataclass
class AppConfig:
    """Web-app level configuration."""

    refresh_interval: float = field(
        default_factory=lambda: _env_float("SCANNER_REFRESH_INTERVAL", 60.0)
    )
    auto_start: bool = field(
        default_factory=lambda: _env("SCANNER_AUTO_START", "1") not in ("0", "false", "False")
    )
    arbitrage_limit: int = field(
        default_factory=lambda: _env_int("SCANNER_ARBITRAGE_LIMIT", 50)
    )
    history_limit: int = field(
        default_factory=lambda: _env_int("SCANNER_HISTORY_LIMIT", 1000)
    )
