#!/usr/bin/env python3
"""check_api_version — Step 0 of every Telegram task.

Fetches the Bot API changelog and compares the live version with the one this
project is pinned to. The Bot API changed nine times between 2025-04 and
2026-07; a plan written against a stale surface is a plan that has to be redone.

    python check_api_version.py --pinned 10.2
    python check_api_version.py --pinned 10.2 --strict     # offline is a failure
    python check_api_version.py --pinned 10.2 --delta      # print what changed

Exit: 0 up to date (or offline and not --strict), 1 newer version available,
2 network failure with --strict.
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request

CHANGELOG = "https://core.telegram.org/bots/api-changelog"
DOCS = "https://core.telegram.org/bots/api"
UA = "telegram-stack/check_api_version"
HEADING = re.compile(r"Bot\s+API\s+(\d+)\.(\d+)\s*(?:</a>)?\s*", re.I)
DATED = re.compile(r"Bot\s+API\s+(\d+)\.(\d+)[^<]{0,40}?"
                   r"(January|February|March|April|May|June|July|August|"
                   r"September|October|November|December)\s+(\d{1,2}),\s*(\d{4})", re.I)


def ver(t: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d+)\.(\d+)", t.strip())
    if not m:
        raise SystemExit(f"--pinned must look like 10.2, got {t!r}")
    return int(m[1]), int(m[2])


def fetch(url: str, timeout: float) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pinned", required=True)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--delta", action="store_true")
    p.add_argument("--timeout", type=float, default=12.0)
    a = p.parse_args(argv[1:])
    pinned = ver(a.pinned)

    try:
        html = fetch(CHANGELOG, a.timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        msg = (f"OFFLINE: could not reach {CHANGELOG} ({e.__class__.__name__}). "
               f"Proceeding against the pinned version {a.pinned}. "
               f"Say this out loud — do not silently assume the surface is current.")
        print(msg, file=sys.stderr)
        return 2 if a.strict else 0

    versions = sorted({(int(m[1]), int(m[2])) for m in HEADING.finditer(html)})
    if not versions:
        print("could not parse any version from the changelog — fetch it manually:\n  "
              + CHANGELOG, file=sys.stderr)
        return 2 if a.strict else 0
    live = versions[-1]
    dates = {(int(m[1]), int(m[2])): f"{m[3]} {m[4]}, {m[5]}"
             for m in DATED.finditer(html)}

    def fmt(v):
        d = dates.get(v)
        return f"{v[0]}.{v[1]}" + (f" ({d})" if d else "")

    if live <= pinned:
        print(f"Bot API up to date: pinned {fmt(pinned)}, live {fmt(live)}")
        return 0

    newer = [v for v in versions if v > pinned]
    print(f"Bot API MOVED: pinned {fmt(pinned)}, live {fmt(live)}")
    print("newer versions: " + ", ".join(fmt(v) for v in newer))
    print(f"\nRead the delta before planning:\n  {CHANGELOG}\n  {DOCS}")
    print("Then either raise the pin (and update the stack) or state explicitly "
          "which new capabilities you are choosing not to use.")

    if a.delta:
        # crude but useful: print the changelog text down to the pinned heading
        cut = re.split(rf"Bot\s+API\s+{pinned[0]}\.{pinned[1]}\b", html, maxsplit=1)[0]
        text = re.sub(r"<[^>]+>", " ", cut)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text)
        print("\n--- delta (raw, verify against the docs) ---")
        print(text.strip()[-6000:])
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
