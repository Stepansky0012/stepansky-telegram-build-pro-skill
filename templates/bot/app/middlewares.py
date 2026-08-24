"""Middleware chain. The order is load-bearing.

  trace  -> dedup -> user -> i18n -> log -> error boundary

trace before everything (every later line needs the id); dedup before anything
with a side effect (Telegram retries webhooks); the error boundary outermost of
the handler so its user-facing message can still be sent.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from .observability import metrics, trace_id_var, update_id_var, user_id_var
from .tg.errors import ChatUnreachable, SendDegraded, user_safe_reason

log = logging.getLogger("mw")
Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]
DEDUP_TTL_SEC = 600


class TraceMiddleware(BaseMiddleware):
    async def __call__(self, handler: Handler, event: TelegramObject, data: dict):
        upd: Update | None = data.get("event_update")
        trace = uuid.uuid4().hex[:12]
        tok = trace_id_var.set(trace)
        utok = update_id_var.set(getattr(upd, "update_id", None))
        user = data.get("event_from_user")
        uptok = user_id_var.set(getattr(user, "id", None))
        data["trace_id"] = trace
        try:
            metrics.inc("tg_updates_total",
                        update_type=upd.event_type if upd else "unknown")
            log.info("update.received", extra={
                "event": "update.received",
                "update_type": upd.event_type if upd else "unknown"})
            return await handler(event, data)
        finally:
            trace_id_var.reset(tok)
            update_id_var.reset(utok)
            user_id_var.reset(uptok)


class DedupMiddleware(BaseMiddleware):
    """Webhook deliveries repeat. Un-deduplicated retries double-send and
    double-charge. Redis SETNX is the whole mechanism."""

    def __init__(self, redis, ttl: int = DEDUP_TTL_SEC):
        self.redis, self.ttl = redis, ttl

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict):
        upd: Update | None = data.get("event_update")
        uid = getattr(upd, "update_id", None)
        if uid is None or self.redis is None:
            return await handler(event, data)
        fresh = await self.redis.set(f"tg:upd:{uid}", "1", ex=self.ttl, nx=True)
        if not fresh:
            log.debug("update.duplicate", extra={"event": "update.duplicate",
                                                 "duplicate": True})
            metrics.inc("tg_updates_duplicate")
            return None
        return await handler(event, data)


class UserMiddleware(BaseMiddleware):
    """Resolve the user once. Handlers never query for the user themselves."""

    def __init__(self, users_service):
        self.users = users_service

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict):
        tg_user = data.get("event_from_user")
        if tg_user is not None and self.users is not None:
            data["user"] = await self.users.get_or_create(
                tg_user.id, username=tg_user.username,
                language_code=tg_user.language_code)
        return await handler(event, data)


class I18nMiddleware(BaseMiddleware):
    def __init__(self, default: str = "ru", supported: tuple[str, ...] = ("ru", "en")):
        self.default, self.supported = default, supported

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict):
        user = data.get("user")
        stored = getattr(user, "locale", None)
        tg_user = data.get("event_from_user")
        code = (stored or getattr(tg_user, "language_code", "") or "")[:2]
        data["locale"] = code if code in self.supported else self.default
        return await handler(event, data)


class LogMiddleware(BaseMiddleware):
    async def __call__(self, handler: Handler, event: TelegramObject, data: dict):
        name = getattr(data.get("handler"), "callback", None)
        handler_name = getattr(name, "__qualname__", "unknown")
        t0 = time.monotonic()
        log.info("handler.enter", extra={"event": "handler.enter",
                                         "handler": handler_name})
        outcome = "ok"
        try:
            return await handler(event, data)
        except Exception:
            outcome = "error"
            raise
        finally:
            dt = time.monotonic() - t0
            metrics.observe("tg_handler_latency", dt, handler=handler_name)
            log.info("handler.exit", extra={
                "event": "handler.exit", "handler": handler_name,
                "outcome": outcome, "latency_ms": round(dt * 1000)})


class ErrorBoundaryMiddleware(BaseMiddleware):
    """Catch, classify, log, tell the user something actionable, re-raise bugs.

    The user-facing message is a product surface, not a fallback:
    what happened - what it means - what to do, plus the trace id so support
    can find the line. `except Exception: pass` is never acceptable.
    """

    def __init__(self, gw):
        self.gw = gw

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict):
        try:
            return await handler(event, data)
        except ChatUnreachable as e:
            log.warning("error", extra={"event": "error", "action": "inactive",
                                        "chat_id": e.chat_id})
            return None                          # nothing to tell an unreachable chat
        except SendDegraded as e:
            log.warning("error", extra={"event": "error", "action": "degrade",
                                        "method": e.method})
            await self._tell(data, user_safe_reason(e))
            return None
        except Exception as e:                                    # noqa: BLE001
            handler_name = getattr(getattr(data.get("handler"), "callback", None),
                                   "__qualname__", "unknown")
            metrics.inc("tg_handler_errors", handler=handler_name,
                        exc_type=type(e).__name__)
            log.exception("error", extra={"event": "error", "action": "fix",
                                          "handler": handler_name})
            await self._tell(data, user_safe_reason(e), with_trace=True)
            raise                                # surface to alerting; do not swallow

    async def _tell(self, data: dict, text: str, *, with_trace: bool = False) -> None:
        chat = getattr(data.get("event_chat"), "id", None)
        if chat is None:
            return
        if with_trace:
            text = f"{text}\nКод запроса: {trace_id_var.get()}"
        try:
            await self.gw.send_message(chat, text)
        except Exception:                                        # noqa: BLE001
            log.warning("error", extra={"event": "error",
                                        "detail": "could not deliver error message"})


def install(dp, *, gw, redis=None, users=None) -> None:
    """Outermost first — aiogram runs outer middleware before inner."""
    for mw in (TraceMiddleware(), ErrorBoundaryMiddleware(gw),
               DedupMiddleware(redis), UserMiddleware(users), I18nMiddleware(),
               LogMiddleware()):
        dp.update.outer_middleware(mw)
