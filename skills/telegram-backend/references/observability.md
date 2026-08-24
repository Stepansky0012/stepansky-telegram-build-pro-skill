# Observability — making the bot auditable

Goal: for any user complaint, one query reconstructs exactly what happened, in order, with timings. Everything below serves that one sentence.

## Log event schema

One JSON object per line. Field names are fixed — a dashboard that has to handle three spellings of `user_id` is not a dashboard.

```json
{
  "ts": "2026-08-24T09:14:02.417Z",
  "level": "info",
  "event": "handler.exit",
  "trace_id": "9f3c1a2e",
  "update_id": 884213771,
  "user_id": 12345678,
  "chat_id": -1001234567890,
  "chat_type": "supergroup",
  "handler": "requests.take",
  "screen": "req_card",
  "callback": "v1:req:take",
  "response_mode": "ephemeral",
  "presence_state": "DONE",
  "outcome": "ok",
  "latency_ms": 412,
  "api_calls": 3,
  "service": "repair-bot",
  "version": "1.14.2",
  "env": "prod"
}
```

Required events, all of them:

| `event` | When | Extra fields |
|---|---|---|
`update.received` | before dispatch | `update_type` |
`update.duplicate` | dedup hit | `update_id` |
`handler.enter` / `handler.exit` | around each handler | `latency_ms`, `outcome`, `api_calls` |
`tg_api` | every gateway call | `method`, `outcome`, `attempt`, `latency_ms`, `error` |
`presence` | every state change | `presence_state`, `detail` |
`response.mode` | when the mode is chosen | `response_mode`, and why |
`nav.transition` | screen → screen | `from_screen`, `to_screen` |
`fsm.transition` | state change | `from_state`, `to_state` |
`payment.*` | invoice / pre_checkout / paid / refund | `charge_id`, `amount`, `currency` |
`miniapp.auth` | initData validated | `ok`, `reason` |
`error` | any unhandled | `exc_type`, `stack`, `action` |

`response_mode`, `presence_state` and `screen` are what make the log *behavioural* rather than merely technical. When a user says "the bot answered weirdly", you read those three fields, not the stack.

## Redaction — allow-list, never deny-list

Log only field names on the allow-list. Everything else is dropped before serialization.

Never logged: bot token, `initData` (raw), payment payloads, phone numbers, email, message text and captions **by default**, file contents, `Authorization` headers.

Message text is the hard one. Default off. Enable per-handler, explicitly, with a `log_text=True` marker and a retention shorter than the rest of your logs — and hash it (`sha256[:12]`) when you only need to know *whether* two messages were the same.

Two belts, because tracebacks defeat one:

```python
class RedactFilter(logging.Filter):
    def filter(self, record):
        msg = str(record.getMessage())
        if TOKEN and TOKEN in msg:                    # tracebacks include argument reprs
            record.msg = msg.replace(TOKEN, "***TOKEN***")
            record.args = ()
        return True
```

Plus the allow-list on structured fields. The filter catches what the schema cannot: exception text.

## Metrics

| Metric | Type | Labels | Watch for |
|---|---|---|---|
`tg_updates_total` | counter | `update_type` | traffic shape |
`tg_handler_latency` | histogram | `handler` | p95 > 2 s means presence is doing real work |
`tg_handler_errors` | counter | `handler`, `exc_type` | |
`tg_api_calls` | counter | `method`, `outcome` | |
`tg_api_latency` | histogram | `method` | Telegram-side degradation |
`tg_api_error` | counter | `code`, `action` | **`action="alert"` must be zero** |
`tg_rate_limited` | counter | `scope` | capacity planning |
`tg_presence_stuck` | gauge | — | processes in a non-terminal state >5 min |
`tg_queue_depth` | gauge | `queue` | saturation |
`tg_payments_total` | counter | `provider`, `outcome` | |
`tg_miniapp_auth_fail` | counter | `reason` | spike = someone probing |

`tg_presence_stuck` is the one people do not think of and the one users feel: it counts work that started and never reached `DONE`/`FAILED`/`CANCELLED`. It is the direct measurement of Presence Law 13.

## Alerts that are worth waking up for

| Alert | Condition | Meaning |
|---|---|---|
Unmapped API error | `tg_api_error{action="alert"} > 0` over 5 min | your model of the API is stale — the highest-signal alert in the whole system |
Parse failure | `tg_api_error{code="400"}` with `can't parse entities` | a Law-1 violation shipped |
Polling conflict | any 409 | two instances; both are broken |
Stuck presence | `tg_presence_stuck > 0` for 10 min | users staring at "Работаю…" |
Auth probing | `tg_miniapp_auth_fail` rate spike | someone is testing your initData validation |
Payment mismatch | `payment.paid` count ≠ fulfilment count over 1 h | money without goods, or goods without money |
Broadcast inactive ratio | `inactive / sent > 0.2` | list rot, or you are being blocked |

## Tracing

`trace_id` per update is the minimum and is enough for most bots. If you already run OpenTelemetry, one span per update with child spans per gateway call and per service call gives you the waterfall — the useful attributes are `handler`, `screen`, `response_mode`, `presence_state`, `tg.method`.

Propagate `trace_id` into the Mini App: return it in the auth response, have the client send it back as a header, and stamp it on Mini App request logs. Then a session that starts in the chat and finishes in the app is **one** trace. That is what "total stitching" means operationally.

## Health and readiness

| Endpoint | Checks |
|---|---|
`/health` | process alive. Nothing else. |
`/ready` | DB reachable, Redis reachable, `getMe` cached-OK, webhook registered and its URL matches config |

`getWebhookInfo` on `/ready` catches the classic deploy failure: new code, old webhook URL, zero updates, everything "green".

## Retention and audit

- Application logs: 14–30 days.
- Payment events: as long as your jurisdiction requires — separate stream, separate retention, never redacted away. `charge_id`, amount, currency, user, fulfilment status.
- Moderation/admin actions: separate audit stream with actor, target, before/after. If staff can act on user data through the bot, this is not optional.
- Anything containing message text: shortest retention of all.

## Definition of "predictable"

The bot is predictable when all five hold:

1. Every user-visible outcome traces to one `trace_id` chain with no gaps.
2. Every API error maps to a named action; `alert` count is zero.
3. Every started process reached a terminal state.
4. The same input produces the same `response_mode` and the same `screen`.
5. A deploy changes `version` in the logs and nothing else about the shape of the log stream.

These are checkable. Check them before saying the work is done.
