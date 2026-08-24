# The Workflow — Brief to Ship in 8 stages

Non-trivial Telegram work goes through these stages in order. Each has an exit gate you can check. **You may not write handlers before Stage 4.**

The stage that gets skipped is always Stage 2, and skipping it is what produces bots whose menus do not match the business. Wrong navigation is not faster — it is a rebuild.

```
0 Version    1 Brief    2 Contract    3 ADR    4 Skeleton
                 5 Implement    6 Verify    7 Ship
```

---

## Stage 0 — Version guard

```bash
python scripts/check_api_version.py --pinned 10.2
```

**Gate:** the live Bot API version is known and stated. If it moved past the pin, the delta has been read and either the pin is raised or the unused capabilities are named explicitly.

Do not proceed on memory. The surface changed nine times between 2025-04 and 2026-07.

---

## Stage 1 — Brief

Write, in the user's words, then in yours:

| Field | Content |
|---|---|
**Jobs** | every business job as `verb + object + outcome`, one line each, IDs `J1…Jn`. No UI words. |
**Actors** | user, staff, admin, system. Who does which job. |
**Volumes** | users, messages/day, peak burst. Decides polling vs webhook and whether the limiter matters. |
**Money** | is there a paid path? which rail? (`telegram-money` → digital goods must be Stars) |
**Surfaces** | private chat / group / channel / Mini App. Each has different rules. |
**Data** | what is stored, what is PII, retention. |
**Non-goals** | what this bot will not do. |

Then classify every job on the five JSA axes (`telegram-ux` Stage 2): frequency, input shape, reversibility, locus, data volume.

**Gate:** every job has an ID and a full classification. A job you cannot classify is not specified yet — go back to the user with the specific question, not with a general "any other requirements?".

---

## Stage 2 — Navigation Contract

Run JSA Stage 3 (the surface matrix) and write `navigation.yaml`.

```bash
python scripts/gen_navigation.py navigation.yaml --check --diagram nav.mmd
```

**Gate:** the validator exits 0. That means: no unreachable screen, no depth over 2, every screen has exactly one escape, every destructive action has a named confirmation, no callback overflow, no namespace collision, **no orphan job**.

`orphan job` is the gate that closes the loop back to the brief: a requirement with no interface. Show the user `nav.mmd` before writing any code — it is cheaper to argue about a diagram than about handlers.

If the validator reports depth 3, do **not** add a submenu. Re-run Stage 3: that job wanted a command, an inline mode, or a Mini App.

---

## Stage 3 — ADR (Architecture Decision Record)

One page. Decide and record:

| Decision | Options and the deciding factor |
|---|---|
Framework | aiogram 3 (default here) / grammY / PTB — team language wins |
Delivery | polling (dev, single instance) vs webhook (prod, needs `secret_token`) |
State | Redis storage is mandatory in prod; MemoryStorage drops every in-flight dialog on deploy |
Data | Postgres + Alembic; what is in Redis and why |
Money rail | Stars for digital, card provider for physical; the ~32% mobile fee is in the pricing model |
Mini App | needed? which jobs; one origin per environment |
Rich messages | are answers long/structured/streamed? if yes, `sendRichMessageDraft`, and the raw escape hatch is in scope |
Presence | which jobs exceed 2 s and therefore need the Presence Protocol |
Process Pack | building the custom emoji set? decide `needs_repainting` **now** — it is immutable |
Observability | where logs go, what the alert on `tg_api_error{action="alert"}` pages |

**Gate:** every row decided with a stated reason, and every irreversible choice (set name, `sticker_type`, `needs_repainting`, set owner account) written down.

---

## Stage 4 — Skeleton

```bash
python scripts/gen_navigation.py navigation.yaml --out app/nav --diagram nav.mmd
cp -r templates/bot/* .          # gateway, limiter, errors, presence, middlewares
cp scripts/tg_text.py app/tg/text.py
python scripts/make_process_assets.py --out assets/process-emoji     # if using the pack
python scripts/validate_sticker_assets.py assets/process-emoji --kind custom_emoji
```

**Gate:** the project starts, `/start` answers, `tg_preflight.py --offline` passes with only `tests` skipped. The generated `routers.py` is now your implementation checklist — one `NotImplementedError` per contract transition.

---

## Stage 5 — Implement

Order matters, because each step makes the next one testable:

1. **Services first**, with tests. No Telegram imports — they are shared with the Mini App backend.
2. **Repositories** + migrations.
3. **Handlers**: fill in the generated stubs. Thin — parse intent, call a service, respond. Delete the `NotImplementedError` line as you go; a remaining stub is a remaining task.
4. **Presence** on every job over ~2 s.
5. **Response mode** through the one facade — never chosen inside a handler.
6. **Mini App**, if in scope: auth route first, then routes, then UI.
7. **Money**, if in scope: `pre_checkout_query` and the idempotent charge row before any UI.

Rules that apply throughout: no hand-written markup (`telegram-text`), no bare `bot.send_*` (`telegram-backend`), every callback answered in a `finally`, every keyboard from `app/nav`.

**Gate:** zero `NotImplementedError` in `app/nav/routers.py`; `tg_lint` clean.

---

## Stage 6 — Verify

```bash
python scripts/tg_preflight.py --project .
```

Every gate must print PASS or a justified `skip`. Then the judgement checks a script cannot make:

| Check | How |
|---|---|
Presence terminates | force an exception in a long job; the chat must end in ⚠️, never a stuck "Работаю…" |
Formatting is data-proof | run the job with `ул. Ленина (д.5) *_[]` as input |
Rate limits | broadcast to 200 chats; watch `tg_rate_limited` and the 403 path |
Duplicate delivery | replay the same `update_id`; exactly one side effect |
Callback answered | force every error branch of one callback; no spinner |
Depth | tap from `/start` to each job; count taps ≤2 |
Mini App gestures | swipe starting exactly at the screen edge on every horizontal element |
Mini App identity | `curl` a route with someone else's `user_id` in the body; must be ignored |
Money | deliver `successful_payment` twice; one entitlement |
Logs | pick one user action, reconstruct it from logs alone by `trace_id` |

**Gate:** all of the above, run and observed. "It worked when I tried it" is not this list.

---

## Stage 7 — Ship

| Step | Detail |
|---|---|
BotFather | commands per scope and language, description, short description, menu button, Mini App URL |
Webhook | `secret_token` set **and verified**; `allowed_updates` narrowed; `/ready` compares the registered URL to config |
`drop_pending_updates` | `True` on any deploy that changes the navigation contract's shape |
Process Pack | built for `prod` with the service account as owner; generated id map produced in CI, not committed |
Secrets | separate token per environment; `.env` never tracked |
Alerts | `tg_api_error{action="alert"}`, 409, stuck presence, paid≠fulfilled |
Rollback | how to revert; old-`callback_data` fallback router live for one release |
`/help` | generated from the contract, includes pricing and refund policy if the bot takes money |

**Gate:** `/ready` green, one real round trip on the production bot, and the alert list is actually wired to somewhere a human looks.

---

## Change protocol after shipping

| Change | Path |
|---|---|
New job | Stage 1 → 2 → regenerate → implement. Never bolt a button onto an existing screen. |
Label change | `navigation.yaml` only, regenerate. |
Namespace change or removed action | bump `version:` to `v2`, keep a `v1` fallback router for one release that answers the callback and re-sends the current screen. Old buttons live in chat history forever. |
Bot API moved | Stage 0, then decide per capability. |
New emoji glyph | edit the spec, rebuild the pack (idempotent), regenerate the id map. |
Price change | plan definition + `/help` + a message to active subscribers before the next renewal. |

## Anti-patterns

| Anti-pattern | What it costs |
|---|---|
Handlers before the contract | the menu does not match the business; rebuild |
"Quick prototype" without the gateway | rate-limit bug in week two, and the fix touches every handler |
Adding a submenu instead of re-running JSA | the depth-3 bot users abandon |
Skipping the ADR | `needs_repainting` and the set name are immutable and now wrong |
Presence added "later" | later is after the users complained |
Preflight as a formality | the difference between "I think it works" and "it works" |
