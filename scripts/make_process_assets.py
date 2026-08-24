#!/usr/bin/env python3
"""make_process_assets — generate the nine Process Pack glyphs as valid TGS files.

Produces single-colour, 100x100, 60 FPS, <=3 s Lottie animations built only from
shapes, transforms and trim paths — the subset rlottie actually renders. Designed
for a custom emoji set created with needs_repainting: true, so the glyphs adopt
the surrounding text colour in every theme.

    python make_process_assets.py --out assets/process-emoji
    python validate_sticker_assets.py assets/process-emoji --kind custom_emoji

These are working, shippable placeholders with correct motion semantics
(ongoing states loop, terminal states play once and hold). Replace them with
designed art when you have it; keep the timings.
"""
from __future__ import annotations

import argparse
import gzip
import json
import pathlib
import sys

FPS = 60
SIZE = 100
WHITE = [1, 1, 1, 1]


# --------------------------------------------------------------------------- #
# lottie primitives
# --------------------------------------------------------------------------- #

def const(v):
    return {"a": 0, "k": v}


def anim(frames):
    """frames: [(t, value), ...] -> animated property."""
    return {"a": 1, "k": [{"t": t, "s": v if isinstance(v, list) else [v]}
                          for t, v in frames]}


def transform(pos=(0, 0), scale=100, rot=0, opacity=100):
    return {"ty": "tr", "p": const(list(pos)), "a": const([0, 0]),
            "s": scale if isinstance(scale, dict) else const([scale, scale]),
            "r": rot if isinstance(rot, dict) else const(rot),
            "o": opacity if isinstance(opacity, dict) else const(opacity),
            "sk": const(0), "sa": const(0), "nm": "tr"}


def stroke(width=9):
    return {"ty": "st", "c": const(WHITE), "o": const(100), "w": const(width),
            "lc": 2, "lj": 2, "ml": 1, "nm": "st"}


def fill():
    return {"ty": "fl", "c": const(WHITE), "o": const(100), "r": 1, "nm": "fl"}


def ellipse(size, pos=(0, 0)):
    return {"ty": "el", "p": const(list(pos)), "s": const(list(size)),
            "d": 1, "nm": "el"}


def rect(size, pos=(0, 0), radius=4):
    return {"ty": "rc", "p": const(list(pos)), "s": const(list(size)),
            "r": const(radius), "d": 1, "nm": "rc"}


def path(vertices, closed=False):
    return {"ty": "sh", "d": 1, "nm": "sh", "ks": const({
        "c": closed, "i": [[0, 0]] * len(vertices), "o": [[0, 0]] * len(vertices),
        "v": [list(v) for v in vertices]})}


def trim(start, end, offset=0):
    return {"ty": "tm", "s": start if isinstance(start, dict) else const(start),
            "e": end if isinstance(end, dict) else const(end),
            "o": const(offset), "m": 1, "nm": "tm"}


def group(items, name="g"):
    return {"ty": "gr", "nm": name, "np": len(items), "it": items}


def layer(ind, shapes, op, name="l", pos=(SIZE / 2, SIZE / 2),
          rot=0, opacity=100, scale=100):
    return {"ddd": 0, "ind": ind, "ty": 4, "nm": name, "sr": 1, "ao": 0,
            "ks": {"o": opacity if isinstance(opacity, dict) else const(opacity),
                   "r": rot if isinstance(rot, dict) else const(rot),
                   "p": const([pos[0], pos[1], 0]), "a": const([0, 0, 0]),
                   "s": scale if isinstance(scale, dict) else const([scale, scale, 100])},
            "shapes": shapes, "ip": 0, "op": op, "st": 0, "bm": 0}


def doc(name, op, layers):
    return {"v": "5.9.0", "fr": FPS, "ip": 0, "op": op, "w": SIZE, "h": SIZE,
            "nm": name, "ddd": 0, "assets": [], "layers": layers, "markers": []}


# --------------------------------------------------------------------------- #
# the nine glyphs
# --------------------------------------------------------------------------- #

def g_received():                                   # eye: lens + blinking pupil
    op = 90
    lens = group([ellipse((64, 44)), stroke(8), transform()], "lens")
    pupil = group([ellipse((18, 18)), fill(),
                   transform(scale=anim([(0, [100, 100]), (30, [100, 100]),
                                          (38, [100, 10]), (46, [100, 100]),
                                          (op, [100, 100])]))], "pupil")
    return doc("received", op, [layer(1, [pupil], op, "pupil"),
                                layer(2, [lens], op, "lens")])


def g_queued():                                     # hourglass rotating 180
    op = 72
    top = group([path([(-18, -22), (18, -22), (0, 0)], True), fill(), transform()], "t")
    bot = group([path([(-18, 22), (18, 22), (0, 0)], True), fill(), transform()], "b")
    r = anim([(0, 0), (op, 180)])
    return doc("queued", op, [layer(1, [top, bot], op, "glass", rot=r)])


def g_studying():                                   # magnifier sweeping
    op = 84
    lens = group([ellipse((40, 40)), stroke(9), transform()], "lens")
    handle = group([path([(14, 14), (30, 30)]), stroke(9), transform()], "handle")
    px = anim([(0, [34, 50, 0]), (28, [66, 44, 0]), (56, [50, 56, 0]),
               (op, [34, 50, 0])])
    lay = layer(1, [lens, handle], op, "glass")
    lay["ks"]["p"] = px
    return doc("studying", op, [lay])


def g_working():                                    # three dots cascade
    op = 54
    layers = []
    for i, x in enumerate((26, 50, 74)):
        o = anim([(0, 30), (12 + i * 8, 100), (30 + i * 8, 30), (op, 30)])
        layers.append(layer(i + 1, [group([ellipse((18, 18)), fill(), transform()], "d")],
                            op, f"dot{i}", pos=(x, 50), opacity=o))
    return doc("working", op, layers)


def g_waiting_user():                               # open hand, calm pulse
    op = 120
    palm = group([rect((36, 44), (0, 6), 10), fill(), transform()], "palm")
    thumb = group([rect((12, 26), (-24, 2), 6), fill(), transform()], "thumb")
    o = anim([(0, 100), (60, 55), (op, 100)])
    return doc("waiting_user", op, [layer(1, [palm, thumb], op, "hand", opacity=o)])


def g_waiting_human():                              # clock, one full sweep
    op = 180
    face = group([ellipse((70, 70)), stroke(8), transform()], "face")
    hand = group([path([(0, 0), (0, -24)]), stroke(8),
                  transform(rot=anim([(0, 0), (op, 360)]))], "hand")
    return doc("waiting_human", op, [layer(1, [hand], op, "hand"),
                                      layer(2, [face], op, "face")])


def g_done():                                       # checkmark draws once, holds
    op = 60
    check = group([path([(-22, 2), (-6, 18), (24, -16)]),
                   trim(0, anim([(0, 0), (36, 100), (op, 100)])),
                   stroke(11), transform()], "check")
    return doc("done", op, [layer(1, [check], op, "check")])


def g_failed():                                     # triangle, single shake
    op = 60
    tri = group([path([(-26, 20), (26, 20), (0, -24)], True), stroke(9), transform()], "tri")
    bang = group([path([(0, -8), (0, 6)]), stroke(9), transform()], "bang")
    px = anim([(0, [50, 50, 0]), (8, [56, 50, 0]), (16, [44, 50, 0]),
               (24, [50, 50, 0]), (op, [50, 50, 0])])
    lay = layer(1, [tri, bang], op, "warn")
    lay["ks"]["p"] = px
    return doc("failed", op, [lay])


def g_cancelled():                                  # circle with a slash, static
    op = 60
    ring = group([ellipse((66, 66)), stroke(9), transform()], "ring")
    slash = group([path([(-20, -20), (20, 20)]), stroke(9), transform()], "slash")
    return doc("cancelled", op, [layer(1, [slash], op, "slash"),
                                  layer(2, [ring], op, "ring")])


GLYPHS = {
    "received": g_received, "queued": g_queued, "studying": g_studying,
    "working": g_working, "waiting_user": g_waiting_user,
    "waiting_human": g_waiting_human, "done": g_done, "failed": g_failed,
    "cancelled": g_cancelled,
}


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=pathlib.Path, required=True)
    p.add_argument("--only", help="comma-separated glyph keys")
    p.add_argument("--json", action="store_true", help="also write readable .json")
    a = p.parse_args(argv[1:])

    keys = list(GLYPHS) if not a.only else [k.strip() for k in a.only.split(",")]
    unknown = set(keys) - set(GLYPHS)
    if unknown:
        print(f"unknown glyphs: {sorted(unknown)}; known: {sorted(GLYPHS)}",
              file=sys.stderr)
        return 2
    a.out.mkdir(parents=True, exist_ok=True)

    for k in keys:
        d = GLYPHS[k]()
        raw = json.dumps(d, separators=(",", ":")).encode()
        tgs = a.out / f"{k}.tgs"
        # mtime=0 so rebuilds are byte-identical and content hashes are stable
        tgs.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
        if a.json:
            (a.out / f"{k}.json").write_bytes(raw)
        dur = d["op"] / FPS
        print(f"{k:<15} {tgs.stat().st_size:>6}B  {dur:.2f}s  "
              f"{'loop' if k not in ('done', 'failed', 'cancelled') else 'once+hold'}")
    print(f"\n{len(keys)} glyph(s) -> {a.out}\n"
          f"next: python scripts/validate_sticker_assets.py {a.out} --kind custom_emoji")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
