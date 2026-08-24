---
name: telegram-text
description: "Use when a Telegram bot sends any user-visible text — bold, italic, links, code, spoilers, quotes, custom emoji, prices, names, filenames, user input echoed back — or when Telegram returns \"can't parse entities\", \"Bad Request: character must be escaped\", markup renders literally, part of a message disappears, or a message fails only for some users."
---

# Telegram text — formatting that cannot break

**A message that fails to parse fails for one user and ships to everyone.** The bug is always data-dependent: it appears the first time a surname contains a dot, a filename contains an underscore, or a price contains a minus. So the rule is not "escape carefully" — it is "never be in a position where escaping is your job".

## The law

**Hand-written `parse_mode="MarkdownV2"` is banned.** Two sanctioned paths, both in `scripts/tg_text.py`:

| Path | When | Failure mode |
|---|---|---|
**HTML mode** (the `H` builder) | 95% of cases | 3 characters to escape (`& < >`), impossible to get wrong |
**Entities** (the `E` builder, no `parse_mode`) | custom emoji, programmatic styling, text you did not author | zero characters to escape — markup is out of band |

MarkdownV2 has **18** reserved characters: `` _ * [ ] ( ) ~ ` > # + - = | { } . ! `` — plus different rules *inside* code blocks (`` ` `` and `\`) and *inside* link targets (`)` and `\`). Three different escaping contexts in one syntax is why this is banned rather than merely discouraged.

## HTML path

```python
from tg_text import H

text = (H.b("Заявка ") + H.code(req.number) + "\n"
        + "Адрес: " + H(req.address) + "\n"          # H(...) escapes
        + H.a("Открыть в приложении", url))
await gw.send_message(chat_id, text, parse_mode="HTML")
```

`H(x)` escapes `&`, `<`, `>` and nothing else. `H.b/i/u/s/code/pre/spoiler/a/quote/emoji` escape their own arguments. `H` is a `str` subclass, so it passes straight to any library. The guard rails: adding a plain `str` to an `H` chain **auto-escapes** it (you cannot inject markup through a variable), `H.a()` rejects unsupported URL schemes, and `%`/`.format()` on an `H` raise — those are the two ways people smuggle unescaped values in. `H.raw()` exists for literals you wrote yourself and is the only unescaped door.

Supported tags (anything else is stripped by Telegram, silently): `b/strong`, `i/em`, `u/ins`, `s/strike/del`, `span class="tg-spoiler"`, `tg-spoiler`, `a href`, `tg-emoji emoji-id`, `code`, `pre`, `pre><code class="language-…"`, `blockquote`, `blockquote expandable`.

Nesting rules Telegram enforces: `code` and `pre` cannot contain other entities. `blockquote` cannot nest. Everything else nests freely.

## Entities path

Use when the styling is computed, when the text comes from outside (an LLM, a DB, a user), or when you need custom emoji. No `parse_mode`, so **no character in the text is special**.

```python
from tg_text import E

b = E()
b.emoji("🔎", CUSTOM["studying"])      # exactly one emoji, id from your set
b.text(" Изучаю: ")
b.code(query)                           # arbitrary content, cannot break anything
text, entities = b.build()
await gw.send_message(chat_id, text, entities=entities)
```

The builder computes offsets in **UTF-16 code units**, which is what Telegram expects and what every naive implementation gets wrong. Emoji, most CJK, and any character above U+FFFF count as **2** units. `len(python_str)` is not the answer; `len(s.encode("utf-16-le")) // 2` is. The builder does this for you and there is no reason to compute an offset by hand ever again.

## Custom emoji — the three rules

1. The `custom_emoji` entity must wrap **exactly one** regular emoji in the text. Not zero, not two, not a letter. Telegram **silently drops** malformed ones — no error, just a missing icon, which is why this must be enforced in code.
2. That regular emoji is the fallback for clients that cannot render the custom one. Choose it to mean the same thing.
3. Sending custom/premium emoji requires the appropriate account status on the bot (a bot with an NFT username, or premium context). Build a fallback: if the set is unavailable, `E.emoji()` degrades to the plain emoji with no entity. Never hard-fail a message over an icon.

Sets are built in `telegram-stickers`; ids are loaded from one generated map so a set rebuild does not require touching handlers.

## Limits — clamp, do not truncate blindly

| Limit | Value | Handling |
|---|---|---|
Message text | 4096 chars | split on paragraph boundaries, never mid-entity |
Caption | 1024 chars | overflow goes to a follow-up message, or use `RICH` |
Entities per message | practical ~100 | merge adjacent identical entities |
Rich message | see `telegram-rich` | prefer over manual chunking for anything long |

Splitting mid-entity produces `can't parse entities` or an unstyled tail. `tg_text.split_safe(text, entities)` splits at the last paragraph break before the limit and re-bases the offsets of every entity that crosses into the next chunk.

## Localization

- Never build a sentence by concatenating translated fragments — word order differs by language. One key per whole sentence, with named placeholders.
- Placeholders are substituted **after** escaping decisions: the builder interpolates values as escaped nodes, so a translator cannot introduce a markup break and a user's name cannot either.
- Pluralization uses the ICU-style plural forms of the target language, not `if n == 1`.

## Validation

```bash
python scripts/tg_lint.py app/ --rules formatting          # part of tg_preflight.py
```

It fails the build on:

| Pattern | Reason |
|---|---|
`parse_mode="MarkdownV2"` anywhere | Law 1 |
An f-string or `.format()`/`%` result passed with any `parse_mode` | interpolation without escaping |
`+` concatenation of a bare `str` into an `H` chain | escape bypass |
A manual `MessageEntity(offset=…)` with a literal integer | hand-computed offset |
`html.escape` used where `H` should be | inconsistent escaping (`html.escape` also escapes quotes, which Telegram does not want inside text nodes) |
A string containing an emoji plus `custom_emoji` built without the builder | rule 1 violation |

## Common mistakes

| Mistake | Symptom | Fix |
|---|---|---|
`f"*{name}*"` with MarkdownV2 | fails only for users whose name has a `.` `-` `_` `(` | HTML or entities |
Escaping the assembled string | your own `*` markers get escaped too, markup renders literally | escape at interpolation |
Reusing an escaped value in a `code` block | wrong context — `code` needs `` ` `` and `\` escaped, not the 18 | let the builder pick the context |
`len(text)` for an offset | every emoji before the entity shifts it by one | UTF-16 units, use the builder |
Truncating with `text[:4096]` | breaks the last entity | `split_safe` |
Putting a URL in the visible text | Telegram auto-links it and your entity overlaps | `H.a(label, url)` |
Uppercasing a formatted string | destroys tag names | style after casing, never before |

## Red flags

- You are counting characters to compute an offset.
- You are writing a `re.sub` that adds backslashes.
- You are testing markup by sending yourself a message with your own name in it.
- A message works in your tests and fails in production → it is data-dependent → it is this.
