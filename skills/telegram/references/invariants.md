# The 30 Invariants

Each law: what it is, why it exists, and how it is checked. `G` = machine-checked by `scripts/tg_preflight.py`. `H` = human/agent judgement.

## A. Text and markup (1–4)

| # | Law | Why | Check |
|---|---|---|---|
| 1 | Never hand-write `MarkdownV2`. Use `tg_text.py` — HTML mode with `esc()`, or an entities array. | MarkdownV2 has 18 reserved characters. Any user-supplied substring (a name, a filename, a URL, a price) eventually contains one, and Telegram rejects the whole message with `can't parse entities`. The failure is data-dependent, so it always ships. | G `tg_lint --rules formatting` |
| 2 | Every interpolated value is escaped at the interpolation site, not "later". | Escaping the assembled string double-escapes literals you added yourself. | G |
| 3 | Custom emoji entities wrap **exactly one** regular emoji; offsets are UTF-16 code units. | Telegram silently drops malformed `custom_emoji` entities — no error, just missing emoji. | G `tg_text.py` builder |
| 4 | Code and pre blocks are never built by concatenation — always the builder's `code()`/`pre()`. | Backticks inside user content terminate the block early and leak formatting. | G |

## B. Navigation and interaction (5–11)

| # | Law | Why | Check |
|---|---|---|---|
| 5 | `callback_data` = `v1:domain:action:arg`, ≤64 bytes, versioned prefix. | Pattern routing beats if/else chains; the version prefix lets you ship a keyboard change without breaking buttons already sitting in users' chat history. | G `tg_lint --rules callback` |
| 6 | Long-lived payloads go in a store; `callback_data` carries only a key. | 64 bytes will not hold a filter set, and truncation is silent. | G |
| 7 | Every screen: one question, ≤5 primary actions, exactly one escape (`back` or `home`). | Cognitive load and thumb reach. More than 5 and users scan instead of choosing. | G (contract: `gen_navigation --check`) |
| 8 | Every business job reachable in ≤2 taps from `/start`. | Three-level menus are a symptom of picking the wrong surface — the job wanted a Mini App, inline mode, or a command. | H, derivation in `telegram-ux` |
| 9 | State transitions edit the existing message. New messages only for genuinely new events. | Telegram's own guidance; keeps chat history readable and makes back-navigation meaningful. | G (heuristic) |
| 10 | `/start`, `/help`, `/settings` always exist and always work from any state. | Required by Telegram; also the only reliable panic exit. | G |
| 11 | Response mode (plain / reply / quote / forward / copy / reaction / ephemeral / edit) is chosen by the documented decision function, not ad hoc. | Predictable UX means the same situation always produces the same shape of response. | H, table in `telegram-ux/references/reply-strategy.md` |

## C. Presence and feedback (12–14)

| # | Law | Why | Check |
|---|---|---|---|
| 12 | Every user action is acknowledged within 300 ms. | Below ~300 ms feels instant; above ~1 s with no signal users re-tap and you get duplicate work. | G (gateway timing metric) |
| 13 | Long work emits the Presence Protocol: reaction on the user's message + a status message edited in place + the correct chat action. | Users need to know *which* phase you are in, not just that you are alive. | H, `telegram-presence` |
| 14 | `answerCallbackQuery` is called for **every** callback, always, even on error. | Un-answered callbacks leave a spinner on the button for 30 s. Users read that as a broken bot. | G |

## D. Backend predictability (15–22)

| # | Law | Why | Check |
|---|---|---|---|
| 15 | One gateway wraps every outgoing call: limiter → retry → error mapping → metrics → log. | Otherwise rate limiting and error handling get reimplemented per handler, inconsistently. | G `tg_lint --rules gateway` |
| 16 | `trace_id` per update, propagated to services and logs. | Without it you cannot reconstruct one user's session out of interleaved logs. | G |
| 17 | Structured JSON logs, one line per handler entry and exit, with `latency_ms` and `outcome`. | Greppable, aggregatable, alertable. | G |
| 18 | Secrets only from environment. Token never logged, never in a traceback, never in an error message to the user. | Bot tokens leak through exception text more often than through git. | G |
| 19 | `update_id` deduplication before dispatch. | Telegram retries webhooks. Un-deduplicated retries double-charge and double-send. | G |
| 20 | 429 is handled by honouring `retry_after`, never by a fixed sleep. | Fixed sleeps are both too slow and too fast. | G |
| 21 | Error taxonomy is exhaustive: every API error maps to one of {ignore, retry, degrade, mark-inactive, alert}. Unknown codes alert. | `references/errors.md`. Silent `except Exception: pass` is how bots become unpredictable. | G |
| 22 | Handlers hold no business logic, no SQL, no HTTP. handlers → services → repositories → integrations. | Testability, and the ability to reuse a service from the Mini App backend. | G (import-graph check) |

## E. Mini App (23–26)

| # | Law | Why | Check |
|---|---|---|---|
| 23 | `initData` HMAC-SHA256 validated server-side on every request, with `auth_date` freshness. | Client data is attacker-controlled. Without this, any user can impersonate any other. | G `validate_initdata.py` + `tg_lint --rules miniapp` |
| 24 | Colors come only from `themeParams` tokens. No hardcoded hex in components. | The app must follow the user's Telegram theme, including themes that do not exist yet. | G `tg_lint --rules theme` |
| 25 | Safe area and content safe area respected; no horizontally-swipeable element within 24 px of a screen edge. | The edge swipe is the system back/close gesture. Carousels at the edge close the app. | H + G (CSS check) |
| 26 | Mini App methods are called only from the app's own origin (enforced by Telegram since 2026-07-20). | Bot API 10.1 security hardening. Cross-origin calls are blocked, not warned. | H |

## F. Money (27–28)

| # | Law | Why | Check |
|---|---|---|---|
| 27 | Every paid action is idempotent on `telegram_payment_charge_id`, and stored before it is fulfilled. | Payment webhooks can repeat; fulfilling twice is a refund and a support ticket. | G |
| 28 | `pre_checkout_query` is answered within 10 s, and answered `ok=False` with a human reason when the order is no longer valid. | Unanswered pre-checkout silently fails the payment with no explanation to the user. | G |

## G. Delivery (29–30)

| # | Law | Why | Check |
|---|---|---|---|
| 29 | Every handler has a test against a mocked Bot API, asserting outgoing calls. | Handlers are the integration seam; untested they drift from the Navigation Contract. | G |
| 30 | `tg_preflight.py` passes before any "done" claim. | Evidence before assertions. | G |

## Rationalization table

| Excuse | Reality |
|---|---|
| "It's a small bot, I'll skip the gateway" | The gateway is 60 lines. The rate-limit bug it prevents costs a day. |
| "MarkdownV2 is fine, I'll just escape carefully" | You will escape carefully. The user's surname will still contain a `.` |
| "I'll add logging later" | The incident you need logs for happens before "later". |
| "callback_data f-string is readable" | It is, until you need to route it or version it. Then it is a rewrite. |
| "initData is fine, only my app calls this" | The endpoint is public. `curl` calls it too. |
| "Three menu levels is normal" | It is normal and it is the reason people abandon bots. Re-derive the surface. |
| "The user asked for it quickly" | Quick means skipping Stage 5–7, not Stage 2. Wrong navigation is not faster, it is a rebuild. |
| "Preflight is a formality" | It is the only thing standing between "I think it works" and "it works". |
