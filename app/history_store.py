"""Persistent JSON Lines store for arbitrage history events.

One JSON object per line. Append-only on the hot path; the file is read in
its entirety only on startup (tail) and on the download endpoint.

The store is intentionally tiny and synchronous — append latency is a few
hundred microseconds at typical event rates and that's well within the
budget of a scan that takes 1+ second to produce its events. A single
``threading.Lock`` is used to serialize concurrent appends and clears.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import deque
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


class HistoryStore:
    """Tail-load + append-only JSONL backing store for arbitrage events."""

    def __init__(self, path: Optional[str]) -> None:
        self._path: Optional[Path] = Path(path) if path else None
        self._lock = threading.Lock()
        if self._path is not None:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:  # noqa: BLE001
                logger.warning(
                    "history: cannot create parent dir for %s: %s — persistence disabled",
                    self._path,
                    exc,
                )
                self._path = None

    @property
    def path(self) -> Optional[Path]:
        return self._path

    @property
    def enabled(self) -> bool:
        return self._path is not None

    def load_tail(self, limit: int) -> List[Dict[str, object]]:
        """Return the most recent ``limit`` events from the file, oldest first.

        Reads the whole file once on startup. For the default 1000-event cap
        this is trivially small (~200 KB). If the file grows much larger,
        consider rotating it or switching to a streaming tail.
        """
        if self._path is None or not self._path.exists() or limit <= 0:
            return []
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                # Cheap O(n) tail via deque. Avoids loading huge files in
                # memory all at once if the user lets the file grow.
                tail: Deque[str] = deque(maxlen=limit)
                for line in fh:
                    line = line.strip()
                    if line:
                        tail.append(line)
        except OSError as exc:  # noqa: BLE001
            logger.warning("history: failed to read %s: %s", self._path, exc)
            return []

        events: List[Dict[str, object]] = []
        for line in tail:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                # Skip a malformed line rather than crashing startup.
                continue
        return events

    def append_many(self, events: Iterable[Dict[str, object]]) -> int:
        """Append events to the file. Returns the number of bytes written."""
        if self._path is None:
            return 0
        chunk = "".join(json.dumps(ev, ensure_ascii=False) + "\n" for ev in events)
        if not chunk:
            return 0
        with self._lock:
            try:
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(chunk)
                    fh.flush()
                    try:
                        os.fsync(fh.fileno())
                    except OSError:
                        # fsync isn't available on every platform/filesystem.
                        pass
            except OSError as exc:  # noqa: BLE001
                logger.warning("history: failed to append to %s: %s", self._path, exc)
                return 0
        return len(chunk.encode("utf-8"))

    def clear(self) -> None:
        """Truncate the file to zero bytes."""
        if self._path is None:
            return
        with self._lock:
            try:
                with self._path.open("w", encoding="utf-8") as fh:
                    fh.truncate(0)
            except OSError as exc:  # noqa: BLE001
                logger.warning("history: failed to truncate %s: %s", self._path, exc)

    def size_bytes(self) -> int:
        if self._path is None or not self._path.exists():
            return 0
        try:
            return self._path.stat().st_size
        except OSError:
            return 0
