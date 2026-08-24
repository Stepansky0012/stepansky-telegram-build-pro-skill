#!/usr/bin/env python3
"""validate_sticker_assets — offline preflight for stickers and custom emoji.

Telegram's rejection messages are terse (STICKER_PNG_DIMENSIONS,
STICKER_TGS_NOTGZIPPED) and the round trip is slow. Check locally first.

    python validate_sticker_assets.py assets/process-emoji --kind custom_emoji
    python validate_sticker_assets.py assets/pack --kind sticker --format json

Zero dependencies. Uses ffprobe for WEBM when it is on PATH, otherwise falls
back to a best-effort EBML scan and reports what it could not verify.

Exit: 0 all valid, 1 violations found, 2 usage error.
"""
from __future__ import annotations

import argparse
import gzip
import json
import pathlib
import re
import shutil
import struct
import subprocess
import sys

SPEC = {
    "sticker":      {"side": 512, "exact_both": False,
                     "tgs_max": 64 * 1024, "webm_max": 256 * 1024,
                     "static_max": 512 * 1024},
    "custom_emoji": {"side": 100, "exact_both": True,
                     "tgs_max": 64 * 1024, "webm_max": 256 * 1024,
                     "static_max": 64 * 1024},
}
MAX_DURATION = 3.0
TGS_FPS = 60
WEBM_MAX_FPS = 30


# --------------------------------------------------------------------------- #
# image headers
# --------------------------------------------------------------------------- #

def png_info(b: bytes) -> dict:
    if b[:8] != b"\x89PNG\r\n\x1a\n":
        return {}
    w, h = struct.unpack(">II", b[16:24])
    color_type = b[25]
    return {"w": w, "h": h, "alpha": color_type in (4, 6),
            "alpha_known": color_type != 3}


def webp_info(b: bytes) -> dict:
    if b[:4] != b"RIFF" or b[8:12] != b"WEBP":
        return {}
    chunk = b[12:16]
    if chunk == b"VP8X":
        w = int.from_bytes(b[24:27], "little") + 1
        h = int.from_bytes(b[27:30], "little") + 1
        return {"w": w, "h": h, "alpha": bool(b[20] & 0x10), "alpha_known": True,
                "animated": bool(b[20] & 0x02)}
    if chunk == b"VP8 ":
        i = b.find(b"\x9d\x01\x2a", 20, 40)
        if i < 0:
            return {}
        w = int.from_bytes(b[i + 3:i + 5], "little") & 0x3FFF
        h = int.from_bytes(b[i + 5:i + 7], "little") & 0x3FFF
        return {"w": w, "h": h, "alpha": False, "alpha_known": True}
    if chunk == b"VP8L":
        if b[20] != 0x2F:
            return {}
        bits = int.from_bytes(b[21:25], "little")
        w = (bits & 0x3FFF) + 1
        h = ((bits >> 14) & 0x3FFF) + 1
        return {"w": w, "h": h, "alpha": bool((bits >> 28) & 1), "alpha_known": True}
    return {}


# --------------------------------------------------------------------------- #
# tgs / webm
# --------------------------------------------------------------------------- #

UNSUPPORTED_LOTTIE = {
    '"ty":5': "text layer (rlottie cannot render text — convert to outlines)",
    '"ty":2': "image layer (rlottie cannot render images — use shapes)",
    '"ef":': "layer effects (unsupported by rlottie)",
    '"x":"': "expression (unsupported by rlottie)",
}


def tgs_info(path: pathlib.Path) -> tuple[dict, list[str]]:
    errs: list[str] = []
    raw = path.read_bytes()
    if raw[:2] != b"\x1f\x8b":
        return {}, ["not gzipped — .tgs is a gzipped Lottie JSON (STICKER_TGS_NOTGZIPPED)"]
    try:
        data = gzip.decompress(raw)
    except OSError as e:
        return {}, [f"gzip error: {e}"]
    try:
        doc = json.loads(data)
    except json.JSONDecodeError as e:
        return {}, [f"not valid JSON inside the gzip: {e}"]

    info = {"w": doc.get("w"), "h": doc.get("h"), "fr": doc.get("fr"),
            "ip": doc.get("ip", 0), "op": doc.get("op")}
    for k in ("w", "h", "fr", "op"):
        if info[k] in (None, 0):
            errs.append(f"Lottie field {k!r} missing or zero")
    if info["fr"] and info["op"] is not None:
        info["duration"] = (info["op"] - info["ip"]) / info["fr"]
    flat = data.decode("utf-8", "replace")
    flat_nospace = re.sub(r"\s+", "", flat)
    for needle, why in UNSUPPORTED_LOTTIE.items():
        if needle in flat_nospace:
            errs.append(f"contains {why}")
    return info, errs


def ffprobe(path: pathlib.Path) -> dict | None:
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-print_format", "json", "-show_streams",
             "-show_format", str(path)],
            capture_output=True, text=True, timeout=25, check=True).stdout
        doc = json.loads(out)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return None
    v = next((s for s in doc.get("streams", []) if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in doc.get("streams", []))
    fps = 0.0
    if v.get("avg_frame_rate", "0/0") not in ("0/0", None):
        num, _, den = v["avg_frame_rate"].partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return {"w": v.get("width"), "h": v.get("height"), "codec": v.get("codec_name"),
            "fps": round(fps, 3), "audio": has_audio,
            "duration": float(doc.get("format", {}).get("duration", 0) or 0)}


def webm_scan(b: bytes) -> dict:
    """Best-effort EBML scan when ffprobe is unavailable."""
    if b[:4] != b"\x1a\x45\xdf\xa3":
        return {}
    info: dict = {"audio": None, "w": None, "h": None}
    if b.find(b"\x83\x81\x02") >= 0:          # TrackType == 2 (audio)
        info["audio"] = True
    m = re.search(rb"\xb0(?P<sz>[\x81-\x84])(?P<val>.{1,4})", b, re.S)
    if m:
        n = m.group("sz")[0] - 0x80
        info["w"] = int.from_bytes(m.group("val")[:n], "big")
    m = re.search(rb"\xba(?P<sz>[\x81-\x84])(?P<val>.{1,4})", b, re.S)
    if m:
        n = m.group("sz")[0] - 0x80
        info["h"] = int.from_bytes(m.group("val")[:n], "big")
    info["codec"] = "vp9" if b.find(b"V_VP9") >= 0 else (
        "vp8" if b.find(b"V_VP8") >= 0 else None)
    return info


# --------------------------------------------------------------------------- #

def check(path: pathlib.Path, kind: str) -> tuple[list[str], list[str], dict]:
    spec = SPEC[kind]
    side, exact = spec["side"], spec["exact_both"]
    errs: list[str] = []
    warns: list[str] = []
    size = path.stat().st_size
    ext = path.suffix.lower()
    b = path.read_bytes()[:4096] if ext != ".tgs" else b""
    info: dict = {}

    def dims(w, h, limit_key: str):
        if not w or not h:
            warns.append("could not read dimensions — verify manually")
            return
        if exact:
            if (w, h) != (side, side):
                errs.append(f"{w}x{h}: {kind} must be exactly {side}x{side}")
        else:
            if max(w, h) != side or min(w, h) > side:
                errs.append(f"{w}x{h}: one side must be exactly {side}, "
                            f"the other <= {side}")
        if size > spec[limit_key]:
            errs.append(f"{size} bytes > {spec[limit_key]} limit")

    if ext in (".png", ".webp"):
        info = png_info(b) if ext == ".png" else webp_info(b)
        if not info:
            errs.append(f"unrecognised {ext} header")
        else:
            dims(info.get("w"), info.get("h"), "static_max")
            if info.get("alpha_known") and not info.get("alpha"):
                warns.append("no alpha channel — stickers need a transparent background")
            if info.get("animated"):
                errs.append("animated WEBP is not a Telegram sticker format; "
                            "use TGS (vector) or WEBM/VP9 (video)")

    elif ext == ".tgs":
        info, e = tgs_info(path)
        errs += e
        if info:
            dims(info.get("w"), info.get("h"), "tgs_max")
            if info.get("fr") and info["fr"] != TGS_FPS:
                errs.append(f"fr={info['fr']}: animated stickers must be {TGS_FPS} FPS "
                            f"(30 is the limit for VIDEO stickers, not TGS)")
            d = info.get("duration")
            if d is not None and d > MAX_DURATION + 1e-6:
                errs.append(f"duration {d:.2f}s > {MAX_DURATION}s")
            if d is not None and d <= 0:
                errs.append("zero-length animation")

    elif ext == ".webm":
        probed = ffprobe(path)
        info = probed or webm_scan(b)
        if not info:
            errs.append("not a WEBM/Matroska file")
        else:
            dims(info.get("w"), info.get("h"), "webm_max")
            if info.get("codec") and info["codec"] != "vp9":
                errs.append(f"codec {info['codec']}: video stickers must be VP9")
            if info.get("audio"):
                errs.append("has an audio track — sticker video must be silent")
            if probed:
                if info["fps"] > WEBM_MAX_FPS + 0.5:
                    errs.append(f"{info['fps']} FPS > {WEBM_MAX_FPS}")
                if info["duration"] > MAX_DURATION + 1e-6:
                    errs.append(f"duration {info['duration']:.2f}s > {MAX_DURATION}s")
            else:
                warns.append("ffprobe not on PATH — FPS, duration and codec unverified")
                if info.get("audio") is None:
                    warns.append("audio track presence unverified")
    else:
        errs.append(f"unsupported extension {ext}; expected .webp .png .tgs .webm")

    info["bytes"] = size
    return errs, warns, info


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("path", type=pathlib.Path)
    p.add_argument("--kind", choices=sorted(SPEC), required=True)
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("--strict-warnings", action="store_true")
    a = p.parse_args(argv[1:])

    if not a.path.exists():
        print(f"path not found: {a.path}", file=sys.stderr)
        return 2
    files = sorted(f for f in ([a.path] if a.path.is_file() else a.path.rglob("*"))
                   if f.is_file() and f.suffix.lower() in
                   (".png", ".webp", ".tgs", ".webm"))
    if not files:
        print(f"no sticker assets under {a.path}", file=sys.stderr)
        return 2

    report, bad, warned = [], 0, 0
    for f in files:
        errs, warns, info = check(f, a.kind)
        bad += bool(errs)
        warned += bool(warns)
        report.append({"file": str(f), "ok": not errs, "errors": errs,
                       "warnings": warns, "info": info})

    if a.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    else:
        for r in report:
            i = r["info"]
            dim = f"{i.get('w') or '?'}x{i.get('h') or '?'}"
            head = (f"{'ok  ' if r['ok'] else 'FAIL'}  {pathlib.Path(r['file']).name:<24}"
                    f" {dim:<9} {i.get('bytes')}B")
            if i.get("duration") is not None:
                head += f"  {float(i['duration']):.2f}s"
            if i.get("fr") or i.get("fps"):
                head += f"  {i.get('fr') or i.get('fps')}fps"
            print(head)
            for e in r["errors"]:
                print(f"        ERROR {e}")
            for w in r["warnings"]:
                print(f"        warn  {w}")
        print(f"\n{len(files)} asset(s), {bad} invalid, {warned} with warnings "
              f"[{a.kind}: {SPEC[a.kind]['side']}px]")
    return 1 if (bad or (a.strict_warnings and warned)) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
