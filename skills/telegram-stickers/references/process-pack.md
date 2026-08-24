# The Process Pack — an animated icon system for your bot

A single `custom_emoji` set that gives the bot animated status glyphs usable in **three** places at once: inside message text (entities), as keyboard button icons (`icon_custom_emoji_id`, 9.4+), and as the visual anchor of the Presence Protocol. Nine glyphs. One build step. It is the highest visual return per hour of work available on the platform.

## Why custom emoji and not stickers

A sticker is its own message — it cannot sit inside a status line, cannot be a button icon, and cannot be edited in place. A custom emoji is a *character*: it lives inside text you can edit, so a status line can animate through phases without sending a single new message.

With `needs_repainting: true` a single-colour glyph adopts the surrounding text colour, so the same asset looks correct in light theme, dark theme, and any theme Telegram ships next year.

## Spec

`templates/process-pack.spec.yaml`:

```yaml
set:
  slug: proc                      # final name: proc_{env}_by_{bot_username}
  title: "{bot_title} · Process"
  type: custom_emoji
  needs_repainting: true          # IMMUTABLE after creation — decide now
  owner_env: STICKER_OWNER_USER_ID

glyphs:
  - key: received
    state: RECEIVED
    fallback: "👀"                # meaning must match; shown when custom is unavailable
    keywords: [received, ack, seen]
    asset: received.tgs
    motion: "eye blink once, 1.0s, loop with 1.5s idle"

  - key: queued
    state: QUEUED
    fallback: "⏳"
    keywords: [queued, waiting, pending]
    asset: queued.tgs
    motion: "hourglass rotates 180°, 1.2s, continuous"

  - key: studying
    state: STUDYING
    fallback: "🔎"
    keywords: [studying, reading, search, analyzing]
    asset: studying.tgs
    motion: "magnifier sweeps left→right→center, 1.4s, continuous"

  - key: working
    state: WORKING
    fallback: "✍️"
    keywords: [working, writing, processing]
    asset: working.tgs
    motion: "three dots cascade, 0.9s, continuous — reads as 'typing'"

  - key: waiting_user
    state: WAITING_USER
    fallback: "✋"
    keywords: [waiting, input, you]
    asset: waiting_user.tgs
    motion: "static with a slow 2s pulse — deliberately calm, not urgent"

  - key: waiting_human
    state: WAITING_HUMAN
    fallback: "🕐"
    keywords: [review, moderation, human]
    asset: waiting_human.tgs
    motion: "clock hand one full sweep, 3.0s"

  - key: done
    state: DONE
    fallback: "✅"
    keywords: [done, ok, success]
    asset: done.tgs
    motion: "checkmark draws in 0.6s then holds — must NOT loop visibly"

  - key: failed
    state: FAILED
    fallback: "⚠️"
    keywords: [failed, error, problem]
    asset: failed.tgs
    motion: "single shake, 0.5s, then hold"

  - key: cancelled
    state: CANCELLED
    fallback: "🚫"
    keywords: [cancelled, stopped, aborted]
    asset: cancelled.tgs
    motion: "static"
```

## Motion design rules

These are UX rules, not art direction, and they are why the pack works:

| Rule | Reason |
|---|---|
Ongoing states loop continuously; terminal states animate **once** and hold | a looping ✅ reads as "still working" |
Loop period 0.9–1.5 s for active states | faster reads as anxious, slower reads as frozen |
Amplitude small — the glyph is 100×100 rendered at ~20 px | large motion becomes visual noise inside a sentence |
Single colour, `needs_repainting: true` | theme-correct forever, and a third of the file size |
Shapes + transforms + trim paths only — no expressions, images, text layers, effects | `rlottie` does not support them; unsupported features render as nothing |
Silhouette must be readable at 20 px with animation paused | many clients show the first frame in previews and notifications |
`working` deliberately echoes the native typing dots | borrow the platform's own vocabulary; users already know it |

Budget: ≤64 KB per TGS. A single-colour shape animation of this kind lands in the 4–15 KB range. If you are near the cap, you used a feature `rlottie` will not render anyway.

## Build

```bash
export BOT_TOKEN=...                 # a bot token, not a user token
export STICKER_OWNER_USER_ID=...     # service account, not a person

python scripts/validate_sticker_assets.py assets/process-emoji --kind custom_emoji
python scripts/build_process_pack.py \
    --spec templates/process-pack.spec.yaml \
    --assets assets/process-emoji \
    --env prod \
    --out app/nav/custom_emoji.py
```

Behaviour:
1. Validate every asset offline. Any failure aborts before touching the API.
2. `getStickerSet` — if absent, `uploadStickerFile` × N then `createNewStickerSet` with the first, `addStickerToSet` sequentially for the rest.
3. If present, diff by the content hash stashed in `keywords` (`v:<sha256[:12]>`) and `replaceStickerInSet` only what changed. Idempotent: re-running with no asset changes performs zero writes.
4. `setStickerSetThumbnail` from the `done` glyph.
5. `getStickerSet` again, read `custom_emoji_id` per glyph, write the generated map.

Generated output:

```python
# app/nav/custom_emoji.py — GENERATED by build_process_pack.py. Do not edit.
SET_NAME = "proc_prod_by_mybot"
BUILT_AT = "2026-08-24T12:00:00Z"
CUSTOM: dict[str, str | None] = {
    "received": "5312536423851630001",
    "studying": "5312536423851630002",
    ...
}
FALLBACK: dict[str, str] = {"received": "👀", "studying": "🔎", ...}
```

## Wiring

```python
from app.nav.custom_emoji import CUSTOM, FALLBACK

E().emoji(FALLBACK["studying"], CUSTOM.get("studying")).text(" Изучаю запрос")

InlineKeyboardButton(text="Оплатить", callback_data=...,
                     icon_custom_emoji_id=CUSTOM.get("done"))
```

`CUSTOM.get()` — never `CUSTOM[...]`. A missing glyph degrades to the plain emoji; it never raises inside a send path.

## Operational rules

- **Two sets: dev and prod.** Same slug, different `env`, different names. Ids differ, so the generated map is per-environment and is not committed for prod builds — generate it in CI.
- The set name is **permanent**. Deleting a set burns the name. Choose `slug` once.
- `needs_repainting` and `sticker_type` are **immutable**. Getting them wrong means a new set and a new name.
- Owner is a service account. A set owned by a departing employee's account is unmanageable.
- Rotate assets by `replaceStickerInSet`, never delete-and-add — delete changes positions and invalidates the ids other messages already reference.
- Version the set in the glyph `keywords` so the diff is content-based, not date-based.

## Extending the pack

The nine process states are the floor. The same set is the natural home for domain glyphs — a currency mark, a priority flag, a category icon. Rules: one set (Telegram surfaces sets as a unit), ≤200 glyphs, and every addition needs a `fallback` that carries the meaning on its own. If you cannot pick a meaningful fallback emoji, the glyph is decoration and does not belong in an icon system.
