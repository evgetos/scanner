from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from .exchanges import EXCHANGES
from .models import ExchangeSettings, ScannerSettings

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(os.environ.get("SCANNER_CONFIG", "data/config.json"))


def default_settings() -> ScannerSettings:
    return ScannerSettings(
        exchanges={name: ExchangeSettings(enabled=True) for name in EXCHANGES},
        min_volume_usdt=1_000_000.0,
        min_spread_pct=0.5,
        scan_interval_sec=1,
        proxy_url=None,
        sound_enabled=False,
        sound_threshold_pct=1.0,
    )


class SettingsStore:
    """Thread-safe settings holder backed by a JSON file."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DEFAULT_CONFIG_PATH
        self._lock = threading.Lock()
        self._settings = self._load()

    def _load(self) -> ScannerSettings:
        if not self.path.exists():
            return default_settings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            settings = ScannerSettings.model_validate(raw)
        except Exception:
            logger.exception("Failed to load %s, falling back to defaults", self.path)
            return default_settings()
        return self._normalise(settings)

    @staticmethod
    def _normalise(settings: ScannerSettings) -> ScannerSettings:
        # Ensure every known exchange has a settings entry, drop unknown ones.
        out = {}
        for name in EXCHANGES:
            out[name] = settings.exchanges.get(name) or ExchangeSettings(enabled=True)
        settings.exchanges = out
        return settings

    def get(self) -> ScannerSettings:
        with self._lock:
            return self._settings.model_copy(deep=True)

    def update(self, new: ScannerSettings) -> ScannerSettings:
        with self._lock:
            self._settings = self._normalise(new)
            self._persist()
            return self._settings.model_copy(deep=True)

    def _persist(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                self._settings.model_dump_json(indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.exception("Failed to persist settings to %s", self.path)
