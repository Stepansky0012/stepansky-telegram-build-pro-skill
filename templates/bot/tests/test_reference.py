"""Reference tests. The unit under test is the set of outgoing API calls.

Run: pytest -q
These pass without a network, without a token, and without Telegram.
"""
from __future__ import annotations

import asyncio

import pytest

from app.tg.errors import TgAction, classify
from app.tg.limiter import Bucket, Limiter
from app.tg.presence import PRESENCE, TERMINAL, Presence, ProcState
from app.tg.text import E, H, split_safe, u16len

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #

class FakeMessage:
    def __init__(self, mid: int = 500):
        self.message_id = mid


class FakeGateway:
    """Records outgoing calls. Assert on `calls`, not on 'did not raise'."""

    def __init__(self, *, fail_reactions: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.fail_reactions = fail_reactions
        self._mid = 600

    def _rec(self, method: str, **kw):
        self.calls.append((method, kw))

    async def send_message(self, chat_id, text, **kw):
        self._rec("sendMessage", chat_id=chat_id, text=text, **kw)
        self._mid += 1
        return FakeMessage(self._mid)

    async def edit_message_text(self, chat_id, message_id, text, **kw):
        self._rec("editMessageText", chat_id=chat_id, message_id=message_id,
                  text=text, **kw)
        return FakeMessage(message_id)

    async def edit_status(self, chat_id, message_id, text, **kw):
        return await self.edit_message_text(chat_id, message_id, text, **kw)

    async def set_message_reaction(self, chat_id, message_id, reaction, **kw):
        self._rec("setMessageReaction", chat_id=chat_id, message_id=message_id,
                  reaction=reaction)
        return None if self.fail_reactions else True

    async def send_chat_action(self, chat_id, action, **kw):
        self._rec("sendChatAction", chat_id=chat_id, action=action)
        return True

    async def answer_callback_query(self, callback_query_id, text=None, **kw):
        self._rec("answerCallbackQuery", callback_query_id=callback_query_id)
        return True

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]


def call_of(gw: FakeGateway, method: str) -> dict | None:
    for m, kw in gw.calls:
        if m == method:
            return kw
    return None


# --------------------------------------------------------------------------- #
# presence
# --------------------------------------------------------------------------- #

async def test_presence_always_reaches_a_terminal_state_on_success():
    gw = FakeGateway()
    async with Presence(gw, 1, 100, min_visible_sec=0) as p:
        await p.set(ProcState.WORKING)
    assert p.state in TERMINAL
    assert "setMessageReaction" in gw.methods()


async def test_presence_marks_failed_and_reraises():
    gw = FakeGateway()
    with pytest.raises(ValueError):
        async with Presence(gw, 1, 100, min_visible_sec=0) as p:
            await p.set(ProcState.WORKING)
            raise ValueError("boom")
    assert p.state is ProcState.FAILED           # a stuck "Работаю..." is a bug
    reactions = [kw["reaction"][0]["emoji"] for m, kw in gw.calls
                 if m == "setMessageReaction"]
    assert PRESENCE[ProcState.FAILED].reaction in reactions


async def test_presence_marks_cancelled_on_cancellation():
    gw = FakeGateway()
    with pytest.raises(asyncio.CancelledError):
        async with Presence(gw, 1, 100, min_visible_sec=0) as p:
            await p.set(ProcState.WORKING)
            raise asyncio.CancelledError
    assert p.state is ProcState.CANCELLED


async def test_presence_reaction_falls_back_when_rejected():
    gw = FakeGateway(fail_reactions=True)
    async with Presence(gw, 1, 100, min_visible_sec=0) as p:
        await p.set(ProcState.STUDYING)
    emojis = [kw["reaction"][0]["emoji"] for m, kw in gw.calls
              if m == "setMessageReaction"]
    assert "👍" in emojis            # degraded to the safe reaction, not to a message


async def test_status_message_becomes_the_answer():
    gw = FakeGateway()
    async with Presence(gw, 1, 100, min_visible_sec=0) as p:
        await p.set(ProcState.WORKING)
        await p.replace_with_result("готово")
    edits = [kw for m, kw in gw.calls if m == "editMessageText"]
    assert edits and edits[-1]["text"] == "готово"


# --------------------------------------------------------------------------- #
# text — the bug is data-dependent, so the test is data-driven
# --------------------------------------------------------------------------- #

DANGEROUS = ["ул. Ленина (д.5)", "a_b*c", "file[1].pdf", "-42%", "```",
             "R&D <ops>", "100$ = 90€", "München", "🔎x🔎", "|pipe|"]


@pytest.mark.parametrize("s", DANGEROUS)
def test_html_escapes_and_never_breaks_structure(s):
    out = str(H.b("Заявка ") + H.code(s))
    assert out.count("<code>") == 1 and out.count("</code>") == 1
    assert "<b>" in out
    assert "<" not in s or "&lt;" in out


@pytest.mark.parametrize("s", DANGEROUS)
def test_entity_offsets_are_utf16(s):
    text, ents = E().emoji("🔎").text(" ").bold(s).build()
    bold = [e for e in ents if e["type"] == "bold"][0]
    assert bold["offset"] == 3                    # 🔎 == 2 u16 units, then a space
    assert bold["length"] == u16len(s)


def test_custom_emoji_must_wrap_exactly_one_emoji():
    with pytest.raises(ValueError):
        E().emoji("ab", "123")
    with pytest.raises(ValueError):
        E().emoji("", "123")


def test_h_rejects_unsafe_urls():
    with pytest.raises(ValueError):
        H.a("x", 'javascript:alert("x")')


def test_split_safe_keeps_entities_inside_their_chunk():
    text, ents = E().bold("абзац " * 200).build()
    chunks = split_safe(text, ents, limit=200)
    assert len(chunks) > 1
    for chunk, ce in chunks:
        for e in ce:
            assert e["offset"] >= 0
            assert e["offset"] + e["length"] <= u16len(chunk)


# --------------------------------------------------------------------------- #
# errors and limiter
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("status,desc,expected", [
    (400, "message is not modified", TgAction.IGNORE),
    (400, "can't parse entities: unexpected end", TgAction.FIX),
    (400, "BUTTON_DATA_INVALID", TgAction.FIX),
    (403, "bot was blocked by the user", TgAction.INACTIVE),
    (429, "Too Many Requests", TgAction.RETRY),
    (502, "Bad Gateway", TgAction.RETRY),
    (400, "some brand new failure nobody mapped", TgAction.ALERT),
])
def test_error_taxonomy(status, desc, expected):
    assert classify(status, desc) is expected


def test_unknown_error_is_never_ignored():
    assert classify(418, "I am a teapot") is TgAction.ALERT


async def test_limiter_penalty_honours_retry_after(monkeypatch):
    lim = Limiter()
    slept: list[float] = []

    async def fake_sleep(d):
        slept.append(d)

    monkeypatch.setattr("app.tg.limiter.asyncio.sleep", fake_sleep)
    lim.penalize(42, 7.0)
    await lim.acquire(42)
    assert slept and slept[0] <= 7.0 and slept[0] > 0


def test_bucket_refills_at_the_configured_rate():
    b = Bucket(capacity=1, refill_per_sec=1.0)
    assert b.wait_for(100.0) == 0.0
    b.take(100.0)
    assert b.wait_for(100.0) == pytest.approx(1.0, abs=0.01)
    assert b.wait_for(101.0) == 0.0
