# Response mode — the contextual decision function

A bot is predictable when the same situation always produces the same *shape* of response. Shape is not a per-handler choice; it is computed. Nine modes, one function.

## The nine modes

| Mode | API | Semantics the user reads |
|---|---|---|
`PLAIN` | `sendMessage` | "a new thing is happening" |
`REPLY` | `sendMessage` + `reply_parameters.message_id` | "this is about *that* message of yours" |
`QUOTE` | `reply_parameters` + `quote`, `quote_position` | "this is about *this part* of that message" |
`REACTION` | `setMessageReaction` | "received / noted", zero noise |
`EDIT` | `editMessageText` / `editMessageReplyMarkup` | "the thing I already told you about changed" |
`EPHEMERAL` | `sendMessage` + `receiver_user_id` (10.2+) | "this is for you only, in this group" |
`FORWARD` | `forwardMessage` | "someone else said this, and you can see who" |
`COPY` | `copyMessage` | "here is that content, authorship irrelevant/hidden" |
`RICH` | `sendRichMessage` / `sendRichMessageDraft` | "a long structured answer, possibly still being written" |

## Semantics that are routinely confused

**`FORWARD` vs `COPY`.** `forwardMessage` preserves the original author header and links back to the source; it also inherits the source's forward restrictions (`protect_content` on the origin makes it fail). `copyMessage` sends the *content* as a new message from your bot — no attribution, no link, and it can carry a new caption and a new keyboard. Rule: attribution is a product decision. If the user needs to know who said it → `FORWARD`. If you are republishing your own channel content or a template → `COPY`.

**`REPLY` vs `QUOTE`.** Reply points at a whole message. `quote` points at a substring of it — use it when the user wrote three things and you are addressing one clause, or when you are correcting a specific value. `quote` must be an exact substring of the original text or Telegram rejects it, so slice from the actual `message.text`, never from your own reconstruction.

**`REACTION` is a first-class response.** It is the right answer far more often than a message. "Got your file", "seen", "approved" — all reactions. It costs the user zero scroll and cannot break formatting. `is_big=True` only for genuinely celebratory outcomes.

**`EPHEMERAL` replaces the DM-the-staffer pattern.** Before 10.2 the only way to say something to one person in a group was a DM (which requires them to have started the bot) or a public message. `receiver_user_id` solves both. Admin panels inside group chats are the canonical use.

**`EDIT` is the default for anything with a lifecycle.** Order status, job progress, a settings screen. A new message per state change turns the chat into a log the user has to read backwards.

## The function

```python
from enum import Enum

class Mode(str, Enum):
    PLAIN = "plain"; REPLY = "reply"; QUOTE = "quote"; REACTION = "reaction"
    EDIT = "edit"; EPHEMERAL = "ephemeral"; FORWARD = "forward"
    COPY = "copy"; RICH = "rich"


def choose_response_mode(
    *,
    chat_type: str,                 # "private" | "group" | "supergroup" | "channel"
    is_state_update: bool,          # we already have a message representing this object
    origin_message_id: int | None,  # id of that message, if any
    content_kind: str,              # "ack" | "short" | "long" | "structured" | "relay"
    relay_needs_attribution: bool = False,
    addresses_fragment: str | None = None,   # exact substring of the user's text
    audience: str = "all",          # "all" | "one_user"
    user_sent_multiple: bool = False,
    streaming: bool = False,
) -> Mode:
    """Single source of truth for response shape. Handlers call this, not vibes."""

    # 1. Something we already reported changed -> never a new message.
    if is_state_update and origin_message_id is not None:
        return Mode.EDIT

    # 2. Relaying third-party content.
    if content_kind == "relay":
        return Mode.FORWARD if relay_needs_attribution else Mode.COPY

    # 3. Pure acknowledgement with nothing to add.
    if content_kind == "ack":
        return Mode.REACTION

    # 4. One person inside a shared chat.
    if audience == "one_user" and chat_type in ("group", "supergroup"):
        return Mode.EPHEMERAL          # requires Bot API 10.2+

    # 5. Long or structured -> rich surface (streamed or not).
    if streaming or content_kind in ("long", "structured"):
        return Mode.RICH

    # 6. Pointing at part of what they wrote.
    if addresses_fragment:
        return Mode.QUOTE

    # 7. Ambiguity about which message we answer.
    if chat_type in ("group", "supergroup") or user_sent_multiple:
        return Mode.REPLY

    return Mode.PLAIN
```

Order matters and is deliberate: state updates outrank everything (predictability), relay outranks shape (semantics), acknowledgement outranks verbosity (noise), audience outranks length (privacy).

## Wiring it up

Put it behind one send facade so no handler chooses for itself:

```python
async def respond(gw, ctx, *, text=None, blocks=None, reaction=None, **kw):
    mode = choose_response_mode(**ctx.response_signals(), **kw)
    match mode:
        case Mode.REACTION:
            return await gw.set_message_reaction(ctx.chat_id, ctx.message_id,
                                                 [{"type": "emoji", "emoji": reaction or "👍"}])
        case Mode.EDIT:
            return await gw.edit_message_text(ctx.chat_id, ctx.origin_message_id, text)
        case Mode.EPHEMERAL:
            return await gw.send_message(ctx.chat_id, text, receiver_user_id=ctx.user_id)
        case Mode.QUOTE:
            return await gw.send_message(ctx.chat_id, text, reply_parameters={
                "message_id": ctx.message_id, "quote": ctx.fragment})
        case Mode.REPLY:
            return await gw.send_message(ctx.chat_id, text,
                                        reply_parameters={"message_id": ctx.message_id})
        case Mode.RICH:
            return await gw.send_rich(ctx.chat_id, blocks, stream=ctx.streaming)
        case Mode.FORWARD:
            return await gw.forward_message(ctx.chat_id, ctx.src_chat_id, ctx.src_message_id)
        case Mode.COPY:
            return await gw.copy_message(ctx.chat_id, ctx.src_chat_id, ctx.src_message_id,
                                         caption=text)
        case _:
            return await gw.send_message(ctx.chat_id, text)
```

Log the chosen mode with the `trace_id`. When a user says "the bot answered weirdly", the mode is the first thing you look at.

## Graceful degradation

`EPHEMERAL` needs Bot API 10.2 on Telegram's side and a client that renders it. Keep a documented fallback chain per mode, decided once:

| Mode | If unavailable |
|---|---|
`EPHEMERAL` | `REPLY` in the group with a `@mention`, or DM if the user has started the bot |
`RICH` | `PLAIN` with HTML formatting and manual chunking at 4096 chars |
`QUOTE` | `REPLY` (drop the fragment) |
`REACTION` | nothing — silence is correct here, do not substitute a message |

Never let a mode failure escalate into a louder response than intended. Degrading `REACTION` into a message is a bug, not a fallback.

## Anti-patterns

| Anti-pattern | Why it hurts |
|---|---|
Replying to every message in a private chat | visual noise; reply means "disambiguation needed" and loses meaning if always on |
New message per status change | user has to scroll backwards to learn the current state |
DM instead of ephemeral for group admin actions | fails for anyone who never started the bot |
`FORWARD` for your own template content | shows a confusing author header and inherits restrictions |
A message where a reaction would do | trains users to ignore the bot |
Mode chosen inside the handler | the same situation gets two shapes; users stop predicting the bot |
