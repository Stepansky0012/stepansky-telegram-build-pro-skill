# Asset format matrix and API field reference

Authoritative source: <https://core.telegram.org/stickers> and `bots/api#stickers`. Fetch it when in doubt — the specs have changed before.

## Dimensions, duration, weight

| Kind | `format` | Container/codec | Dimensions | Duration | FPS | Max size | Notes |
|---|---|---|---|---|---|---|---|
Static sticker | `static` | WEBP (preferred) or PNG | one side **exactly 512**, the other ≤512 | — | — | 512 KB | transparent background; PNG must be RGBA |
Animated sticker | `animated` | **TGS** — gzipped Lottie/bodymovin JSON | 512×512 | ≤3 s | **60** | 64 KB | must loop; rendered by `rlottie` |
Video sticker | `video` | WEBM, **VP9** | 512×512 | ≤3 s | ≤30 | 256 KB | alpha channel supported; **no audio track** |
Static custom emoji | `static` | WEBP/PNG | **exactly 100×100** | — | — | 64 KB | |
Animated custom emoji | `animated` | TGS | **exactly 100×100** | ≤3 s | 60 | 64 KB | |
Video custom emoji | `video` | WEBM/VP9 | **exactly 100×100** | ≤3 s | ≤30 | 256 KB | |
Set thumbnail | matches set | WEBP/TGS/WEBM | 100×100 | ≤3 s | — | 32 KB (static) | optional but always set it |

TGS is literally a gzipped Lottie JSON. `gzip -9 anim.json > anim.tgs`. Common Lottie features `rlottie` does **not** support: expressions, merge paths on some renderers, image layers (use shapes), text layers (convert to outlines), effects/blurs. Keeping to shapes + transforms + trim paths keeps files tiny and portable.

## `InputSticker`

```json
{
  "sticker": "<file_id | attach://name | https url>",
  "format": "static | animated | video",
  "emoji_list": ["🔎"],
  "keywords": ["studying", "search", "v:1a2b3c"],
  "mask_position": {"point": "forehead", "x_shift": 0, "y_shift": 0, "scale": 1.0}
}
```

- `emoji_list`: 1–20 emoji. For a **custom emoji** this is the fallback shown to clients that cannot render yours — pick one that means the same thing.
- `keywords`: up to 20, ≤64 chars each. Telegram's sticker search indexes them. Also a convenient place to stash a content hash for idempotent rebuilds.
- `mask_position` only for `sticker_type="mask"`.

## `createNewStickerSet`

| Field | Notes |
|---|---|
`user_id` | the owner. Use a service account, never a person's. |
`name` | 1–64 chars, `[a-zA-Z0-9_]`, must start with a letter, **must end `_by_<bot_username>`** (case-sensitive). Globally unique **forever** — not reusable after deletion. Put the environment in it. |
`title` | 1–64 chars, shown to users. Changeable later via `setStickerSetTitle`. |
`stickers` | 1–50 `InputSticker`. More go through `addStickerToSet`. |
`sticker_type` | `regular` \| `mask` \| `custom_emoji`. **Immutable.** |
`needs_repainting` | custom emoji only. Single-color glyphs adopt the surrounding text colour. **Settable only at creation.** Use it for anything that appears inside text. |

## Method reference

| Method | Purpose | Gotcha |
|---|---|---|
`uploadStickerFile` | upload once, get a reusable `file_id` | `sticker_format` must match the file |
`createNewStickerSet` | create | name suffix + immutable `sticker_type`/`needs_repainting` |
`addStickerToSet` | append one | **sequential only** — parallel adds race |
`replaceStickerInSet` | swap one in place | keeps position; preferred over delete+add |
`deleteStickerFromSet` | remove one | |
`setStickerPositionInSet` | reorder | position is 0-based |
`setStickerEmojiList` | change emoji | replaces the whole list |
`setStickerKeywords` | change keywords | replaces the whole list |
`setStickerSetThumbnail` | set the face | needs `format` |
`setStickerSetTitle` | rename | display only |
`deleteStickerSet` | delete | the **name is burned**, not reusable |
`getStickerSet` | read back — this is where `custom_emoji_id` comes from | |
`getCustomEmojiStickers` | resolve ids → stickers | up to 200 ids |

## Identifier taxonomy — the source of half the bugs

| Id | What it identifies | Portable across bot tokens? | Where used |
|---|---|---|---|
`file_id` | an uploaded file, **per bot** | **No** | `sendSticker`, `InputSticker.sticker` |
`file_unique_id` | the file's content, globally | yes, but cannot be used to send | dedup, caching |
`custom_emoji_id` | a custom emoji glyph | yes | `MessageEntity.custom_emoji_id`, `icon_custom_emoji_id` |
set `name` | the set | yes | all set methods, `t.me/addstickers/<name>` |

Persisting a `file_id` from a dev bot and using it in prod produces `wrong file identifier` — cache per token.

## Preflight

```bash
python scripts/validate_sticker_assets.py assets/ --kind custom_emoji   # or sticker
```

Checks: container/codec sniffing, exact dimensions, gzip validity and Lottie shape for TGS (`fr`, `op`, `w`, `h`, loop), duration and FPS for WEBM, byte size, alpha presence, audio-track absence, and filename→state mapping against the pack spec. Runs offline; no token required.
