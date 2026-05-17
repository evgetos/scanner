"""Telegram bot notifier for newly recorded arbitrage events.

Sends one HTML-formatted message per completed scan, listing only events
that have NOT been seen before (deduplicated by ``pair | buy_ex -> sell_ex``).
Falls back gracefully when the bot token / chat id are missing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import aiohttp

try:  # pragma: no cover - optional dep
    from aiohttp_socks import ProxyConnector
except ImportError:  # pragma: no cover
    ProxyConnector = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Telegram caps a single message at 4096 chars. We aim a bit lower so the
# header/footer always fit without truncation.
MAX_MSG = 3900


def _esc(value: Any) -> str:
    """HTML-escape for Telegram (parse_mode=HTML)."""
    if value is None:
        return ""
    s = str(value)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_price(value: Any) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if v == 0:
        return "0"
    av = abs(v)
    if av < 1e-5:
        return f"{v:.4e}"
    if av < 1:
        return f"{v:.6f}"
    if av < 100:
        return f"{v:.4f}"
    return f"{v:.2f}"


def _fmt_usd(value: Any) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if abs(v) >= 1:
        return f"${v:,.0f}"
    return f"${v:.2f}"


def _dedup_key(event: Dict[str, Any]) -> str:
    pair = event.get("pair") or ""
    buy = event.get("buy_exchange") or ""
    sell = event.get("sell_exchange") or ""
    return f"{pair}|{buy}->{sell}"


def _mask_proxy(url: Optional[str]) -> Optional[str]:
    """Strip credentials from a proxy URL so it's safe to surface in /api/state."""
    if not url:
        return None
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" not in rest:
        return url
    creds, host = rest.rsplit("@", 1)
    if ":" in creds:
        user, _ = creds.split(":", 1)
        return f"{scheme}://{user}:***@{host}"
    return f"{scheme}://***@{host}"


class TelegramNotifier:
    """Async Telegram notifier with per-scan deduplication.

    ``enabled`` flips at runtime via :meth:`update_config`. When the bot
    token / chat id are missing, ``ready`` is False and ``notify_events``
    is a no-op (the caller doesn't need to check).
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        enabled: bool = True,
        proxy: Optional[str] = None,
        fallback_proxy_provider: Optional[Callable[[], Optional[str]]] = None,
        dedup_capacity: int = 5000,
    ) -> None:
        self._bot_token: str = (bot_token or "").strip()
        self._chat_id: str = (chat_id or "").strip()
        self._enabled: bool = bool(enabled)
        # Telegram-specific proxy override; if empty, we ask
        # ``fallback_proxy_provider`` at send-time (typically the scanner's
        # current proxy, so the user only has to configure it once).
        self._proxy: str = (proxy or "").strip()
        self._fallback_proxy_provider = fallback_proxy_provider
        # OrderedDict so we get FIFO eviction once we hit ``dedup_capacity``.
        self._seen: "OrderedDict[str, float]" = OrderedDict()
        self._dedup_capacity: int = max(100, int(dedup_capacity))
        self._send_lock = asyncio.Lock()
        self.last_error: Optional[str] = None
        self.last_sent_at: Optional[float] = None
        self.last_sent_count: int = 0
        self.total_sent: int = 0
        self.total_skipped_dup: int = 0

    # ---- config ----

    @property
    def ready(self) -> bool:
        return bool(self._bot_token) and bool(self._chat_id)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def chat_id(self) -> str:
        return self._chat_id

    @property
    def has_token(self) -> bool:
        return bool(self._bot_token)

    def _effective_proxy(self) -> Tuple[Optional[str], str]:
        """Return (proxy_url, source) where source is 'telegram'/'scanner'/'none'."""
        if self._proxy:
            return self._proxy, "telegram"
        if self._fallback_proxy_provider is not None:
            try:
                fp = self._fallback_proxy_provider()
            except Exception:  # noqa: BLE001
                fp = None
            if fp:
                return fp, "scanner"
        return None, "none"

    def public_state(self) -> Dict[str, Any]:
        """JSON-serializable snapshot used by GET /api/state."""
        proxy, source = self._effective_proxy()
        return {
            "enabled": self._enabled,
            "ready": self.ready,
            "has_token": self.has_token,
            "chat_id": self._chat_id,
            "proxy": _mask_proxy(proxy),
            "proxy_source": source,
            "proxy_configured": _mask_proxy(self._proxy) if self._proxy else "",
            "last_error": self.last_error,
            "last_sent_at": self.last_sent_at,
            "last_sent_count": self.last_sent_count,
            "total_sent": self.total_sent,
            "total_skipped_dup": self.total_skipped_dup,
            "dedup_size": len(self._seen),
            "dedup_capacity": self._dedup_capacity,
        }

    def update_config(
        self,
        *,
        enabled: Optional[bool] = None,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        proxy: Optional[str] = None,
    ) -> None:
        if enabled is not None:
            self._enabled = bool(enabled)
        if bot_token is not None:
            self._bot_token = bot_token.strip()
        if chat_id is not None:
            self._chat_id = chat_id.strip()
        if proxy is not None:
            self._proxy = proxy.strip()

    def reset_dedup(self) -> None:
        self._seen.clear()

    # ---- notification ----

    def _filter_new(self, events: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
        """Split events into (new, dup_count) and register new keys."""
        new_events: List[Dict[str, Any]] = []
        dup = 0
        now = time.time()
        for ev in events:
            key = _dedup_key(ev)
            if key in self._seen:
                dup += 1
                continue
            new_events.append(ev)
            self._seen[key] = now
            # Bound the dedup memory.
            while len(self._seen) > self._dedup_capacity:
                self._seen.popitem(last=False)
        return new_events, dup

    def _format_event(self, ev: Dict[str, Any]) -> str:
        ts = ev.get("timestamp")
        time_part = ""
        if ts:
            try:
                time_part = datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
            except Exception:  # noqa: BLE001
                time_part = ""
        pair = _esc(ev.get("pair", "?"))
        buy_ex = _esc(ev.get("buy_exchange", "?"))
        sell_ex = _esc(ev.get("sell_exchange", "?"))
        spread = ev.get("spread")
        try:
            spread_str = f"{float(spread):.2f}%" if spread is not None else "—"
        except (TypeError, ValueError):
            spread_str = "—"
        buy_price = _esc(_fmt_price(ev.get("buy_price")))
        sell_price = _esc(_fmt_price(ev.get("sell_price")))
        profit = _esc(_fmt_usd(ev.get("ob_profit_usdt")))
        transfer = "✓" if ev.get("has_transfer") else "·"
        prefix = f"{time_part} · " if time_part else ""
        return (
            f"{prefix}<b>{pair}</b>  {buy_ex} → {sell_ex}  "
            f"<b>{spread_str}</b>  "
            f"({buy_price} → {sell_price})  "
            f"профит {profit} {transfer}"
        )

    def _build_messages(self, events: List[Dict[str, Any]]) -> List[str]:
        """Compose 1+ messages out of ``events`` respecting Telegram's limit."""
        if not events:
            return []
        header = f"🟢 <b>Арбитраж: {len(events)} новых ситуаций</b>"
        lines = [self._format_event(ev) for ev in events]
        messages: List[str] = []
        current = header
        for line in lines:
            candidate = current + "\n" + line
            if len(candidate) > MAX_MSG:
                messages.append(current)
                current = "(продолжение)\n" + line
            else:
                current = candidate
        messages.append(current)
        return messages

    def _build_session(self, proxy: Optional[str]) -> Tuple[aiohttp.ClientSession, Optional[str]]:
        """Return (session, http_proxy_for_request).

        For SOCKS proxies we route via :class:`ProxyConnector`; for HTTP(S)
        proxies we pass ``proxy=`` per request because aiohttp's connector
        doesn't handle proxy auth headers the same way.
        """
        timeout = aiohttp.ClientTimeout(total=20)
        if not proxy:
            return aiohttp.ClientSession(timeout=timeout), None
        scheme = proxy.split("://", 1)[0].lower() if "://" in proxy else ""
        if scheme in ("socks5", "socks4", "socks5h", "socks4a"):
            if ProxyConnector is None:
                raise RuntimeError(
                    "SOCKS proxies require the aiohttp-socks package "
                    "(pip install aiohttp-socks)."
                )
            connector = ProxyConnector.from_url(proxy)
            return aiohttp.ClientSession(timeout=timeout, connector=connector), None
        # http / https
        return aiohttp.ClientSession(timeout=timeout), proxy

    async def _send_raw(self, text: str) -> None:
        """Low-level send; raises on transport / API errors."""
        if not self.ready:
            raise RuntimeError("Telegram bot is not configured")
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        proxy, _ = self._effective_proxy()
        session, http_proxy = self._build_session(proxy)
        try:
            kwargs: Dict[str, Any] = {}
            if http_proxy:
                kwargs["proxy"] = http_proxy
            async with session.post(url, json=payload, **kwargs) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"Telegram HTTP {resp.status}: {body[:200]}")
                try:
                    import json
                    data = json.loads(body)
                except Exception:  # noqa: BLE001
                    data = {}
                if isinstance(data, dict) and data.get("ok") is False:
                    desc = data.get("description") or body[:200]
                    raise RuntimeError(f"Telegram API error: {desc}")
        finally:
            await session.close()

    async def send_test(self, message: Optional[str] = None) -> Dict[str, Any]:
        """Force-send a probe message; bypasses ``enabled`` and dedup."""
        if not self.ready:
            self.last_error = "Не задан bot_token и/или chat_id"
            return {"ok": False, "error": self.last_error}
        text = message or (
            "🧪 <b>Тест Telegram</b>\nСканер арбитража: уведомления настроены."
        )
        async with self._send_lock:
            try:
                await self._send_raw(text)
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                logger.exception("Telegram test send failed")
                return {"ok": False, "error": self.last_error}
            self.last_error = None
            self.last_sent_at = time.time()
            self.last_sent_count = 1
            self.total_sent += 1
            return {"ok": True}

    async def notify_events(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Notify about events that have NOT been sent before.

        Returns a small summary so the caller can log it. Never raises;
        errors are stored in ``self.last_error``.
        """
        if not events or not self._enabled or not self.ready:
            return {"sent": 0, "duplicates": 0, "skipped_reason": "disabled" if not self._enabled else ("no_config" if not self.ready else "empty")}

        new_events, dup = self._filter_new(events)
        self.total_skipped_dup += dup
        if not new_events:
            return {"sent": 0, "duplicates": dup, "skipped_reason": "all_duplicates"}

        messages = self._build_messages(new_events)
        async with self._send_lock:
            sent_ok = 0
            for msg in messages:
                try:
                    await self._send_raw(msg)
                    sent_ok += 1
                    # Be polite — Telegram limits ~1msg/sec to the same chat.
                    if len(messages) > 1:
                        await asyncio.sleep(1.1)
                except Exception as exc:  # noqa: BLE001
                    self.last_error = str(exc)
                    logger.exception("Telegram send failed")
                    break
            if sent_ok > 0:
                self.last_error = None
                self.last_sent_at = time.time()
                self.last_sent_count = len(new_events) if sent_ok == len(messages) else 0
                self.total_sent += sent_ok
            return {
                "sent": sent_ok,
                "duplicates": dup,
                "new_events": len(new_events),
                "messages": len(messages),
                "error": self.last_error,
            }
