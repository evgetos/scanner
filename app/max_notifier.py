"""MAX messenger bot notifier for newly recorded arbitrage events.

Mirrors :class:`telegram_notifier.TelegramNotifier` in design (per-scan
deduplication, message splitting, optional proxy with fallback to the
scanner proxy) but talks to MAX's bot API at ``platform-api.max.ru``.

Docs: https://dev.max.ru/docs-api/methods/POST/messages

API quirks worth noting:

* Auth is a raw ``Authorization: <token>`` header (no ``Bearer`` prefix).
* The recipient is a query parameter: ``?chat_id={id}`` for chats, or
  ``?user_id={id}`` for DMs. We expose this as a single "chat_id" field
  plus a "Тип получателя" select in the UI.
* Body uses ``format: "markdown"`` (we picked markdown over html because
  MAX's docs list a slightly narrower HTML tag set, and markdown covers
  everything we need).
* Per-message text cap is 4000 characters — we aim a bit lower so the
  header always fits.
"""

from __future__ import annotations

import asyncio
import json
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

MAX_API_URL = "https://platform-api.max.ru/messages"
# MAX caps a single message at 4000 chars; leave headroom for the header.
MAX_MSG = 3800


def _md_esc(value: Any) -> str:
    """Escape markdown special chars so user data renders as plain text.

    MAX markdown is similar to CommonMark; we conservatively escape the
    handful of characters that have formatting meaning.
    """
    if value is None:
        return ""
    s = str(value)
    # Order matters — escape backslashes first.
    for ch in ("\\", "*", "_", "~", "`", "[", "]", "(", ")", "<", ">", "#"):
        s = s.replace(ch, "\\" + ch)
    return s


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


class MaxNotifier:
    """Async MAX messenger notifier with per-scan deduplication."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        recipient_kind: str = "chat",
        enabled: bool = True,
        proxy: Optional[str] = None,
        fallback_proxy_provider: Optional[Callable[[], Optional[str]]] = None,
        dedup_capacity: int = 5000,
    ) -> None:
        self._bot_token: str = (bot_token or "").strip()
        self._chat_id: str = (chat_id or "").strip()
        # "chat" → query param ``chat_id``; "user" → query param ``user_id``.
        self._recipient_kind: str = self._normalise_kind(recipient_kind)
        self._enabled: bool = bool(enabled)
        self._proxy: str = (proxy or "").strip()
        self._fallback_proxy_provider = fallback_proxy_provider
        self._seen: "OrderedDict[str, float]" = OrderedDict()
        self._dedup_capacity: int = max(100, int(dedup_capacity))
        self._send_lock = asyncio.Lock()
        self.last_error: Optional[str] = None
        self.last_sent_at: Optional[float] = None
        self.last_sent_count: int = 0
        self.total_sent: int = 0
        self.total_skipped_dup: int = 0

    # ---- config ----

    @staticmethod
    def _normalise_kind(kind: Optional[str]) -> str:
        return "user" if (kind or "").strip().lower() == "user" else "chat"

    @property
    def ready(self) -> bool:
        return bool(self._bot_token) and bool(self._chat_id)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def has_token(self) -> bool:
        return bool(self._bot_token)

    def _effective_proxy(self) -> Tuple[Optional[str], str]:
        if self._proxy:
            return self._proxy, "max"
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
            "recipient_kind": self._recipient_kind,
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
        recipient_kind: Optional[str] = None,
        proxy: Optional[str] = None,
    ) -> None:
        if enabled is not None:
            self._enabled = bool(enabled)
        if bot_token is not None:
            self._bot_token = bot_token.strip()
        if chat_id is not None:
            self._chat_id = chat_id.strip()
        if recipient_kind is not None:
            self._recipient_kind = self._normalise_kind(recipient_kind)
        if proxy is not None:
            self._proxy = proxy.strip()

    def reset_dedup(self) -> None:
        self._seen.clear()

    # ---- notification ----

    def _filter_new(
        self, events: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], int]:
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
        pair = _md_esc(ev.get("pair", "?"))
        buy_ex = _md_esc(ev.get("buy_exchange", "?"))
        sell_ex = _md_esc(ev.get("sell_exchange", "?"))
        spread = ev.get("spread")
        try:
            spread_str = f"{float(spread):.2f}%" if spread is not None else "—"
        except (TypeError, ValueError):
            spread_str = "—"
        # _md_esc the formatted numbers too — they can contain ``.`` (fine)
        # but the e-notation includes ``+`` which isn't a markdown char, so
        # this is a no-op in practice. Still safe.
        buy_price = _md_esc(_fmt_price(ev.get("buy_price")))
        sell_price = _md_esc(_fmt_price(ev.get("sell_price")))
        profit = _md_esc(_fmt_usd(ev.get("ob_profit_usdt")))
        # Optional "Profit at $N" — only show when populated AND the row
        # actually filled some budget.
        budget = ev.get("ob_budget_usdt")
        profit_at = ev.get("ob_profit_at_budget_usdt")
        filled = ev.get("ob_filled_usdt") or 0
        budget_part = ""
        if budget and profit_at is not None and filled > 0:
            budget_part = (
                f" · при {_md_esc(_fmt_usd(budget))}: "
                f"{_md_esc(_fmt_usd(profit_at))}"
            )
        transfer = " ✓" if ev.get("has_transfer") else ""
        prefix = f"{time_part} · " if time_part else ""
        # Note: ``**bold**`` in MAX markdown.
        return (
            f"{prefix}**{pair}**  {buy_ex} → {sell_ex}  "
            f"**{spread_str}**  "
            f"({buy_price} → {sell_price})  "
            f"профит {profit}{budget_part}{transfer}"
        )

    def _build_messages(self, events: List[Dict[str, Any]]) -> List[str]:
        if not events:
            return []
        header = f"🟢 **Арбитраж: {len(events)} новых ситуаций**"
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

    def _build_session(
        self, proxy: Optional[str]
    ) -> Tuple[aiohttp.ClientSession, Optional[str]]:
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
        return aiohttp.ClientSession(timeout=timeout), proxy

    async def _send_raw(self, text: str) -> None:
        """Low-level send; raises on transport / API errors."""
        if not self.ready:
            raise RuntimeError("MAX bot is not configured")
        # Recipient goes into the URL as a query string.
        param = "user_id" if self._recipient_kind == "user" else "chat_id"
        url = f"{MAX_API_URL}?{param}={self._chat_id}"
        payload = {
            "text": text,
            "format": "markdown",
            "notify": True,
            "disable_link_preview": True,
        }
        headers = {
            "Authorization": self._bot_token,
            "Content-Type": "application/json",
        }
        proxy, _ = self._effective_proxy()
        session, http_proxy = self._build_session(proxy)
        try:
            kwargs: Dict[str, Any] = {"headers": headers}
            if http_proxy:
                kwargs["proxy"] = http_proxy
            async with session.post(url, json=payload, **kwargs) as resp:
                body = await resp.text()
                if resp.status >= 400:
                    # Try to surface MAX's own error message; otherwise the
                    # raw body (truncated) so we don't spam logs.
                    try:
                        data = json.loads(body)
                    except Exception:  # noqa: BLE001
                        data = {}
                    if isinstance(data, dict):
                        desc = (
                            data.get("message")
                            or data.get("error")
                            or data.get("description")
                            or body[:200]
                        )
                    else:
                        desc = body[:200]
                    raise RuntimeError(f"MAX HTTP {resp.status}: {desc}")
        finally:
            await session.close()

    async def send_test(self, message: Optional[str] = None) -> Dict[str, Any]:
        """Force-send a probe message; bypasses ``enabled`` and dedup."""
        if not self.ready:
            self.last_error = "Не задан bot_token и/или chat_id"
            return {"ok": False, "error": self.last_error}
        text = message or (
            "🧪 **Тест MAX**\nСканер арбитража: уведомления настроены."
        )
        async with self._send_lock:
            try:
                await self._send_raw(text)
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                logger.exception("MAX test send failed")
                return {"ok": False, "error": self.last_error}
            self.last_error = None
            self.last_sent_at = time.time()
            self.last_sent_count = 1
            self.total_sent += 1
            return {"ok": True}

    async def notify_events(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Notify about events that have NOT been sent before.

        Never raises; errors are stored in ``self.last_error`` instead.
        """
        if not events or not self._enabled or not self.ready:
            reason = (
                "disabled" if not self._enabled
                else ("no_config" if not self.ready else "empty")
            )
            return {"sent": 0, "duplicates": 0, "skipped_reason": reason}

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
                    if len(messages) > 1:
                        # Be polite — pace ourselves at ~1 msg/sec.
                        await asyncio.sleep(1.1)
                except Exception as exc:  # noqa: BLE001
                    self.last_error = str(exc)
                    logger.exception("MAX send failed")
                    break
            if sent_ok > 0:
                self.last_error = None
                self.last_sent_at = time.time()
                self.last_sent_count = (
                    len(new_events) if sent_ok == len(messages) else 0
                )
                self.total_sent += sent_ok
            return {
                "sent": sent_ok,
                "duplicates": dup,
                "new_events": len(new_events),
                "messages": len(messages),
                "error": self.last_error,
            }
