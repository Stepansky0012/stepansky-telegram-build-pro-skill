"""Error taxonomy: every Telegram API error maps to exactly one named action.

Unknown -> ALERT, never IGNORE. A silent default is how a bot drifts out of sync
with the API and nobody notices for a quarter.

Full table with rationale: skills/telegram/references/errors.md
"""
from __future__ import annotations

import re
from enum import Enum


class TgAction(str, Enum):
    IGNORE = "ignore"        # expected, not an error
    RETRY = "retry"          # honour backoff
    DEGRADE = "degrade"      # fall back to a simpler send
    INACTIVE = "inactive"    # user/chat unreachable, stop trying forever
    FIX = "fix"              # bug in our code, must surface
    ALERT = "alert"          # unknown, page a human


class ChatUnreachable(RuntimeError):
    def __init__(self, chat_id: int | None):
        super().__init__(f"chat {chat_id} is unreachable")
        self.chat_id = chat_id


class SendDegraded(RuntimeError):
    def __init__(self, method: str):
        super().__init__(f"{method} degraded")
        self.method = method


_RULES: list[tuple[re.Pattern[str], TgAction]] = [
    (re.compile(r"message is not modified", re.I), TgAction.IGNORE),
    (re.compile(r"query is too old|query id is invalid", re.I), TgAction.IGNORE),
    (re.compile(r"reaction is not (valid|available)", re.I), TgAction.IGNORE),
    (re.compile(r"message to (edit|delete) not found", re.I), TgAction.DEGRADE),
    (re.compile(r"message can'?t be edited", re.I), TgAction.DEGRADE),
    (re.compile(r"not enough rights", re.I), TgAction.DEGRADE),
    (re.compile(r"can'?t parse entities", re.I), TgAction.FIX),
    (re.compile(r"BUTTON_DATA_INVALID", re.I), TgAction.FIX),
    (re.compile(r"message text is empty", re.I), TgAction.FIX),
    (re.compile(r"wrong (file identifier|remote file id)", re.I), TgAction.FIX),
    (re.compile(r"STICKER_[A-Z_]+|STICKERSET_INVALID", re.I), TgAction.FIX),
    (re.compile(r"terminated by other getUpdates", re.I), TgAction.FIX),
    (re.compile(r"bot was blocked by the user", re.I), TgAction.INACTIVE),
    (re.compile(r"user is deactivated", re.I), TgAction.INACTIVE),
    (re.compile(r"bot was kicked", re.I), TgAction.INACTIVE),
    (re.compile(r"CHAT_WRITE_FORBIDDEN", re.I), TgAction.INACTIVE),
    (re.compile(r"chat not found|PEER_ID_INVALID", re.I), TgAction.INACTIVE),
]

# Errors a `soft=True` call may swallow. FIX and ALERT never qualify:
# a cosmetic call must not fail the user's work, but a bug is still a bug.
SOFT_OK = frozenset({TgAction.IGNORE, TgAction.DEGRADE})


def classify(status: int, description: str | None) -> TgAction:
    if status == 429:
        return TgAction.RETRY
    if status in (500, 502, 503, 504):
        return TgAction.RETRY
    if status == 401:
        return TgAction.FIX
    for pattern, action in _RULES:
        if pattern.search(description or ""):
            return action
    return TgAction.ALERT


def user_safe_reason(exc: BaseException) -> str:
    """What the user reads. Never a traceback, never a bare code.
    Shape: what happened - what it means - what to do."""
    if isinstance(exc, ChatUnreachable):
        return "Не удалось доставить сообщение."
    if isinstance(exc, SendDegraded):
        return ("Telegram не принял сообщение с первого раза. "
                "Попробуйте ещё раз через минуту.")
    if isinstance(exc, TimeoutError):
        return ("Внешний сервис не ответил вовремя. Работа не потеряна — "
                "повторите операцию.")
    return ("Внутренняя ошибка. Мы её видим и уже разбираемся. "
            "Если это срочно, напишите в поддержку и укажите код запроса.")
