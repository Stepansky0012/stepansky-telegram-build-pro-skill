# Keyboards, callbacks, pagination

## The callback protocol

```
v1:domain:action:arg
│  │      │      └─ optional, ≤32 bytes, opaque to the router
│  │      └─ verb, snake_case
│  └─ business domain, snake_case
└─ protocol version
```

Budget: 64 **bytes** total (UTF-8, not characters — a Cyrillic label in callback data costs 2 bytes per letter, which is why labels never go in callback data).

```python
# app/nav/callbacks.py (generated)
from dataclasses import dataclass

@dataclass(frozen=True)
class CallbackProtocol:
    version: str = "v1"
    sep: str = ":"

    def pack(self, domain: str, action: str, arg: str | int = "") -> str:
        raw = self.sep.join(filter(None, (self.version, domain, action, str(arg))))
        if len(raw.encode()) > 64:
            raise ValueError(f"callback_data overflow ({len(raw.encode())}B): {raw!r}")
        return raw

    def unpack(self, data: str) -> tuple[str, str, str, str]:
        parts = data.split(self.sep, 3)
        parts += [""] * (4 - len(parts))
        return tuple(parts)  # version, domain, action, arg
```

Routing is by prefix filter, never by if/else chain:

```python
router.callback_query(F.data.startswith("v1:req:"))          # whole domain
router.callback_query(F.data.startswith("v1:req:take:"))     # one action
```

### When the payload does not fit

Anything larger than an id — a filter set, a search query, a draft — goes in a **payload store** keyed by a short token:

```python
token = await payloads.put(user_id, {"filters": {...}, "sort": "date"}, ttl=3600)
cb = CB.pack("cat", "apply", token)     # v1:cat:apply:7fA3k2
```

Requirements: TTL (so the store does not grow forever), scoped to `user_id` (so a leaked token is useless to someone else), and a graceful "this selection expired, here it is again" path when the token is gone. Never truncate to fit — truncation is silent corruption.

### Versioning

`callback_data` sits in users' chat history indefinitely. A button tapped six months from now must not crash. Rules:

- Bump `version` whenever a namespace's *meaning* changes.
- Keep a catch-all router for old versions that answers the callback (Law 14), tells the user the menu is outdated, and re-sends the current screen.
- Never reuse a `domain:action` pair with different argument semantics inside the same version.

## Shape rules

| Rule | Value | Why |
|---|---|---|
Primary actions per screen | ≤5 | above this users scan instead of choosing |
Rows | ≤5 (paginated lists: 5 items + 1 nav row) | a taller keyboard pushes the message text off-screen |
Buttons per row | 1 for verbs, 2–3 for short choices, 3–4 for digits/pagination | thumb targets |
Label length | ≤20 chars | truncation is client-dependent |
Escape | exactly one, always last row | predictability |
Destructive | never adjacent to a safe action; own row, after a spacer row if possible | mis-taps |

## Icons on buttons — the modern way

Bot API **9.4** added `icon_custom_emoji_id` and `style` to `InlineKeyboardButton` and `KeyboardButton`. Use them instead of pasting emoji into the label:

```python
InlineKeyboardButton(
    text="Оплатить",
    callback_data=CB.pack("pay", "start", order_id),
    icon_custom_emoji_id=EMOJI["star"],     # id from your own emoji set
)
```

Why this and not `"⭐ Оплатить"`:
- The icon renders in a dedicated slot — no alignment drift between labels of different length.
- It can be animated (your own TGS/WEBM custom emoji), which a text emoji cannot.
- The label stays clean for length limits and for localization.
- It cannot break text formatting.

Building the icon set is `telegram-stickers` → `references/process-pack.md`. Fall back to a plain label if the emoji set is unavailable — never hard-fail a keyboard because an icon is missing.

## Pagination

```python
def paginate(items, page, page_size, ns, escape="home"):
    total = (len(items) + page_size - 1) // page_size
    page = max(0, min(page, total - 1))
    kb = InlineKeyboardBuilder()
    for it in items[page*page_size:(page+1)*page_size]:
        kb.row(InlineKeyboardButton(text=it.label,
                                    callback_data=CB.pack(ns, "open", it.id)))
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="‹", callback_data=CB.pack(ns, "page", page-1)))
    if total > 1:
        nav.append(InlineKeyboardButton(text=f"{page+1}/{total}",
                                        callback_data=CB.pack(ns, "noop", "")))
    if page < total - 1:
        nav.append(InlineKeyboardButton(text="›", callback_data=CB.pack(ns, "page", page+1)))
    if nav:
        kb.row(*nav)
    kb.row(InlineKeyboardButton(text="⌂", callback_data=CB.pack("nav", escape, "")))
    return kb.as_markup()
```

Notes: the page counter is a real button with a `noop` action so the row spacing is stable; clamping `page` means a stale button from an old message degrades to the nearest valid page instead of erroring; **paging edits the message, never sends a new one.**

Above ~50 items, pagination is the wrong surface — add text search, or move the job to a Mini App (JSA Stage 3).

## Multi-select

State lives server-side (FSM data or the payload store), not in the keyboard. Each tap flips one key and re-renders:

```python
selected = set(await state.get_value("selected", []))
selected ^= {arg}                        # toggle
await state.update_data(selected=list(selected))
await cb.message.edit_reply_markup(reply_markup=render(options, selected))
await cb.answer()                        # Law 14
```

Render checked state with a leading `✓ ` / `  ` prefix (two-space pad keeps labels aligned), and always include a **Готово** button — without it users do not know the selection is committed.

## Reply keyboards

Use only for the 2–4 `constant` jobs. Properties:
- `is_persistent=True`, `resize_keyboard=True`.
- Labels must be unique across the whole bot — you route on text, and a duplicate label is an ambiguous route.
- Never combine a reply keyboard with a message that also has an inline keyboard for the *same* choice.
- `KeyboardButtonRequestUsers` / `RequestChat` / `web_app` / `request_location` belong here, not in inline keyboards.

## `answerCallbackQuery` discipline

```python
try:
    ...
finally:
    await cb.answer()          # or cb.answer("Готово", show_alert=False)
```

- Always, even on exception. An unanswered callback shows a spinner for 30 s.
- Use the text form for outcomes with no visual change ("Скопировано"); silent for anything where the message visibly updates.
- `show_alert=True` only for irreversible outcomes or errors that need reading.
- `cache_time` on genuinely idempotent read-only callbacks reduces duplicate work from double-taps.
