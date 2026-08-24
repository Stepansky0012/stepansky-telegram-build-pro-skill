#!/usr/bin/env python3
"""tg_text — Telegram text that cannot break.

Two sanctioned ways to produce formatted Telegram text:

  H  — HTML mode. Escapes & < > and nothing else. Pass with parse_mode="HTML".
  E  — entities builder. No parse_mode at all, so no character is special.
       Offsets are computed in UTF-16 code units, which is what Telegram wants
       and what hand-written code always gets wrong.

Also: split_safe() for messages over the length limit, without breaking entities.

Zero dependencies. Python 3.10+.
"""
from __future__ import annotations

import html as _html
import json
import sys
import unicodedata
from dataclasses import dataclass, field

MAX_TEXT = 4096
MAX_CAPTION = 1024

# --------------------------------------------------------------------------- #
# UTF-16 arithmetic
# --------------------------------------------------------------------------- #


def u16len(s: str) -> int:
    """Length in UTF-16 code units — Telegram's unit for entity offsets."""
    return len(s.encode("utf-16-le")) // 2


def _cp_to_u16_prefix(text: str) -> list[int]:
    """cum[i] == u16len(text[:i]) for i in 0..len(text)."""
    cum = [0] * (len(text) + 1)
    total = 0
    for i, ch in enumerate(text):
        total += 2 if ord(ch) > 0xFFFF else 1
        cum[i + 1] = total
    return cum


def _u16_to_cp(cum: list[int], u16_off: int) -> int:
    """Inverse of the prefix table. Clamps into range."""
    lo, hi = 0, len(cum) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if cum[mid] < u16_off:
            lo = mid + 1
        else:
            hi = mid
    return lo


# --------------------------------------------------------------------------- #
# HTML path
# --------------------------------------------------------------------------- #

_ALLOWED_LANG = set("abcdefghijklmnopqrstuvwxyz0123456789+-#._")


class H(str):
    """Safe Telegram HTML fragment.

    H("a < b")        -> escaped text node
    H.b("bold")       -> <b>bold</b>, argument escaped
    H.raw("<b>x</b>") -> trusted, NOT escaped. Use only for literals you wrote.

    Concatenation with a plain str auto-escapes the str, so you can never
    accidentally inject markup by adding a variable.
    """

    __slots__ = ()

    def __new__(cls, value: object = "") -> "H":
        if isinstance(value, H):
            return value
        return str.__new__(cls, _html.escape(str(value), quote=False))

    # -- construction -------------------------------------------------------
    @classmethod
    def raw(cls, s: str) -> "H":
        return str.__new__(cls, s)

    @classmethod
    def join(cls, sep: str, parts) -> "H":
        return cls.raw(str(H(sep)).join(str(H(p)) for p in parts))

    # -- operators ----------------------------------------------------------
    def __add__(self, other) -> "H":  # type: ignore[override]
        return H.raw(str(self) + str(H(other)))

    def __radd__(self, other) -> "H":
        return H.raw(str(H(other)) + str(self))

    def __mod__(self, args):  # block accidental %-formatting
        raise TypeError("do not %-format H; build fragments and add them")

    def format(self, *a, **kw):  # noqa: A003 - block accidental .format
        raise TypeError("do not .format() H; build fragments and add them")

    # -- inline styles ------------------------------------------------------
    @classmethod
    def _wrap(cls, tag: str, content, attrs: str = "") -> "H":
        return cls.raw(f"<{tag}{attrs}>{H(content)}</{tag}>")

    @classmethod
    def b(cls, c) -> "H":
        return cls._wrap("b", c)

    @classmethod
    def i(cls, c) -> "H":
        return cls._wrap("i", c)

    @classmethod
    def u(cls, c) -> "H":
        return cls._wrap("u", c)

    @classmethod
    def s(cls, c) -> "H":
        return cls._wrap("s", c)

    @classmethod
    def spoiler(cls, c) -> "H":
        return cls._wrap("tg-spoiler", c)

    @classmethod
    def code(cls, c) -> "H":
        return cls._wrap("code", c)

    @classmethod
    def pre(cls, c, lang: str | None = None) -> "H":
        if lang:
            lang = "".join(ch for ch in lang.lower() if ch in _ALLOWED_LANG)
            inner = f'<code class="language-{lang}">{H(c)}</code>'
            return cls.raw(f"<pre>{inner}</pre>")
        return cls._wrap("pre", c)

    @classmethod
    def a(cls, label, url: str) -> "H":
        url = str(url).strip()
        if not url or any(ch in url for ch in '"<>'):
            raise ValueError(f"unsafe url: {url!r}")
        if not url.startswith(("http://", "https://", "tg://", "t.me", "mailto:")):
            raise ValueError(f"unsupported url scheme: {url!r}")
        return cls.raw(f'<a href="{_html.escape(url, quote=True)}">{H(label)}</a>')

    @classmethod
    def mention(cls, label, user_id: int) -> "H":
        return cls.a(label, f"tg://user?id={int(user_id)}")

    @classmethod
    def quote(cls, c, expandable: bool = False) -> "H":
        return cls._wrap("blockquote", c, " expandable" if expandable else "")

    @classmethod
    def emoji(cls, fallback: str, custom_emoji_id: str | None) -> "H":
        """Custom emoji. Degrades to the plain fallback when no id is available."""
        _assert_single_emoji(fallback)
        if not custom_emoji_id:
            return cls.raw(fallback)
        cid = str(custom_emoji_id)
        if not cid.isdigit():
            raise ValueError(f"custom_emoji_id must be numeric, got {cid!r}")
        return cls.raw(f'<tg-emoji emoji-id="{cid}">{fallback}</tg-emoji>')


# --------------------------------------------------------------------------- #
# Entities path
# --------------------------------------------------------------------------- #

_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2190, 0x21FF), (0x2B00, 0x2BFF),
    (0x1F1E6, 0x1F1FF), (0xFE0F, 0xFE0F), (0x200D, 0x200D), (0x20E3, 0x20E3),
    (0x00A9, 0x00A9), (0x00AE, 0x00AE), (0x2122, 0x2122), (0x0030, 0x0039),
)


def _is_emojiish(ch: str) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in _EMOJI_RANGES)


def _assert_single_emoji(s: str) -> None:
    """Telegram silently drops a custom_emoji entity that does not wrap exactly
    one regular emoji. Fail loudly here instead."""
    if not s:
        raise ValueError("custom emoji fallback is empty")
    if any(ch.isspace() for ch in s):
        raise ValueError(f"custom emoji fallback contains whitespace: {s!r}")
    if any(unicodedata.category(ch).startswith("L") for ch in s):
        raise ValueError(f"custom emoji fallback contains letters: {s!r}")
    if not any(_is_emojiish(ch) for ch in s):
        raise ValueError(f"custom emoji fallback is not an emoji: {s!r}")
    if len(s) > 8:  # ZWJ sequences are legitimately multi-codepoint
        raise ValueError(f"custom emoji fallback is more than one emoji: {s!r}")


@dataclass
class E:
    """Entity builder. No parse_mode -> no special characters, ever.

        b = E()
        b.emoji("🔎", CUSTOM["studying"]).text(" Изучаю: ").code(query)
        text, entities = b.build()
    """

    _parts: list[str] = field(default_factory=list)
    _ents: list[dict] = field(default_factory=list)
    _len: int = 0  # running UTF-16 length

    # -- internals ----------------------------------------------------------
    def _push(self, s: str, ent: dict | None = None) -> "E":
        s = str(s)
        if not s:
            return self
        if ent is not None:
            ent = {**ent, "offset": self._len, "length": u16len(s)}
            self._ents.append(ent)
        self._parts.append(s)
        self._len += u16len(s)
        return self

    # -- nodes --------------------------------------------------------------
    def text(self, s) -> "E":
        return self._push(s)

    def nl(self, n: int = 1) -> "E":
        return self._push("\n" * n)

    def bold(self, s) -> "E":
        return self._push(s, {"type": "bold"})

    def italic(self, s) -> "E":
        return self._push(s, {"type": "italic"})

    def underline(self, s) -> "E":
        return self._push(s, {"type": "underline"})

    def strike(self, s) -> "E":
        return self._push(s, {"type": "strikethrough"})

    def spoiler(self, s) -> "E":
        return self._push(s, {"type": "spoiler"})

    def code(self, s) -> "E":
        return self._push(s, {"type": "code"})

    def pre(self, s, lang: str | None = None) -> "E":
        ent = {"type": "pre"}
        if lang:
            ent["language"] = lang
        return self._push(s, ent)

    def link(self, label, url: str) -> "E":
        return self._push(label, {"type": "text_link", "url": str(url)})

    def mention(self, label, user_id: int) -> "E":
        return self._push(label, {"type": "text_mention", "user": {"id": int(user_id)}})

    def quote(self, s, expandable: bool = False) -> "E":
        return self._push(s, {"type": "expandable_blockquote" if expandable else "blockquote"})

    def emoji(self, fallback: str, custom_emoji_id: str | None = None) -> "E":
        """One custom emoji. Degrades to the plain fallback when id is None."""
        _assert_single_emoji(fallback)
        if not custom_emoji_id:
            return self._push(fallback)
        return self._push(fallback, {"type": "custom_emoji",
                                     "custom_emoji_id": str(custom_emoji_id)})

    # -- output -------------------------------------------------------------
    def build(self) -> tuple[str, list[dict]]:
        text = "".join(self._parts)
        ents = _merge_adjacent(self._ents)
        for e in ents:
            if e["type"] == "custom_emoji" and e["length"] > 2:
                raise ValueError(f"custom_emoji entity spans {e['length']} u16 units")
        return text, ents

    def __len__(self) -> int:
        return self._len


def _merge_adjacent(ents: list[dict]) -> list[dict]:
    """Collapse touching identical entities — keeps the entity count down."""
    out: list[dict] = []
    for e in sorted(ents, key=lambda x: (x["offset"], x["length"])):
        if out:
            p = out[-1]
            same = {k: v for k, v in p.items() if k not in ("offset", "length")}
            cur = {k: v for k, v in e.items() if k not in ("offset", "length")}
            if same == cur and p["offset"] + p["length"] == e["offset"]:
                p["length"] += e["length"]
                continue
        out.append(dict(e))
    return out


# --------------------------------------------------------------------------- #
# Safe splitting
# --------------------------------------------------------------------------- #

_BREAKS = ("\n\n", "\n", ". ", " ")


def split_safe(text: str, entities: list[dict] | None = None,
               limit: int = MAX_TEXT) -> list[tuple[str, list[dict]]]:
    """Split a long message into chunks <= limit UTF-16 units, re-basing entity
    offsets and clipping entities that cross a boundary. Never splits inside an
    entity if a paragraph break is available before the limit."""
    entities = entities or []
    cum = _cp_to_u16_prefix(text)
    if cum[-1] <= limit:
        return [(text, [dict(e) for e in entities])]

    chunks: list[tuple[str, list[dict]]] = []
    start_cp = 0
    while start_cp < len(text):
        budget_u16 = cum[start_cp] + limit
        end_cp = _u16_to_cp(cum, budget_u16)
        if end_cp >= len(text):
            end_cp = len(text)
        else:
            window = text[start_cp:end_cp]
            cut = -1
            for br in _BREAKS:
                cut = window.rfind(br)
                if cut > 0:
                    cut += len(br)
                    break
            if cut > 0:
                end_cp = start_cp + cut
        piece = text[start_cp:end_cp]
        lo_u16, hi_u16 = cum[start_cp], cum[end_cp]
        piece_ents = []
        for e in entities:
            a, b = e["offset"], e["offset"] + e["length"]
            if b <= lo_u16 or a >= hi_u16:
                continue
            na, nb = max(a, lo_u16), min(b, hi_u16)
            ne = dict(e)
            ne["offset"], ne["length"] = na - lo_u16, nb - na
            if ne["length"] > 0:
                piece_ents.append(ne)
        chunks.append((piece, piece_ents))
        start_cp = end_cp
    return chunks


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

_MD2_RESERVED = r"_*[]()~`>#+-=|{}.!"


def md2_reserved_report(s: str) -> list[str]:
    return sorted({ch for ch in s if ch in _MD2_RESERVED})


def _demo() -> int:
    print("== HTML path ==")
    t = (H.b("Заявка ") + H.code("REQ-81_2.a") + H("\nАдрес: ")
         + H("ул. Ленина, д. 5 (корп. 2) <офис>") + H("\n")
         + H.a("Открыть", "https://t.me/example") + H("\n")
         + H.quote("Клиент: «сломался лифт»", expandable=True))
    print(t)
    print("\n== entities path ==")
    b = (E().emoji("🔎", "5312536423851630001").text(" Изучаю: ")
         .code("SELECT * FROM x WHERE a='*_[]'").nl()
         .bold("Готово").text(" — 12 из 12"))
    text, ents = b.build()
    print(repr(text))
    print(json.dumps(ents, ensure_ascii=False, indent=1))
    print("\n== utf-16 offsets ==")
    print("u16len('🔎 x') =", u16len("🔎 x"), "(python len =", len("🔎 x"), ")")
    print("\n== split_safe ==")
    long = ("абзац " * 40 + "\n\n") * 30
    parts = split_safe(long, [{"type": "bold", "offset": 0, "length": 6}], limit=1000)
    print(f"{len(long)} chars -> {len(parts)} chunks, "
          f"sizes {[u16len(p) for p, _ in parts][:5]}…")
    print("\n== md2 danger report ==")
    print("reserved chars in 'ул. Ленина (д.5)':",
          md2_reserved_report("ул. Ленина (д.5)"))
    return 0


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1251
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if len(argv) < 2 or argv[1] == "--demo":
        return _demo()
    if argv[1] == "--check":
        s = argv[2] if len(argv) > 2 else sys.stdin.read()
        bad = md2_reserved_report(s)
        print(json.dumps({
            "u16_len": u16len(s), "python_len": len(s),
            "over_text_limit": u16len(s) > MAX_TEXT,
            "over_caption_limit": u16len(s) > MAX_CAPTION,
            "markdownv2_reserved_present": bad,
            "verdict": "use H (HTML) or E (entities); do not use MarkdownV2",
        }, ensure_ascii=False, indent=1))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
