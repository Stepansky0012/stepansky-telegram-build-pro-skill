---
name: telegram
description: "Use when any task touches Telegram — bots, Bot API methods, keyboards, menus, FSM dialogs, stickers, custom emoji, reactions, \"typing\" indicators, Rich Messages, Mini Apps / TWA / WebApp, initData, Telegram Stars, XTR, invoices, subscriptions, webhooks, aiogram, grammY, Telegraf, python-telegram-bot, teloxide, BotFather, deep links, or when a Telegram bot behaves unpredictably, sends broken markup, hits 429, or loses state."
---

# Telegram Stack — Router

Entry point for **all** Telegram work. This skill routes to a sub-skill and enforces the invariants that hold across every one of them.

**Pinned Bot API: `10.2` (2026-07-14).** Everything in this stack is written against 10.2.

## Step 0 — Version Guard (never skip)

Before planning, before writing code:

```bash
python scripts/check_api_version.py --pinned 10.2
```

- If live version **> pinned**: read the delta at <https://core.telegram.org/bots/api-changelog>, report it to the user, and only then plan. New methods may make your plan obsolete.
- If the script cannot reach the network: say so out loud and proceed under the pinned version. Do **not** silently assume.

**You do not know the Bot API from memory.** The surface changed 9 times between 2025-04 and 2026-07. Any claim of the form "Telegram has no method for X" must be backed by a fetch of the API page in *this* session, not by recall.

## Step 1 — Route

| The task involves | Go to |
|---|---|
| Designing menus/flows from business requirements, keyboards, callbacks, reply-vs-forward-vs-reaction choice | `telegram-ux` |
| Any user-visible text, bold/links/code, escaping, entities, custom emoji in text | `telegram-text` |
| "in progress" / "studying" / "typing" feedback, reactions on user messages, status messages | `telegram-presence` |
| Creating or managing sticker sets, custom emoji sets, TGS/WEBM/WEBP assets | `telegram-stickers` |
| Structured/long/AI answers, streaming, tables, code, ephemeral group replies | `telegram-rich` |
| Project layout, gateway, rate limits, retries, logging, tracing, metrics, deploy, webhooks | `telegram-backend` |
| Mini App / TWA: layout, theme, initData, safe area, gestures, bot↔app stitching | `telegram-miniapp` |
| Stars, XTR, invoices, subscriptions, refunds, providers, fee math, affiliate | `telegram-money` |
| Tests for handlers, mocked Bot API, CI gates | `telegram-test` |

Multiple rows apply on almost every real task. Load them all. `telegram-ux` + `telegram-text` + `telegram-backend` are load-bearing for *any* bot.

## Step 2 — Follow the Workflow

Non-trivial work (new bot, new module, redesign) goes through `workflows/WORKFLOW.md` — 8 stages, Brief → Ship. **You may not jump straight to writing handlers.** The stage that gets skipped is always Stage 2 (Navigation Contract), and skipping it is what produces bots whose menus don't match the business.

## The Invariants

30 laws that hold everywhere. Full text with rationale: `references/invariants.md`. They are machine-checked — run before you claim done:

```bash
python scripts/tg_preflight.py --project .
```

Individual rule groups: `python scripts/tg_lint.py app/ --rules formatting,callback,gateway,layers,secrets,presence`. A deliberate exception is marked inline with `# tg-lint: ignore[<rule>]` — use it for test fixtures, never to silence a real finding.

The nine that get violated most:

1. **Never build markup by hand.** `parse_mode="MarkdownV2"` with an f-string is banned. Use `tg_text.py` (HTML or entities). → `telegram-text`
2. **`callback_data` is a protocol, not a string.** `domain:action:arg` , ≤64 **bytes**, versioned. → `telegram-ux`
3. **Every outgoing API call goes through one gateway.** No bare `bot.send_*` in handlers. → `telegram-backend`
4. **Every update carries a `trace_id`**, logged on entry and exit with latency and outcome.
5. **Idempotency by `update_id`.** Webhooks retry. Handlers must be replay-safe.
6. **State machines change messages in place** (`editMessageText`/`editMessageReplyMarkup`), they do not append new ones.
7. **`initData` is validated server-side with HMAC-SHA256, every request.** No exceptions. → `telegram-miniapp`
8. **Handlers contain no business logic and no SQL.** handlers → services → repositories → integrations.
9. **Every user action is acknowledged within 300 ms** — reaction, chat action, or edited status line. Silence is the #1 UX defect. → `telegram-presence`

## Escalation — When the Rules Run Out

When you hit something this stack does not cover, do **not** improvise from memory. Follow the escalation ladder, in order:

1. **`references/api-map.md`** — topic → exact documentation URL, with anchors. Covers the whole Bot API surface plus MTProto-side sticker/emoji/stars internals.
2. **Fetch the doc** at that URL and quote the actual parameter names into your plan.
3. **`references/errors.md`** — if the symptom is an API error code or an unexpected `Update` shape.
4. **The changelog** — <https://core.telegram.org/bots/api-changelog>. If the feature is newer than your framework, call the raw HTTP method (see `telegram-backend` → "Raw method escape hatch") rather than waiting for library support.
5. **Only then** state an assumption explicitly, mark it as unverified, and continue.

Announce which rung you used. "I fetched #sendrichmessage and the field is `blocks`" is a valid answer; "I believe there's a method for that" is not.

## Red Flags — Stop

- About to write `f"*{name}*"` with a `parse_mode` → violation of law 1.
- About to write `callback_data=f"btn_{i}"` → violation of law 2.
- About to add a third level of menu nesting → your surface choice is wrong, re-run the JSA derivation in `telegram-ux`.
- About to `sleep()` between sends to dodge rate limits → the gateway's limiter is the only sanctioned mechanism.
- About to answer "Telegram can't do X" without a fetch → escalate instead.
- About to say "done" without `tg_preflight.py` output → not done.

## Quick Reference — Operational Limits

| Limit | Value | Source of truth |
|---|---|---|
| `callback_data` | 64 bytes | API docs, hard error |
| Inline keyboard buttons | practical ceiling ~100 total; design for ≤15 | UX, not API |
| Messages per chat | ~1/sec sustained | 429 `retry_after` is authoritative |
| Messages per group | ~20/min | operational consensus |
| Broadcast throughput | ~30/sec global | operational consensus |
| Sticker set initial | 1–50 stickers | `createNewStickerSet` |
| CloudStorage | 1024 keys per user | Mini Apps docs |
| Deep link payload | 64 chars, `A-Za-z0-9_-` | `bots/features#deep-linking` |
| Poll options | 12 | Bot API 9.1 |

The only authoritative rate limit is the `retry_after` Telegram hands you in a 429. Treat the table as budgets, not guarantees.
