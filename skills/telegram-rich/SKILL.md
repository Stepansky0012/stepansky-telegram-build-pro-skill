---
name: telegram-rich
description: "Use when a Telegram bot must send long, structured or AI-generated answers — headings, tables, lists, code, math, collages, slideshows, maps — or stream a reply token by token like a chat assistant, or send a message inside a group that only one user can see; also when a streamed answer loses its formatting, a long answer is being chunked at 4096 characters by hand, or you need `sendRichMessage`, `sendRichMessageDraft`, `sendMessageDraft`, or ephemeral messages."
---

# Rich Messages, streaming, and ephemeral replies

Bot API **10.1** (2026-06-11) added Rich Messages; **10.2** (2026-07-14) extended them with media and voice-note blocks and added Ephemeral Messages. Together they retire three long-standing workarounds: manual 4096-char chunking, `editMessageText` streaming, and DM-ing one person out of a group.

**Before writing any Rich Message code, fetch `bots/api#sendrichmessage` and the `InputRichBlock*` types and quote the real field names into your plan.** The block model is new and large; this skill teaches the doctrine, not the schema. Field names below are marked ✱ where you must verify against the live docs rather than trust this file.

## When to use which surface

| Content | Surface |
|---|---|
≤4096 chars, flat text with a little emphasis | plain `sendMessage` + HTML (`telegram-text`) |
Headings, tables, nested lists, code, math, galleries, maps | **`sendRichMessage`** |
Any of the above, produced token by token by a model | **`sendRichMessageDraft`** then finalize |
Plain-text streaming, no structure | `sendMessageDraft` (9.3+, all bots since 9.5) |
Long content the user should *navigate*, not read linearly | Mini App (`telegram-miniapp`) |
One user's eyes only, inside a group | ephemeral (`receiver_user_id`✱) |

The last row is a judgement call worth stating: if the answer is a dataset the user will filter, sort or revisit, a Rich Message is still the wrong surface. Rich Messages are for *reading*.

## Streaming — the one rule that matters

**Never stream by repeatedly calling `editMessageText`.** It is the reflex from 2023 and with Rich Messages it actively destroys the formatting: each edit re-parses the whole payload, so partial markup mid-stream is either rejected or renders as literal text, and the user watches the message flicker between broken states.

The sanctioned shape:

```python
async def stream_rich(gw, chat_id, token_iter, *, reply_to=None):
    """Stream with sendRichMessageDraft, finalize once. Field names marked ✱
    must be verified against bots/api#sendrichmessagedraft."""
    draft = None
    buf: list[str] = []
    async for delta in token_iter:
        buf.append(delta)
        if _should_flush(buf):                       # throttle, see below
            blocks = md_to_rich_blocks("".join(buf), partial=True)
            draft = await gw.send_rich_draft(        # ✱ sendRichMessageDraft
                chat_id, blocks, draft_id=draft, reply_to=reply_to)
    blocks = md_to_rich_blocks("".join(buf), partial=False)
    return await gw.send_rich(chat_id, blocks, reply_to=reply_to)   # ✱ sendRichMessage
```

Throttle policy — this is where streaming bots die:

| Parameter | Value | Why |
|---|---|---|
Min interval between draft updates | **1.2 s** | the per-chat send budget is ~1/s; tighter and you 429 mid-answer |
Flush trigger | interval elapsed **and** (a sentence ended, or a block closed, or ≥120 new chars) | flushing mid-token shows the user half a word |
Max drafts per answer | ~40 | a 60-second answer at 1.2 s is 50 calls; cap it and let the tail land in the final send |
Never flush | inside an unclosed code fence, table row, or list item | partial block structure is the thing that renders broken |

Throttling lives in the gateway (`telegram-backend`), not in the loop. A loop that decides its own rate will eventually be called from two places.

## Partial-safe block conversion

The function that turns a model's in-progress markdown into blocks must be **partial-safe**: given a prefix of the output, it emits only *closed* blocks and holds the trailing incomplete one back.

```
markdown prefix  ->  [closed blocks…]  +  (held tail)
```

Rules:
- An unclosed ``` fence emits nothing for that block; hold the tail.
- A table emits only complete rows; hold a partial row.
- A list emits complete items; hold a partial item.
- A heading emits only after its newline.
- On the final call (`partial=False`), force-close everything and, if a fence is still open, emit its content as a code block anyway — a model that stops mid-fence must not lose the code.

This is the piece worth unit-testing hardest: feed it every prefix of a known output and assert that the concatenation of emitted blocks is always a valid prefix of the final blocks. `telegram-test` has the property-test template.

## Block model — the map

Verify names at `bots/api#inputrichmessage`. The families:

| Family | Use for |
|---|---|
paragraph / heading | prose structure |
list (ordered, unordered) | enumerations |
quote / expandable quote | citations, long source text |
code (with language) | code, logs, JSON |
math | formulas |
table | comparisons — the single biggest win over plain text |
collapsible details | long appendices without wall-of-text |
checklist | actionable output; also see `sendChecklist` |
media: photo, video, audio, **voice note** (`InputMediaVoiceNote`, 10.2✱), document | inline media |
collage / slideshow | multiple images as one unit |
map | locations |

`InputRichMessageMedia`✱ (10.2) lets you state explicitly which media a markdown/HTML rich payload refers to, instead of relying on inference. Use it — inference is where "wrong image attached" bugs come from.

## Ephemeral messages (10.2+)

A message in a group chat visible only to one user.

```python
await gw.send_message(chat_id, text, receiver_user_id=user_id)   # ✱
# later:
await gw.edit_ephemeral_message_text(...)      # ✱ editEphemeralMessageText
await gw.delete_ephemeral_message(...)         # ✱ deleteEphemeralMessage
```

Canonical uses: admin controls inside a group, per-user error messages, "only you can see this" confirmations, staff actions in a shared work chat. It replaces the DM pattern, which silently fails for every user who never started the bot.

Constraints to design around: ephemeral messages are tied to a receiver, so **do not** use them for anything that needs an audit trail visible to the chat, and do not assume they are private in the security sense — they are a *visibility* feature, not a confidentiality guarantee. Anything genuinely sensitive goes to a DM or a Mini App behind `initData` auth.

Fallback chain when 10.2 is unavailable: reply in-chat with an `@mention`, or DM if the user has started the bot. Decide once, in the response-mode facade (`telegram-ux/references/reply-strategy.md`), never per handler.

## Raw method escape hatch

Frameworks lag the API by weeks or months — as of writing, typed wrappers for the 10.x methods are incomplete in most libraries. Do not wait, and do not fake it with `editMessageText`:

```python
# app/tg/raw.py
async def raw(bot, method: str, **params):
    """Call a Bot API method the library does not wrap yet.
    Every call site MUST name the API version that introduced the method."""
    return await bot.session.make_request(bot, _Raw(method, params))
```

Rules for raw calls:
- One wrapper, in the gateway, so limiter/retry/logging still apply. A raw call that bypasses the gateway bypasses every invariant.
- Comment the introducing API version at each call site: `# Bot API 10.2`.
- Build payloads as plain dicts from the *documentation*, fetched this session.
- Add a `telegram-test` case asserting the payload shape, so a library upgrade that adds real typing does not silently change behaviour.

## Common mistakes

| Mistake | Consequence |
|---|---|
`editMessageText` in a stream loop | formatting destroyed, flicker, 429 |
Flushing on every token | instant rate-limit, unreadable |
Flushing inside an open code fence | literal ``` visible to the user |
Manual 4096 chunking where a Rich Message fits | loses tables and structure; user reads 4 messages |
Rich Message for a filterable dataset | wrong surface — Mini App |
Ephemeral used as a security boundary | it is visibility, not confidentiality |
Raw method called outside the gateway | no limiter, no retry, no logs |
Assuming block field names from memory | silent 400s; fetch the docs |

## Red flags

- A loop containing both `edit` and a token iterator.
- A `sleep()` used as a throttle.
- Block field names in your code that you have not read in the docs this session.
- No test that feeds partial prefixes to the block converter.
