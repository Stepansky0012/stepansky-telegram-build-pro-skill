---
name: telegram-ux
description: "Use when designing or reviewing how a Telegram bot talks to users — menus, commands, inline and reply keyboards, callback routing, multi-step dialogs, pagination, confirmations, deciding between reply/forward/reaction/edit, or when a bot's menu structure does not match what the business actually needs, users get lost, or the same action behaves differently in different places."
---

# Telegram UX — from business requirements to a navigation contract

Menus are not decoration and not a taste question. A Telegram interface is **derived** from the business's jobs by a documented procedure, written down as a contract, and only then turned into code. If you cannot point at the row of the contract a button came from, the button is wrong.

Core principle: **the surface is chosen by the shape of the job, not by habit.** Most bad Telegram UX is one mistake — a job that wanted a Mini App or inline mode got a three-level inline menu instead.

## The procedure — JSA (Job → Surface → Affordance)

### Stage 1. Extract jobs

From the business requirements, write every job as `verb + object + outcome`, one line each. No UI words allowed in this list.

```
J1  submit a repair request           → request exists, user has a number
J2  check status of my request        → user knows the phase
J3  attach a photo to a request       → photo linked
J4  pay for expedited handling        → paid, request flagged urgent
J5  browse the price list             → user found the relevant price
J6  share a price with a colleague    → colleague sees it in their chat
J7  cancel a request                  → request cancelled, irreversible
J8  (staff) take a request into work  → assignee set, user notified
```

If a "job" cannot be phrased this way, it is a feature idea, not a job. Send it back.

### Stage 2. Classify each job

| Axis | Values |
|---|---|
Frequency | `constant` (daily) · `regular` (weekly) · `rare` (once/never repeated)
Input shape | `none` · `one-of-few` (≤8) · `one-of-many` (>8) · `free text` · `form` (n fields) · `file/media`
Reversibility | `safe` · `destructive`
Locus | `private` · `group` · `cross-chat` · `external entry`
Data volume | `single` · `list` · `dataset` (needs filtering/charts/map)

### Stage 3. Pick the surface — the matrix

| Job profile | Surface | Never use instead |
|---|---|---|
`constant` + `none` | **slash command** + menu-button entry | a button three levels deep |
top 2–4 `constant` jobs | **persistent reply keyboard** (≤4 buttons, 1–2 rows) | inline keyboard on every message |
`one-of-few` | **inline keyboard**, ≤5 options + escape | free-text parsing |
`one-of-many` | **inline keyboard + pagination + text search**, or Mini App if >50 | a 40-button wall |
`form` with ≤5 fields | **FSM wizard**, one field per step, back at every step | a single message asking for `name, phone, address` |
`form` with >5 fields, or interdependent fields | **Mini App** | an 11-step FSM |
`dataset` (filter/sort/chart/map/gallery) | **Mini App** | pagination |
`cross-chat` | **inline mode** (`@bot query`) | "forward this to your colleague" |
`external entry` | **deep link** `t.me/bot?start=…` / `?startapp=…` | "press /start then choose…" |
`file/media` | direct upload handler + a reaction ack | an FSM step that says "now send the photo" with no ack |
`destructive` | any surface **+ a mandatory confirm step naming the object** | a bare Delete button |
task list / progress with many items | **checklist message** (`sendChecklist`, API 9.1+) | numbered text edited by hand |
staff/admin actions inside a group | **ephemeral message** (`receiver_user_id`, API 10.2+) | DM'ing the staff member |

Then apply the two hard rules:

- **Depth rule.** Every job reachable in **≤2 taps** from `/start` or from a command. If a job needs 3, the surface is wrong — go back to Stage 3, do not add a submenu.
- **Escape rule.** Every screen has exactly one escape. `back` inside a flow, `home` at flow roots. Never both, never neither.

### Stage 4. Write the Navigation Contract

One table, checked into the repo as `navigation.yaml`. Format, semantics and the code generator: `references/navigation-contract.md`.

```bash
python scripts/gen_navigation.py navigation.yaml --out app/nav --diagram nav.mmd
```

The generator emits keyboard builders, router stubs with correct filters, a Mermaid graph, and — most importantly — **fails** on unreachable screens, missing escapes, depth >2, duplicate callback namespaces and screens with >5 actions. The contract is the spec; the generator is the test.

### Stage 5. Only now write handlers

Handlers are filled-in stubs. A handler that does not correspond to a contract row does not get merged.

## Callback protocol

`v1:domain:action:arg` — see `references/keyboards.md` for the full grammar, the 64-byte budget, the payload store for anything that does not fit, and versioning so that buttons already sitting in old chat messages keep working.

```python
CB = CallbackProtocol(version="v1")
kb.button(text="Take", callback_data=CB.pack("req", "take", req_id))   # v1:req:take:81f2
```

Banned: `callback_data=f"btn_{i}"`, indexes into a list that lives in memory, JSON blobs, anything unversioned.

## Response mode — the contextual decision function

A predictable bot answers the *same situation* the same way every time. Do not decide per handler. The full table and a runnable `choose_response_mode()` are in `references/reply-strategy.md`. Summary:

| Situation | Mode |
|---|---|
Direct answer in private, nothing to disambiguate | plain `sendMessage`
The user sent several things and this answers one of them | `reply_to_message_id` |
You need to point at a *fragment* of what they wrote | `ReplyParameters` with `quote` |
Answering in a group where others are talking | reply, always |
Answering only one person in a busy group | **ephemeral** (`receiver_user_id`, 10.2+) |
Acknowledging receipt, no content to add | `setMessageReaction` — not a message |
The state of something you already reported changed | `editMessageText` on the original |
Passing along someone else's content with attribution | `forwardMessage` |
Passing content along *without* attribution or from a channel you own | `copyMessage` |
Long, structured, or streamed answer | `sendRichMessage` / `sendRichMessageDraft` → `telegram-rich` |
Content the user should send onward to a third chat | `switch_inline_query` button or inline mode |

## Copy and tone

- Button labels: **verb-first, ≤20 chars, no ending punctuation.** "Оплатить" not "Оплата заказа сейчас".
- One message = one idea. If you need "also", it is a second message or a second screen.
- Never write "Ошибка" alone. Format: *what happened · what it means for you · what to do now*.
- Numbers, IDs, filenames go in `code()` — monospace makes them tappable-to-copy and immune to markup breakage.
- `/help` is a map of the contract, generated from it, not hand-maintained prose.

## Commands

- `/start`, `/help`, `/settings` are mandatory and must work from **any** FSM state (register them on a high-priority router that clears state).
- Commands are specific: `/newrequest`, not `/new` with an argument. Telegram's own guidance.
- Register with `setMyCommands` per scope and per language. Different lists for private / group / admin — a user must never see a command they cannot use.
- The menu button label is set deliberately (`setChatMenuButton`), not left as the default when the primary job is a Mini App.

## Common mistakes

| Mistake | Consequence | Fix |
|---|---|---|
Keyboard rebuilt as a new message each tap | chat fills with dead menus, back-navigation meaningless | edit in place (Law 9) |
Tall keyboard (>5 rows) | pushes the message off-screen; users see buttons with no context | paginate |
`answerCallbackQuery` only on success | 30 s spinner on every error | answer in `finally` |
Emoji glued into button text as decoration | inconsistent rendering across clients | `icon_custom_emoji_id` (9.4+), or nothing |
Free-text parsing where a keyboard belongs | typos become support tickets | keyboard |
Same action lives in two places with different `callback_data` | two code paths, one drifts | one namespace per domain |
Destructive action with a bare confirm ("Are you sure?") | users confirm reflexively | name the object: "Delete request #81 permanently?" |
`/help` written by hand | drifts from reality within a sprint | generate from contract |

## Red flags — stop and re-derive

- You are adding a third menu level.
- You are writing a "Ещё…" / "More…" button.
- You cannot say which job a button serves.
- Two screens have the same title.
- A screen has 7 buttons.
- You are about to parse free text where the answer set is finite and known.
