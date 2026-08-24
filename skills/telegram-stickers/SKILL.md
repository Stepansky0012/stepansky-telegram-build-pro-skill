---
name: telegram-stickers
description: "Use when a Telegram bot creates, manages or sends stickers or custom emoji — sticker packs, emoji sets, TGS/Lottie animations, WEBM video stickers, WEBP images, animated status icons, per-user generated packs, icons on keyboard buttons — or when Telegram rejects an asset with STICKER_PNG_DIMENSIONS, STICKER_TGS_NOTGZIPPED, STICKERSET_INVALID, or a custom emoji does not render."
---

# Stickers and custom emoji as a design surface

Most bots treat stickers as content ("send a funny sticker"). The leverage is elsewhere: **a custom emoji set is your bot's icon system.** One set of nine animated glyphs gives you animated status icons in messages, animated icons on keyboard buttons (`icon_custom_emoji_id`, API 9.4+), and a visual identity that no text emoji can match — and it costs one build step.

Two orthogonal things, do not confuse them:

| | Sticker set | Custom emoji set |
|---|---|---|
Created with | `createNewStickerSet(sticker_type="regular")` | `createNewStickerSet(sticker_type="custom_emoji")` |
Sent as | its own message (`sendSticker`) | an *entity inside text*, or a button icon |
Size | 512×512 | **exactly 100×100** |
Use it for | reactions, personality, shareable content, virality | status icons, button icons, inline glyphs |
Users need Premium to *use* it themselves | no | yes (to pick it) — but your bot rendering it is fine |

For the process/status use case the user sees in every message, you want a **custom emoji set**, not stickers. Full spec and build pipeline: `references/process-pack.md`.

## Hard specs — get these wrong and the API rejects the upload

Complete matrix, including the fields that are easy to miss (`needs_repainting`, set-name suffix, `format`): `references/formats.md`. The short version:

| | Sticker (512) | Custom emoji (100) |
|---|---|---|
Static | WEBP/PNG, one side exactly 512, ≤512 KB | WEBP/PNG, exactly 100×100 |
Animated | **TGS** = gzipped Lottie/bodymovin JSON, 512×512, ≤3 s, **60 FPS**, must loop, ≤64 KB | TGS, 100×100, same rules |
Video | WEBM/**VP9**, 512×512, ≤3 s, ≤30 FPS, ≤256 KB, **no audio** | WEBM/VP9, 100×100 |

Validate before uploading — the API's error messages are terse and the round trip is slow:

```bash
python scripts/validate_sticker_assets.py assets/process-emoji/ --kind custom_emoji
```

## The pipeline

```
uploadStickerFile(png_sticker=..., sticker_format=...)   -> file_id (stable, reusable)
createNewStickerSet(user_id, name, title, stickers=[1..50], sticker_type, needs_repainting?)
addStickerToSet(user_id, name, sticker)                  -> for each remaining
setStickerEmojiList / setStickerKeywords / setStickerSetThumbnail
getCustomEmojiStickers(custom_emoji_ids)                 -> read ids back
```

Non-obvious constraints:

- **Set name must end with `_by_<bot_username>`**, case-sensitive, and is globally unique forever. Include an environment marker: `proc_dev_by_mybot` / `proc_prod_by_mybot`. A name is never reusable after deletion.
- `createNewStickerSet` accepts **1–50** stickers; the rest go through `addStickerToSet`.
- The set is owned by the `user_id` you pass. Use a dedicated service account, not a developer's personal account — otherwise the set dies with the employee.
- `uploadStickerFile` gives you a `file_id` you can reference repeatedly without re-uploading. Upload once, then create and patch.
- `needs_repainting=True` (custom emoji sets only) makes single-color glyphs **adopt the surrounding text color**. This is what makes a status icon look native in both light and dark themes. It is the single most valuable flag in this API and it can only be set at creation time.
- `custom_emoji_id` values are **not** the same as `file_id`. Read them from the created set (`getStickerSet`) and persist them; they are what goes into entities and button icons.

## Idempotent build

Sticker set management is not idempotent by nature, so make it so:

```bash
python scripts/build_process_pack.py --spec templates/process-pack.spec.yaml \
       --assets assets/process-emoji --env prod --out app/nav/custom_emoji.py
```

The script: validates every asset → creates the set if missing, patches it if present (diff by `emoji_list` + a content hash stored in `keywords`) → reads ids back → writes a generated Python map. Re-running it is safe and is the only sanctioned way to change the set. Handlers import the generated map; they never hardcode an id.

Ship a fallback: the generated map is `dict[str, str | None]`, and `E.emoji(fallback, id)` degrades to the plain emoji when the id is `None`. A missing set must never break a message.

## Per-user generated packs — the underused viral mechanic

A bot that builds a pack *for* the user (their photos, their name, generated art) gets shared by construction: every sticker they send carries a link back to the set. Pattern:

1. Set name `u{user_id}_{slug}_by_{bot}` — one set per user, deterministic, so re-running is a patch.
2. Generate → validate → `createNewStickerSet` on first sticker → `addStickerToSet` for the rest, sequentially (parallel adds race on the same set).
3. `setStickerKeywords` per sticker: Telegram's client-side sticker search is AI-driven and indexes keywords. A pack with no keywords is a pack nobody finds again.
4. `setStickerSetThumbnail` — the pack's face in the panel. Skipping it makes the pack look broken.
5. Reply with a `t.me/addstickers/<name>` link **and** send one sticker from the set, so the user sees it before installing.

Rate reality: adds are sequential and each is an API call. A 30-sticker pack is ~30 s of work — this is a `WORKING` phase with real `k/n` progress (`telegram-presence`), not a spinner.

## Icons on buttons

```python
InlineKeyboardButton(text="Оплатить", callback_data=...,
                     icon_custom_emoji_id=CUSTOM["star"])
```

API 9.4+. Beats gluing an emoji into the label: dedicated slot (no alignment drift), animatable, label stays clean for length limits and localization, cannot break formatting. Details in `telegram-ux/references/keyboards.md`.

## Sending stickers well

- `sendSticker` by `file_id` is free and instant; by URL/upload it is neither. Cache `file_id` per environment — **a `file_id` is not portable between bot tokens.**
- Sticker + text in one message is impossible. If you need both, send the sticker as a reply to the text, or use a custom emoji inside the text instead. Nine times out of ten the custom emoji is the right answer.
- Never send a sticker as an error message. Stickers read as celebratory; failures need words.
- `choose_sticker` is a real chat action — use it while generating.

## Common mistakes

| Mistake | Symptom | Fix |
|---|---|---|
Uploading a 512×512 asset to a custom emoji set | `STICKER_PNG_DIMENSIONS` | 100×100 exactly |
Raw Lottie JSON instead of gzipped | `STICKER_TGS_NOTGZIPPED` | gzip it; `.tgs` is a gzipped `.json` |
30 FPS TGS | rejected or plays wrong | animated stickers are 60 FPS; **video** stickers are ≤30 |
Non-looping animation | plays once then freezes | loop is mandatory |
Audio track in the WEBM | rejected | strip audio |
`file_id` reused across dev and prod tokens | `wrong file identifier` | per-environment cache |
Set name without `_by_<bot>` | `STICKERSET_INVALID` | fix the suffix |
Storing `file_id` where `custom_emoji_id` is needed | entity silently dropped | read ids from the set |
Forgetting `needs_repainting` | icons look wrong in one theme | recreate the set — it cannot be changed later |
Parallel `addStickerToSet` | random failures, missing stickers | sequential |
