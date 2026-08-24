---
name: telegram-backend
description: "Use when building or reviewing the server side of a Telegram bot — project structure, handlers and services, webhooks vs polling, rate limits, 429 and retry_after, logging, tracing, metrics, idempotency, duplicate updates, FSM state storage, config and secrets, deployment, or when a bot behaves unpredictably, silently swallows errors, double-sends, loses state on restart, or nobody can tell what it did from the logs."
---

# Backend — Telegram as a logged, predictable instrument

Telegram is a remote system with soft rate limits, retried deliveries, and errors that mean "stop" as often as they mean "retry". Treated casually, a bot is a pile of `try/except: pass`. Treated as an integration, it is boring and observable — which is the goal.

Two structures carry everything: the **layer rule** and the **single gateway**.

## Layers

```
app/
  bot.py            # composition root: config -> gateway -> middlewares -> routers -> run
  tg/
    gateway.py      # THE only place that calls the Bot API
    limiter.py      # token buckets: per-chat, per-group, global
    errors.py       # error taxonomy -> TgAction
    raw.py          # escape hatch for methods the library lacks
    presence.py     # Presence Protocol object
  middlewares/      # trace_id, dedup, user, i18n, logging, error boundary
  nav/              # GENERATED from navigation.yaml — do not edit
  handlers/         # thin: parse intent -> call service -> respond. No logic, no SQL, no HTTP.
  services/         # business logic. Pure-ish, unit-testable, no aiogram imports.
  repositories/     # data access. SQL lives here and nowhere else.
  integrations/     # LLMs, payment providers, CRMs. One module per vendor.
  models/           # domain types
  observability/    # logging config, metrics, tracing
```

The rule that makes it real: **`handlers/` may not import from `repositories/` or `integrations/`, and `services/` may not import aiogram.** Enforced by an import-graph check in `tg_preflight.py`. The payoff is not purity — it is that the Mini App backend reuses `services/` unchanged, which is what makes bot and app behave identically (`telegram-miniapp`).

## The gateway

Every outgoing call goes through one object. Full implementation: `references/gateway.md`.

```
gw.send_message(...) ──► limiter ──► attempt ──► on error: taxonomy ──► retry / degrade / raise
                            │                        │
                            └── throttle             └── metric + structured log (trace_id)
```

Responsibilities, all of them, in one place:

| Concern | Behaviour |
|---|---|
Rate limiting | token buckets: ~1/s per chat, ~20/min per group, ~30/s global. Budgets, not guarantees. |
429 | honour `parameters.retry_after` exactly. Never a fixed sleep. Per-chat 429 backs off that chat only. |
Retry | 5xx and network only, exponential + jitter, cap 3, then DEGRADE. |
Error mapping | `references/errors.md` in the `telegram` skill. Unknown code → ALERT, never IGNORE. |
`soft=True` | for cosmetic calls (reactions, status edits): IGNORE-class errors are swallowed, everything else still raises. Presence must never fail a user's work. |
Throttling | status/progress edits ≥1.2 s apart per chat, coalescing intermediate updates. |
Logging | one line per call: method, chat, latency, outcome, `trace_id`. Token and PII redacted. |
Metrics | `tg_api_calls{method,outcome}`, `tg_api_latency{method}`, `tg_api_error{code,action}`, `tg_rate_limited{scope}`. |

Banned: `await bot.send_message(...)` in a handler. Caught by preflight.

## Middleware chain

Order is load-bearing:

1. **trace** — `trace_id = uuid4()` bound to a `ContextVar`; every log line and every gateway call carries it. Include `update_id` so you can correlate with Telegram's retries.
2. **dedup** — `SETNX tg:upd:{update_id}` with a 10-minute TTL. Already seen → drop and log at `debug`. Telegram retries webhooks; without this you double-charge and double-send.
3. **user** — resolve/create the user row once; attach to context. Handlers never query for the user.
4. **i18n** — resolve locale from `from_user.language_code`, overridden by a stored preference.
5. **log** — entry and exit lines with `latency_ms` and `outcome`.
6. **error boundary** — catch, classify, log with `trace_id`, tell the user something actionable, re-raise for FIX-class so it reaches your alerting. **Never `except Exception: pass`.**

The error boundary's user-facing message is a product surface, not a fallback: *what happened · what it means · what to do*, plus the short `trace_id` so support can find the line. "Что-то пошло не так" with no trace id is how bots become unsupportable.

## Delivery: webhook or polling

| | Polling | Webhook |
|---|---|---|
Use when | local dev, single instance, low volume | production, serverless, >1 instance |
Concurrency | one process only — a second one causes **409** and both break | horizontal |
Requirements | none | HTTPS, valid cert, `secret_token` |
Sharp edge | 409 on double-start | silent retries → dedup is mandatory |

Non-negotiables for webhooks:
- `secret_token` set on `setWebhook` **and verified** on every request against `X-Telegram-Bot-Api-Secret-Token`. Without verification your endpoint is public.
- `allowed_updates` — subscribe only to what you handle. It cuts noise and cost, and makes the log meaningful.
- `drop_pending_updates=True` on deploys that change the navigation contract, so a queue of stale callbacks does not hit new code.
- 200 fast. Do work in a task/queue; a slow response makes Telegram retry, and now you are relying on dedup.
- Single-instance guard for polling: a Redis lock at startup, and treat 409 as fatal — crash loudly rather than retry.

For files >20 MB or when rate limits are the bottleneck, run the **local Bot API server** (`tdlib/telegram-bot-api`). It removes most limits and raises the file cap; it also changes `file_id` semantics, so it is an environment-level decision, not a per-feature one.

## State

| What | Where | Why |
|---|---|---|
FSM state + wizard data | Redis (`RedisStorage`), TTL 24 h | must survive restarts; memory storage loses every in-flight dialog on deploy |
Callback payloads too big for 64 bytes | Redis, TTL 1 h, scoped by `user_id` | see `telegram-ux/references/keyboards.md` |
Business data | Postgres + Alembic | |
`file_id` cache | Postgres, **keyed by bot token id** | `file_id` is not portable between tokens |
Idempotency keys | Redis (updates), Postgres (payments) | payments must survive a Redis flush |

`/start`, `/help`, `/settings` are registered on a high-priority router that **clears FSM state first**. Otherwise a user stuck mid-wizard cannot escape, which is the most common "the bot is broken" report.

## Logging and observability

Full spec, field names, SLOs and alert rules: `references/observability.md`. The contract:

- **Structured JSON**, one event per line. Never a bare `print`, never an f-string log message with embedded values — values go in fields so they are queryable.
- Every line carries `trace_id`, `update_id`, `user_id`, `chat_id`, `handler`, `outcome`, `latency_ms`.
- Redact by allow-list, not deny-list: log field names you explicitly permit. Bot token, phone numbers, payment payloads and message text (by default) are never logged.
- Four golden signals per handler: rate, errors, latency, saturation (queue depth).
- One alert that actually matters: **`tg_api_error{action="alert"} > 0`** — an unmapped error means your model of the API is stale.

## Idempotency

Three levels, all required:

1. **Update level** — dedup middleware on `update_id`.
2. **Command level** — a user double-tapping "Оплатить" must not create two orders. Key: `user_id + action + business_key`, short TTL, checked in the service.
3. **Payment level** — `telegram_payment_charge_id` unique in Postgres; the row is written **before** fulfilment (`telegram-money`).

## Config and secrets

- Environment only. A token in the repo means a rotated token.
- Typed config object validated at startup; **fail fast and loudly** on a missing variable rather than at first use.
- The token never enters a log line, a traceback, or a user-facing error. Install a log filter that redacts anything matching the token pattern — belt and braces, because tracebacks include repr of arguments.
- Separate tokens per environment. `file_id`s, sticker sets and Stars balances are all per-token.

## Common mistakes

| Mistake | Consequence | Fix |
|---|---|---|
`except Exception: pass` around a send | silent unpredictability, the thing you were hired to fix | taxonomy + boundary |
`asyncio.sleep(1)` between broadcast sends | too slow at low volume, still 429s at high | limiter |
Broadcast without 403 handling | one blocked user aborts the batch | INACTIVE handling |
Memory FSM storage in production | every deploy drops in-flight dialogs | Redis |
Two polling instances | 409, both dead | single-instance lock, fatal on 409 |
Webhook without `secret_token` verification | anyone can post fake updates | verify the header |
No dedup | double charges on webhook retries | dedup middleware |
Logging `message.text` by default | PII in logs, GDPR problem | allow-list redaction |
SQL in a handler | untestable, unreusable from the Mini App | repositories |
`bot.send_message` in a handler | no limiter, no retry, no logs | gateway |

## Red flags

- A bare `except` anywhere near an API call.
- A `sleep` used for rate limiting.
- A log line built with an f-string.
- A handler importing a repository or a vendor SDK.
- A deploy procedure with no `drop_pending_updates` decision.
- "It works on my machine" where the machine is polling and production is a webhook.
