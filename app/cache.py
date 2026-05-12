"""Простейший асинхронный TTL-кэш на одну запись для каждого ключа."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self._store: dict[str, tuple[float, T]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def get_or_set(self, key: str, loader: Callable[[], Awaitable[T]]) -> T:
        now = time.time()
        cached = self._store.get(key)
        if cached and now - cached[0] < self.ttl:
            return cached[1]
        async with self._lock(key):
            cached = self._store.get(key)
            now = time.time()
            if cached and now - cached[0] < self.ttl:
                return cached[1]
            value = await loader()
            self._store[key] = (time.time(), value)
            return value

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self._store.clear()
        else:
            self._store.pop(key, None)


def now_ms() -> int:
    return int(time.time() * 1000)


__all__ = ["TTLCache", "now_ms", "Any"]
