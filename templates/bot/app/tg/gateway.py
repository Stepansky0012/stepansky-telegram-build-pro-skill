"""TgGateway — the only place in the process that calls the Bot API.

Limiter -> attempt -> taxonomy -> retry/degrade/raise, with one structured log
line and one metric per call. A bare `bot.send_*` in a handler bypasses all of
it, which is why tg_lint forbids it.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

from ..observability import metrics, trace_id_var
from .errors import SOFT_OK, ChatUnreachable, SendDegraded, TgAction, classify
from .limiter import Limiter

log = logging.getLogger("tg.gateway")

MAX_ATTEMPTS = 3
STATUS_THROTTLE_SEC = 1.2       # Presence/progress edits; the send budget is ~1/s


def _is_group(chat_id: int | str) -> bool:
    return isinstance(chat_id, int) and chat_id < 0


class TgGateway:
    def __init__(self, bot: Bot, limiter: Limiter | None = None):
        self.bot = bot
        self.lim = limiter or Limiter(metrics=metrics)
        self._last_status_edit: dict[int, float] = {}

    # ------------------------------------------------------------------ core
    async def call(self, method: str, *, chat_id: int | None = None,
                   soft: bool = False, **params):
        attempt = 0
        while True:
            attempt += 1
            if chat_id is not None:
                await self.lim.acquire(chat_id, _is_group(chat_id))
            t0 = time.monotonic()
            try:
                result = await self._invoke(method, params)
                self._observe(method, "ok", t0, attempt)
                return result
            except TelegramRetryAfter as e:
                self.lim.penalize(chat_id, e.retry_after)
                self._observe(method, "retry_after", t0, attempt, str(e))
                if attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(float(e.retry_after))
                    continue
                if soft:
                    return None
                raise SendDegraded(method) from e
            except TelegramAPIError as e:
                status = getattr(e, "code", None) or 400
                action = classify(status, str(e))
                metrics.inc("tg_api_error", code=str(status), action=action.value)
                self._observe(method, action.value, t0, attempt, str(e))

                if action is TgAction.RETRY and attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(self._backoff(attempt))
                    continue
                if action is TgAction.RETRY:
                    action = TgAction.DEGRADE
                if soft and action in SOFT_OK:
                    return None
                if action is TgAction.IGNORE:
                    return None
                if action is TgAction.INACTIVE:
                    raise ChatUnreachable(chat_id) from e
                if action is TgAction.DEGRADE:
                    raise SendDegraded(method) from e
                raise                       # FIX and ALERT are bugs, not conditions

    async def _invoke(self, method: str, params: dict):
        """Prefer the typed method when the installed aiogram has it; fall back to
        the raw request layer for anything newer than the library."""
        fn = getattr(self.bot, _snake(method), None)
        if callable(fn):
            return await fn(**params)
        from .raw import raw_request
        return await raw_request(self.bot, method, params)

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(2 ** attempt, 8) * (0.5 + random.random())

    @staticmethod
    def _observe(method: str, outcome: str, t0: float, attempt: int,
                 err: str | None = None) -> None:
        dt = time.monotonic() - t0
        metrics.inc("tg_api_calls", method=method, outcome=outcome)
        metrics.observe("tg_api_latency", dt, method=method)
        log.info("tg_api", extra={"event": "tg_api", "method": method,
                                  "outcome": outcome, "attempt": attempt,
                                  "latency_ms": round(dt * 1000), "error": err})

    # ------------------------------------------------------------- façade
    async def send_message(self, chat_id: int, text: str, **kw):
        return await self.call("sendMessage", chat_id=chat_id, text=text, **kw)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str,
                                *, soft: bool = False, **kw):
        return await self.call("editMessageText", chat_id=chat_id,
                               message_id=message_id, text=text, soft=soft, **kw)

    async def edit_status(self, chat_id: int, message_id: int, text: str, **kw):
        """Throttled, coalescing, always soft. Presence only — a separate method
        so throttling cannot be forgotten by a caller or applied by accident."""
        now = time.monotonic()
        if now - self._last_status_edit.get(chat_id, 0.0) < STATUS_THROTTLE_SEC:
            return None
        self._last_status_edit[chat_id] = now
        return await self.edit_message_text(chat_id, message_id, text, soft=True, **kw)

    async def set_message_reaction(self, chat_id: int, message_id: int,
                                   reaction: list[dict], *, soft: bool = True):
        return await self.call("setMessageReaction", chat_id=chat_id,
                               message_id=message_id, reaction=reaction, soft=soft)

    async def send_chat_action(self, chat_id: int, action: str, **kw):
        return await self.call("sendChatAction", chat_id=chat_id, action=action,
                               soft=True, **kw)

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None,
                                    **kw):
        return await self.call("answerCallbackQuery",
                               callback_query_id=callback_query_id, text=text,
                               soft=True, **kw)

    async def copy_message(self, chat_id: int, from_chat_id: int, message_id: int, **kw):
        return await self.call("copyMessage", chat_id=chat_id, from_chat_id=from_chat_id,
                               message_id=message_id, **kw)

    async def forward_message(self, chat_id: int, from_chat_id: int, message_id: int, **kw):
        return await self.call("forwardMessage", chat_id=chat_id,
                               from_chat_id=from_chat_id, message_id=message_id, **kw)

    async def raw(self, method: str, *, api_version: str, chat_id: int | None = None,
                  soft: bool = False, **params):
        """Escape hatch for methods aiogram does not wrap yet (10.x Rich Messages,
        ephemeral, guest queries...). `api_version` is mandatory so a grep tells
        you what to revisit after a library upgrade."""
        log.debug("tg_raw", extra={"event": "tg_raw", "method": method,
                                   "detail": f"api {api_version}"})
        return await self.call(method, chat_id=chat_id, soft=soft, **params)


def _snake(camel: str) -> str:
    out = []
    for ch in camel:
        if ch.isupper():
            out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)
