#!/usr/bin/env python3
"""build_process_pack — idempotent builder for a custom emoji (or sticker) set.

Sticker set management is not idempotent by nature. This makes it so: validate
offline, create what is missing, replace only what changed, read the ids back,
and generate the Python map handlers import.

    export BOT_TOKEN=...                 # bot token
    export STICKER_OWNER_USER_ID=...     # service account, NOT a person

    python build_process_pack.py --spec templates/process-pack.spec.yaml \
        --assets templates/assets/process-emoji --env prod \
        --out app/nav/custom_emoji.py

    ... --dry-run        # show the plan, touch nothing

Re-running with unchanged assets performs zero writes.
Exit: 0 ok, 1 build error, 2 usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"
UA = "telegram-stack/build_process_pack"


class ApiError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# transport
# --------------------------------------------------------------------------- #

def _multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = "----tgstack" + hashlib.sha1(
        str(time.time_ns()).encode()).hexdigest()[:16]
    out = bytearray()
    for k, v in fields.items():
        if v is None:
            continue
        if not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n"
                f"\r\n{v}\r\n").encode()
    for k, path in files.items():
        p = pathlib.Path(path)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; "
                f"filename=\"{p.name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode()
        out += p.read_bytes() + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def call(token: str, method: str, fields: dict | None = None,
         files: dict | None = None, *, retries: int = 3):
    url = API.format(token=token, method=method)
    body, ctype = _multipart(fields or {}, files or {})
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": ctype, "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                doc = json.loads(r.read().decode())
            if not doc.get("ok"):
                raise ApiError(f"{method}: {doc.get('description')}")
            return doc["result"]
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                doc = json.loads(raw)
            except json.JSONDecodeError:
                doc = {"description": raw[:400]}
            desc = doc.get("description", "")
            retry_after = (doc.get("parameters") or {}).get("retry_after")
            if retry_after and attempt < retries:
                print(f"  429 on {method}; sleeping {retry_after}s "
                      f"(Telegram's number, not ours)")
                time.sleep(float(retry_after))
                continue
            if e.code >= 500 and attempt < retries:
                time.sleep(min(2 ** attempt, 8))
                continue
            raise ApiError(f"{method} [{e.code}]: {desc}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries:
                time.sleep(min(2 ** attempt, 8))
                continue
            raise ApiError(f"{method}: network error {e}") from None
    raise ApiError(f"{method}: exhausted retries")


# --------------------------------------------------------------------------- #
# spec
# --------------------------------------------------------------------------- #

FORMAT_BY_EXT = {".tgs": "animated", ".webm": "video",
                 ".webp": "static", ".png": "static"}


def load_spec(path: pathlib.Path) -> dict:
    try:
        import yaml
    except ImportError:
        sys.exit("pyyaml is required: pip install pyyaml")
    with path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    if not isinstance(doc, dict) or "set" not in doc or "glyphs" not in doc:
        sys.exit(f"{path}: expected top-level 'set' and 'glyphs'")
    fallbacks = [g.get("fallback") for g in doc["glyphs"]]
    if len(set(fallbacks)) != len(fallbacks):
        sys.exit("glyph fallbacks must be unique — they are how ids are mapped back")
    return doc


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


# --------------------------------------------------------------------------- #

GENERATED = '''\
# GENERATED by scripts/build_process_pack.py. Do not edit.
# Rebuild:  python scripts/build_process_pack.py --spec {spec} --env {env}
SET_NAME = {set_name!r}
SET_TYPE = {set_type!r}
BUILT_AT = {built_at!r}

CUSTOM: dict[str, str | None] = {custom}

FALLBACK: dict[str, str] = {fallback}


def glyph(key: str) -> tuple[str, str | None]:
    """(fallback_emoji, custom_emoji_id). Always use .get semantics: a missing
    glyph must degrade to the plain emoji, never raise inside a send path."""
    return FALLBACK.get(key, "\\u2022"), CUSTOM.get(key)
'''


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--spec", type=pathlib.Path, required=True)
    p.add_argument("--assets", type=pathlib.Path, required=True)
    p.add_argument("--env", default="dev")
    p.add_argument("--out", type=pathlib.Path)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv[1:])

    spec = load_spec(a.spec)
    s, glyphs = spec["set"], spec["glyphs"]
    token = os.environ.get("BOT_TOKEN", "")
    owner = os.environ.get(s.get("owner_env", "STICKER_OWNER_USER_ID"), "")

    # ---- resolve assets and hashes (offline, always) ----
    plan = []
    for g in glyphs:
        f = a.assets / g["asset"]
        if not f.exists():
            print(f"missing asset: {f}", file=sys.stderr)
            return 1
        fmt = FORMAT_BY_EXT.get(f.suffix.lower())
        if not fmt:
            print(f"unsupported asset type: {f}", file=sys.stderr)
            return 1
        plan.append({"key": g["key"], "file": f, "format": fmt,
                     "emoji": g["fallback"], "hash": digest(f),
                     "keywords": (g.get("keywords") or [])[:19]})

    lock_path = a.assets / ".process-pack.lock.json"
    lock = {}
    if lock_path.exists():
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            lock = {}
    lock_env = lock.get(a.env, {})

    if not token:
        print("BOT_TOKEN is not set — offline plan only:\n")
        for it in plan:
            state = "unchanged" if lock_env.get(it["key"]) == it["hash"] else "changed/new"
            print(f"  {it['key']:<15} {it['emoji']}  {it['format']:<8} "
                  f"{it['hash']}  {state}")
        print("\nSet BOT_TOKEN and STICKER_OWNER_USER_ID to build.")
        return 0 if a.dry_run else 2

    me = call(token, "getMe")
    bot_username = me["username"]
    set_name = f"{s['slug']}_{a.env}_by_{bot_username}"
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", set_name):
        print(f"invalid set name {set_name!r}", file=sys.stderr)
        return 1
    title = str(s.get("title", "{bot_title} · Process")).replace(
        "{bot_title}", me.get("first_name", bot_username))
    set_type = s.get("type", "custom_emoji")

    print(f"bot @{bot_username}  set {set_name}  type {set_type}  "
          f"repaint {bool(s.get('needs_repainting'))}")

    existing = None
    try:
        existing = call(token, "getStickerSet", {"name": set_name})
    except ApiError as e:
        if "STICKERSET_INVALID" not in str(e) and "not found" not in str(e).lower():
            raise
    print(f"existing: {'yes, ' + str(len(existing['stickers'])) + ' stickers' if existing else 'no'}")

    changed = [it for it in plan if lock_env.get(it["key"]) != it["hash"]]
    print(f"changed/new glyphs: {[it['key'] for it in changed] or 'none'}")

    if a.dry_run:
        print("\n--dry-run: nothing written")
        return 0
    if not owner:
        print("STICKER_OWNER_USER_ID is not set (a service account, not a person)",
              file=sys.stderr)
        return 2

    def input_sticker(it, file_key="sticker_file"):
        return {"sticker": f"attach://{file_key}", "format": it["format"],
                "emoji_list": [it["emoji"]],
                "keywords": it["keywords"] + [f"v:{it['hash']}"]}

    if existing is None:
        first, rest = plan[0], plan[1:]
        fields = {"user_id": owner, "name": set_name, "title": title,
                  "sticker_type": set_type,
                  "stickers": [input_sticker(first)]}
        if s.get("needs_repainting") and set_type == "custom_emoji":
            fields["needs_repainting"] = "true"
        call(token, "createNewStickerSet", fields,
             {"sticker_file": first["file"]})
        print(f"  created with {first['key']}")
        for it in rest:
            call(token, "addStickerToSet",
                 {"user_id": owner, "name": set_name, "sticker": input_sticker(it)},
                 {"sticker_file": it["file"]})
            print(f"  added {it['key']}")
            time.sleep(0.4)                     # sequential: parallel adds race
    else:
        by_emoji = {st.get("emoji"): st for st in existing["stickers"]}
        for it in changed:
            old = by_emoji.get(it["emoji"])
            if old:
                call(token, "replaceStickerInSet",
                     {"user_id": owner, "name": set_name,
                      "old_sticker": old["file_id"], "sticker": input_sticker(it)},
                     {"sticker_file": it["file"]})
                print(f"  replaced {it['key']}")
            else:
                call(token, "addStickerToSet",
                     {"user_id": owner, "name": set_name, "sticker": input_sticker(it)},
                     {"sticker_file": it["file"]})
                print(f"  added {it['key']}")
            time.sleep(0.4)
        if not changed:
            print("  nothing to write (idempotent no-op)")

    thumb = next((it for it in plan if it["key"] == s.get("thumbnail_glyph", "done")), plan[0])
    try:
        call(token, "setStickerSetThumbnail",
             {"name": set_name, "user_id": owner, "format": thumb["format"],
              "thumbnail": "attach://thumb"}, {"thumb": thumb["file"]})
        print(f"  thumbnail <- {thumb['key']}")
    except ApiError as e:
        print(f"  warn: thumbnail not set ({e})")

    final = call(token, "getStickerSet", {"name": set_name})
    ids = {}
    for it in plan:
        st = next((x for x in final["stickers"] if x.get("emoji") == it["emoji"]), None)
        ids[it["key"]] = (st or {}).get("custom_emoji_id")
        if ids[it["key"]] is None and set_type == "custom_emoji":
            print(f"  warn: no custom_emoji_id for {it['key']} — will degrade to "
                  f"the plain emoji {it['emoji']}")

    lock[a.env] = {it["key"]: it["hash"] for it in plan}
    lock_path.write_text(json.dumps(lock, indent=1, sort_keys=True), encoding="utf-8")

    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(GENERATED.format(
            spec=a.spec, env=a.env, set_name=set_name, set_type=set_type,
            built_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            custom=json.dumps(ids, ensure_ascii=False, indent=4),
            fallback=json.dumps({it["key"]: it["emoji"] for it in plan},
                                ensure_ascii=False, indent=4)), encoding="utf-8")
        print(f"wrote {a.out}")
    print(f"\nset: https://t.me/addstickers/{set_name}"
          if set_type != "custom_emoji" else f"\nemoji set: {set_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
