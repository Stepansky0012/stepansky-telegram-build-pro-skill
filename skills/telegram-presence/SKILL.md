---
name: telegram-presence
description: "Use when a Telegram bot does work that takes longer than an instant — AI generation, file processing, external API calls, human moderation, queues — or when users re-tap buttons, ask \"did it work?\", the bot goes silent for seconds, or you need a \"typing\" indicator, a progress message, a reaction on the user's message, or a status that says which phase the work is in."
---

# The Presence Protocol

Silence is the single most common Telegram UX defect. A bot that thinks for four seconds with no signal is indistinguishable from a broken bot, so users re-tap, and you get duplicate work.

But "typing…" alone is not enough either: it says *alive*, not *what phase*. The Presence Protocol makes the bot's internal state machine visible, in the user's own chat, using three simultaneous channels.

## Core principle

**One process state → one bundle of three signals, emitted together, always.**

| Channel | Where it appears | What it conveys |
|---|---|---|
**Reaction** on the *user's* message | next to what they sent | "I have your message and I am at phase X" |
**Status line** in the bot's own message, edited in place | the conversation | the phase, in words, with an animated custom emoji |
**Chat action** | the header ("typing…", "uploading photo…") | native liveness, auto-expires in 5 s |

Three channels because each fails differently: reactions survive scrolling, the status line survives everything and carries detail, chat actions are the only signal that appears *before* any message exists.

## The state machine

Canonical vocabulary. Do not invent per-project names — predictability across bots is the point.

| State | Meaning | Reaction | Status line | Chat action |
|---|---|---|---|---|
`RECEIVED` | update accepted, nothing started | 👀 | — (too fast to show) | `typing` |
`QUEUED` | waiting for a worker/rate limit | ⏳ | `В очереди · N-й` | `typing` |
`STUDYING` | reading input, retrieval, understanding | 🔎 | `Изучаю запрос` | `typing` |
`WORKING` | the actual work | ✍️ | `Работаю · шаг k/n` | phase-specific |
`WAITING_USER` | blocked on the user | ✋ | `Жду ответа` | none |
`WAITING_HUMAN` | blocked on staff/moderation | 🕐 | `На проверке` | none |
`DONE` | success | ✅ | replaced by the result | none |
`FAILED` | failure | ⚠️ | `Не получилось · <reason>` | none |
`CANCELLED` | user aborted | 🚫 | `Отменено` | none |

Reaction emoji must come from Telegram's allowed reaction set — the set is server-controlled, so read it from `getAvailableReactions`/chat capabilities at startup and fall back to 👍/👎 rather than failing a send.

Chat action per phase: `typing` for text, `upload_photo`, `upload_document`, `record_voice`/`upload_voice`, `upload_video`, `find_location`, `choose_sticker`. Using the *right* one is free realism — a bot that says "uploading photo" while generating an image reads as competent.

## Timing contract

| Deadline | Requirement |
|---|---|
≤ 300 ms | `RECEIVED` reaction placed, or `answerCallbackQuery` returned |
≤ 1 s | chat action sent (it expires after 5 s — re-send every 4 s while working) |
≤ 2 s | status message exists, if the work will exceed ~2 s |
every ≤ 5 s | status line updated with real progress, or the user assumes a hang |
on every exit path | terminal state emitted — `DONE`, `FAILED` or `CANCELLED`. **A process that ends in a non-terminal state is a bug**, not an edge case. |

The last row is the one that gets missed: an exception path that returns without clearing `WORKING` leaves a permanent "Работаю…" in someone's chat.

## Implementation

```python
class Presence:
    """One instance per unit of work. Owns the three channels and guarantees
    a terminal state via the context manager."""

    def __init__(self, gw, chat_id, user_message_id, *, trace_id):
        self.gw, self.chat_id, self.umid = gw, chat_id, user_message_id
        self.trace_id = trace_id
        self.status_message_id: int | None = None
        self.state: ProcState | None = None
        self._action_task = None

    async def set(self, state: ProcState, detail: str = "", *, progress=None):
        spec = PRESENCE[state]
        self.state = state
        # 1. reaction on the user's message (best effort, never fatal)
        if spec.reaction:
            await self.gw.set_message_reaction(
                self.chat_id, self.umid,
                [{"type": "emoji", "emoji": spec.reaction}], soft=True)
        # 2. status line, edited in place
        if spec.status:
            text, ents = self._render(spec, detail, progress)
            if self.status_message_id is None:
                m = await self.gw.send_message(self.chat_id, text, entities=ents,
                                               reply_parameters={"message_id": self.umid})
                self.status_message_id = m.message_id
            else:
                await self.gw.edit_message_text(self.chat_id, self.status_message_id,
                                                text, entities=ents, soft=True)
        # 3. chat action heartbeat
        await self._retune_action(spec.action)
        log.info("presence", extra={"trace_id": self.trace_id, "state": state,
                                     "detail": detail})

    def _render(self, spec, detail, progress):
        b = E().emoji(spec.reaction or "•", CUSTOM_EMOJI.get(spec.key)).text(" ")
        b.bold(spec.status)
        if progress:
            b.text(" · ").code(f"{progress[0]}/{progress[1]}")
        if detail:
            b.nl().text(detail)
        return b.build()

    async def __aenter__(self):
        await self.set(ProcState.RECEIVED)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._stop_action()
        if self.state not in TERMINAL:                 # Law: always terminal
            if exc_type is None:
                await self.set(ProcState.DONE)
            elif exc_type is asyncio.CancelledError:
                await self.set(ProcState.CANCELLED)
            else:
                await self.set(ProcState.FAILED, _user_safe_reason(exc))
        return False                                    # never swallow
```

Usage — the whole point is that it is impossible to forget the terminal state:

```python
async with Presence(gw, chat_id, msg.message_id, trace_id=ctx.trace_id) as p:
    await p.set(ProcState.STUDYING)
    docs = await retrieval.search(msg.text)
    await p.set(ProcState.WORKING, progress=(0, len(docs)))
    async for i, chunk in llm.stream(docs):
        await p.set(ProcState.WORKING, progress=(i, len(docs)))   # throttled
    await p.set(ProcState.DONE)
```

Notes that matter:
- `soft=True` on reactions and edits means "an error here must not fail the user's work" — the gateway maps `message is not modified` and reaction failures to IGNORE.
- Status updates are **throttled** in the gateway (min 1.2 s apart per chat) so a fast loop cannot burn the rate limit. Throttling belongs in the gateway, not in the loop.
- The chat-action heartbeat is a task; `_stop_action` is in `__aexit__`, not in the happy path.
- `_user_safe_reason` maps an exception to a sentence a user can act on. Never a traceback, never an error code alone.

## Where the result goes

When `DONE`, you have a choice, and it should be consistent:

| Result | Do |
|---|---|
Short answer | **replace** the status message content with the answer (`EDIT`) — the status message becomes the answer |
Long/structured answer | keep the status message as a one-line "Готово", send the answer as a Rich Message → `telegram-rich` |
File/media | replace status text with a caption, send the media as a reply to the original |
Nothing to show | replace status with the outcome in one line, and switch the reaction to ✅ |

Never leave a dead "Работаю…" message next to the answer. Either it becomes the answer or it becomes a one-line receipt.

## Anti-patterns

| Anti-pattern | Why it fails |
|---|---|
`sendChatAction` once at the start | expires after 5 s; the user sees liveness then silence |
A new message per progress step | turns a 10-step job into 10 messages |
`⏳ 45%` in the *reaction* | reactions are a small fixed vocabulary and cannot show detail |
Progress with a fake percentage | users notice; use `k/n` of real units or no number at all |
Deleting the status message on success | the user loses the receipt and the reply anchor |
Presence only on the happy path | the hang case is exactly when presence matters |
Emitting presence for work under ~300 ms | flicker; below the threshold do nothing but the reaction |

## Red flags

- A `try` block that can `return` without a terminal state.
- A progress loop with no throttle.
- `sendChatAction` outside a heartbeat.
- A status string invented ad hoc instead of taken from the table.
- Presence code inside a handler instead of behind the `Presence` object.
