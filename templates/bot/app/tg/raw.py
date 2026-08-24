"""Raw Bot API access for methods the installed library does not wrap yet.

Frameworks lag the API by weeks. Bot API 10.0-10.2 (guest mode, rich messages,
ephemeral messages, communities) landed faster than typed wrappers. The wrong
answer is to wait or to fake the feature with editMessageText; the right answer
is one audited escape hatch that still goes through the gateway.

Always call this via `TgGateway.raw(method, api_version=...)`, never directly:
that is what keeps the limiter, the retries, the taxonomy and the logs.
"""
from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.methods.base import TelegramMethod


class RawMethod(TelegramMethod[Any]):
    """A Bot API call described by name and a plain dict of parameters.

    Build the payload from the documentation you fetched THIS session
    (skills/telegram/references/api-map.md), not from memory.
    """

    __returning__ = Any
    __api_method__ = ""

    model_config = {"arbitrary_types_allowed": True, "extra": "allow"}

    def __init__(self, method: str, params: dict[str, Any]):
        clean = {k: v for k, v in params.items() if v is not None}
        super().__init__(**clean)
        object.__setattr__(self, "_method_name", method)

    @property
    def __api_method__(self) -> str:            # type: ignore[override]
        return getattr(self, "_method_name")


async def raw_request(bot: Bot, method: str, params: dict[str, Any]):
    """One-shot raw call. Kept tiny on purpose — the interesting logic lives in
    the gateway, and this must stay a transport detail."""
    return await bot(RawMethod(method, params))


# --------------------------------------------------------------------------- #
# Thin, documented wrappers for the 10.x surface. Each names its API version so
# `grep "api_version"` lists everything to re-check after an upgrade.
# Field names marked ✱ must be verified against the live docs before first use.
# --------------------------------------------------------------------------- #

async def send_rich_message(gw, chat_id: int, blocks: list[dict], **kw):
    """Bot API 10.1 — sendRichMessage. ✱ verify `blocks` field name."""
    return await gw.raw("sendRichMessage", api_version="10.1",
                        chat_id=chat_id, blocks=blocks, **kw)


async def send_rich_message_draft(gw, chat_id: int, blocks: list[dict], **kw):
    """Bot API 10.1 — sendRichMessageDraft. Streams a partial rich message.
    NEVER stream with editMessageText: it re-parses the whole payload and
    destroys the formatting mid-stream."""
    return await gw.raw("sendRichMessageDraft", api_version="10.1",
                        chat_id=chat_id, blocks=blocks, **kw)


async def send_ephemeral(gw, chat_id: int, text: str, receiver_user_id: int, **kw):
    """Bot API 10.2 — a group message only one user can see. ✱ verify the
    parameter name `receiver_user_id`. Visibility, not confidentiality."""
    return await gw.raw("sendMessage", api_version="10.2", chat_id=chat_id,
                        text=text, receiver_user_id=receiver_user_id, **kw)


async def edit_ephemeral_message_text(gw, **kw):
    """Bot API 10.2 — editEphemeralMessageText."""
    return await gw.raw("editEphemeralMessageText", api_version="10.2", **kw)


async def delete_ephemeral_message(gw, **kw):
    """Bot API 10.2 — deleteEphemeralMessage."""
    return await gw.raw("deleteEphemeralMessage", api_version="10.2", **kw)


async def send_message_draft(gw, chat_id: int, text: str, **kw):
    """Bot API 9.3 (all bots since 9.5) — plain-text streaming."""
    return await gw.raw("sendMessageDraft", api_version="9.5",
                        chat_id=chat_id, text=text, **kw)
