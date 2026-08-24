# Error taxonomy — every API error maps to exactly one action

Actions: `IGNORE` (expected, not an error) · `RETRY` (honour backoff) · `DEGRADE` (fall back to a simpler send) · `INACTIVE` (mark user/chat unreachable) · `FIX` (bug in our code, alert) · `ALERT` (unknown, page a human).

## HTTP 400 — Bad Request

| `description` contains | Action | Note |
|---|---|---|
| `message is not modified` | IGNORE | You edited to identical content. Compare before editing, or just swallow it. |
| `message to edit not found` | DEGRADE | Send a fresh message; the old one was deleted by the user. |
| `message can't be edited` | DEGRADE | Too old (48 h) or not ours. |
| `query is too old` / `query ID is invalid` | IGNORE | `answerCallbackQuery` after 30 s. Fix latency, don't retry. |
| `can't parse entities` | FIX | **Law 1 violation.** Never retry — retry sends the same broken markup. Log the offending text. |
| `BUTTON_DATA_INVALID` | FIX | `callback_data` >64 bytes. |
| `wrong file identifier` / `wrong remote file id` | FIX | `file_id` from a different bot, or reused across environments. |
| `chat not found` | INACTIVE | Never messaged us, or chat deleted. |
| `not enough rights` | DEGRADE | Missing admin permission; surface a clear message to admins. |
| `message text is empty` | FIX | Usually an empty builder result. |
| `PEER_ID_INVALID` | INACTIVE | |
| `STICKERSET_INVALID` | FIX | Set name wrong or deleted. |
| `STICKER_PNG_DIMENSIONS` / `STICKER_TGS_NOTGZIPPED` / `STICKER_VIDEO_NOWEBP` | FIX | Asset spec violation → run `validate_sticker_assets.py`. |
| anything else | ALERT | Unknown 400 means our model of the API is wrong. |

## HTTP 403 — Forbidden

| Contains | Action |
|---|---|
| `bot was blocked by the user` | INACTIVE — set `user.is_active=false`, stop broadcasting to them. Never retry. |
| `user is deactivated` | INACTIVE |
| `bot was kicked from the ... chat` | INACTIVE for that chat |
| `CHAT_WRITE_FORBIDDEN` | INACTIVE for that chat |

403 is the single most under-handled error. An unhandled 403 in a broadcast loop kills the whole broadcast.

## HTTP 429 — Too Many Requests

Action: **RETRY, honouring `parameters.retry_after` exactly.** Never a fixed sleep, never immediate retry, never a tighter loop.

- Per-chat 429 → back off that chat only; keep the global pipeline moving.
- Global 429 → pause the whole limiter for `retry_after`.
- Cap retries at 3, then DEGRADE (queue for later delivery) and record a metric. A 429 storm is a capacity bug, not a transient.

## HTTP 409 — Conflict

`terminated by other getUpdates request` → **FIX, immediately fatal.** Two instances are polling the same token. Crash loudly; do not retry. Cause is almost always a stale process or a webhook still registered while polling.

## HTTP 401 — Unauthorized

Token invalid or revoked. FIX, fatal at startup. Never log the token in the error.

## HTTP 500 / 502 / 504 — Telegram side

RETRY with exponential backoff and jitter, cap 3 attempts, then DEGRADE. These happen; they are not your bug.

## Network / timeout

RETRY with jitter. Distinguish connect timeout (retry freely) from read timeout on a *mutating* call (retry may duplicate — for `sendMessage` prefer idempotency keys over blind retry).

## Mini App specific

| Symptom | Cause | Action |
|---|---|---|
| initData hash mismatch | Wrong secret derivation, or params re-encoded | FIX — the secret is `HMAC_SHA256(bot_token, "WebAppData")`, key and message are *that* way round |
| initData valid but `auth_date` old | Replayed | Reject; enforce a freshness window (24 h max, 1 h for money) |
| Mini App methods silently no-op | Called from a non-app origin — blocked by Telegram since 2026-07-20 | FIX the hosting/origin |
| Blank screen on iOS only | Bundle too large / unsupported syntax | FIX, check performance class handling |

## Payments specific

| Symptom | Action |
|---|---|
| `pre_checkout_query` unanswered | FIX — payment silently fails. Answer within 10 s, always. |
| `successful_payment` arrives twice | Expected. Idempotency on `telegram_payment_charge_id` (Law 27). |
| Refund requested for consumed goods | Policy decision — document it *before* launch, not after. |

## Implementation contract

The gateway maps errors once, centrally:

```python
class TgAction(str, Enum):
    IGNORE = "ignore"; RETRY = "retry"; DEGRADE = "degrade"
    INACTIVE = "inactive"; FIX = "fix"; ALERT = "alert"
```

Requirements:
- Unknown code → `ALERT`, never `IGNORE`. A silent default is how bots become unpredictable.
- `FIX` raises in dev/CI, logs at `error` + alerts in prod. It must never be swallowed.
- Every mapping emits a metric `tg_api_error{code,action}` so you can watch the shape of your failures.
