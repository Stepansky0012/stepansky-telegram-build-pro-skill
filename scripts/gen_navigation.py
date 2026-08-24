#!/usr/bin/env python3
"""gen_navigation — Navigation Contract validator and code generator.

The contract (navigation.yaml) is the spec for a bot's interface. This script is
its test suite and its compiler:

  validate  reachability, depth, escapes, confirmations, callback budget,
            namespace collisions, orphan jobs
  generate  app/nav/{callbacks,keyboards,routers,help}.py + a Mermaid graph

    python gen_navigation.py navigation.yaml --check
    python gen_navigation.py navigation.yaml --out app/nav --diagram nav.mmd

Exit codes: 0 ok, 1 contract errors, 2 usage/parse error.
"""
from __future__ import annotations

import argparse
import collections
import pathlib
import re
import sys

MAX_CB_BYTES = 64
DEFAULT_MAX_ARG_BYTES = 16
MAX_ACTIONS = 5
MAX_DEPTH = 2          # taps from a root; a `confirm` screen may sit one deeper
MAX_FSM_STEPS = 5

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
NS_RE = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
SURFACES = {"command", "reply_keyboard", "inline", "inline_paginated",
            "fsm", "miniapp", "inline_mode", "checklist"}


class Err(list):
    def add(self, where: str, msg: str) -> None:
        self.append(f"{where}: {msg}")


# --------------------------------------------------------------------------- #
# load
# --------------------------------------------------------------------------- #

def load(path: pathlib.Path) -> dict:
    try:
        import yaml
    except ImportError:
        sys.exit("pyyaml is required: pip install pyyaml")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        sys.exit(f"{path}: top level must be a mapping")
    return data


def is_root_entry(e: str) -> bool:
    return e.startswith("/") or e.startswith("start=") or e.startswith("startapp=")


# --------------------------------------------------------------------------- #
# validate
# --------------------------------------------------------------------------- #

def validate(doc: dict) -> tuple[Err, dict]:
    err = Err()
    version = str(doc.get("version", "v1"))
    if not re.fullmatch(r"v\d+", version):
        err.add("version", f"must look like v1, got {version!r}")

    max_arg = int(doc.get("max_arg_bytes", DEFAULT_MAX_ARG_BYTES))
    jobs = doc.get("jobs") or {}
    screens = doc.get("screens") or []
    if not screens:
        err.add("screens", "contract has no screens")
        return err, {}

    by_id: dict[str, dict] = {}
    for s in screens:
        sid = s.get("id")
        if not sid or not ID_RE.match(str(sid)):
            err.add(f"screen {sid!r}", "id must match ^[a-z][a-z0-9_]{0,31}$")
            continue
        if sid in by_id:
            err.add(f"screen {sid}", "duplicate screen id")
            continue
        by_id[sid] = s

    # namespaces
    ns_owner: dict[str, str] = {}
    for sid, s in by_id.items():
        ns = str(s.get("ns") or sid[:16])
        if not NS_RE.match(ns):
            err.add(f"screen {sid}", f"ns {ns!r} must match ^[a-z][a-z0-9_]{{0,15}}$")
        if ns in ns_owner:
            err.add(f"screen {sid}", f"namespace collision with {ns_owner[ns]} ({ns})")
        ns_owner[ns] = sid
        s["_ns"] = ns

    # per-screen checks + edges
    edges: dict[str, set[str]] = collections.defaultdict(set)
    roots: set[str] = set()
    claimed_jobs: set[str] = set()

    for sid, s in by_id.items():
        where = f"screen {sid}"
        surface = s.get("surface")
        if surface not in SURFACES:
            err.add(where, f"unknown surface {surface!r}; expected one of {sorted(SURFACES)}")
        if not s.get("title"):
            err.add(where, "missing title")

        for j in s.get("jobs") or []:
            if j not in jobs:
                err.add(where, f"unknown job {j!r} (not in jobs:)")
            claimed_jobs.add(j)

        entries = [str(e) for e in (s.get("entry") or [])]
        for e in entries:
            if is_root_entry(e):
                roots.add(sid)
            elif e in by_id:
                edges[e].add(sid)
            else:
                err.add(where, f"entry {e!r} is neither a command/deep link nor a known screen")

        actions = s.get("actions") or []
        primary = [a for a in actions if a.get("to") or a.get("url") or a.get("web_app")]
        if len(primary) > MAX_ACTIONS:
            err.add(where, f"{len(primary)} actions; max {MAX_ACTIONS} "
                           f"(re-run JSA Stage 3 — the surface is wrong)")

        for a in actions:
            label = a.get("label")
            if not label:
                err.add(where, "action without label")
            elif len(str(label)) > 20:
                err.add(where, f"label {label!r} longer than 20 chars")
            tgt = a.get("to")
            if tgt:
                if tgt not in by_id:
                    err.add(where, f"dangling reference to {tgt!r}")
                else:
                    edges[sid].add(tgt)
                    if a.get("destructive") and not by_id[tgt].get("confirm"):
                        err.add(where, f"destructive action -> {tgt} which is not confirm: true")
            elif not (a.get("url") or a.get("web_app") or a.get("switch_inline")):
                err.add(where, f"action {label!r} has no to/url/web_app/switch_inline")

            # callback budget at max arg length
            act = str(a.get("action") or (by_id[tgt]["_ns"] if tgt in by_id else "act"))
            packed = f"{version}:{s.get('_ns','x')}:{act}:" + "x" * max_arg
            if len(packed.encode()) > MAX_CB_BYTES:
                err.add(where, f"callback overflow for {label!r}: "
                               f"{len(packed.encode())}B > {MAX_CB_BYTES}B")

        escape = s.get("escape")
        if escape not in (None, "none", "back", "home"):
            err.add(where, f"escape must be back|home|none, got {escape!r}")
        if escape in (None, "none") and sid not in roots:
            err.add(where, "no escape (only roots may have escape: none)")

        if s.get("confirm") and not s.get("confirm_object"):
            err.add(where, "confirm: true requires confirm_object naming what is affected")

        if surface == "fsm":
            steps = s.get("steps") or []
            if not steps:
                err.add(where, "surface: fsm requires steps")
            if len(steps) > MAX_FSM_STEPS:
                err.add(where, f"{len(steps)} fsm steps; >{MAX_FSM_STEPS} belongs in a Mini App")
            if not s.get("state"):
                err.add(where, "surface: fsm requires state")
            for st in steps:
                if not st.get("id") or not st.get("prompt"):
                    err.add(where, f"fsm step {st!r} needs id and prompt")
        if surface == "inline_paginated" and not s.get("page_size"):
            err.add(where, "surface: inline_paginated requires page_size")
        if surface == "miniapp" and not s.get("app_route"):
            err.add(where, "surface: miniapp requires app_route")

    if not roots:
        err.add("contract", "no root screen (none has a command or deep-link entry)")

    # reachability + depth
    depth: dict[str, int] = {r: 0 for r in roots}
    q = collections.deque(roots)
    while q:
        cur = q.popleft()
        for nxt in edges[cur]:
            if nxt not in depth:
                depth[nxt] = depth[cur] + 1
                q.append(nxt)

    for sid, s in by_id.items():
        if sid not in depth:
            err.add(f"screen {sid}", "unreachable screen")
            continue
        allowed = MAX_DEPTH + (1 if s.get("confirm") else 0)
        if depth[sid] > allowed:
            err.add(f"screen {sid}", f"depth {depth[sid]} > {allowed} — "
                                     f"pick a different surface (JSA Stage 3), do not add a submenu")

    for j in jobs:
        if j not in claimed_jobs:
            err.add(f"job {j}", "orphan job — no screen serves this requirement")

    model = {"version": version, "by_id": by_id, "roots": roots, "depth": depth,
             "edges": edges, "jobs": jobs, "max_arg": max_arg,
             "bot": doc.get("bot", "bot"),
             "default_response_mode": doc.get("default_response_mode", "plain")}
    return err, model


# --------------------------------------------------------------------------- #
# generate
# --------------------------------------------------------------------------- #

BANNER = ("# GENERATED by scripts/gen_navigation.py from navigation.yaml.\n"
          "# Do not edit. Change the contract and regenerate.\n")


def gen_callbacks(m: dict) -> str:
    out = [BANNER, "from dataclasses import dataclass\n", "",
           "MAX_CB_BYTES = 64", "", "", "@dataclass(frozen=True)",
           "class CallbackProtocol:", f'    version: str = "{m["version"]}"',
           '    sep: str = ":"', "",
           "    def pack(self, domain: str, action: str, arg: str | int = \"\") -> str:",
           "        raw = self.sep.join(str(p) for p in (self.version, domain, action, arg) if p != \"\")",
           "        if len(raw.encode()) > MAX_CB_BYTES:",
           "            raise ValueError(f\"callback_data overflow ({len(raw.encode())}B): {raw!r}\")",
           "        return raw", "",
           "    def unpack(self, data: str) -> tuple[str, str, str, str]:",
           "        parts = data.split(self.sep, 3) + [\"\", \"\", \"\", \"\"]",
           "        return parts[0], parts[1], parts[2], parts[3]", "", "",
           "CB = CallbackProtocol()", "",
           "NAMESPACES = {"]
    for sid, s in m["by_id"].items():
        out.append(f'    "{sid}": "{s["_ns"]}",')
    out.append("}")
    return "\n".join(out) + "\n"


def gen_keyboards(m: dict) -> str:
    out = [BANNER,
           "from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,",
           "                           KeyboardButton, ReplyKeyboardMarkup, WebAppInfo)",
           "", "from .callbacks import CB", "",
           'WEBAPP_URL = ""  # set from config at import time', "",
           "try:  # optional animated button icons",
           "    from .custom_emoji import CUSTOM",
           "except ImportError:  # pragma: no cover",
           "    CUSTOM: dict[str, str | None] = {}", "",
           'ESCAPE_LABEL = {"back": "\\u2039 Назад", "home": "\\u2302 Меню"}', ""]

    for sid, s in m["by_id"].items():
        ns, surface = s["_ns"], s.get("surface")
        fn = f"kb_{sid}"
        args = "()" if surface != "inline_paginated" else "(items, page: int = 0)"
        out += [f"def {fn}{args}:",
                f'    """{s.get("title", sid)} — jobs: {", ".join(s.get("jobs") or []) or "-"}"""']
        if surface == "reply_keyboard":
            rows = []
            for a in s.get("actions") or []:
                rows.append(f'        [KeyboardButton(text="{a["label"]}")],')
            out += ["    return ReplyKeyboardMarkup(keyboard=["] + rows + [
                "    ], resize_keyboard=True, is_persistent=True)", "", ""]
            continue

        out.append("    rows: list[list[InlineKeyboardButton]] = []")
        if surface == "inline_paginated":
            ps = s.get("page_size", 5)
            item = s.get("item_action") or {}
            act = item.get("to", "open")
            tgt_ns = m["by_id"].get(act, {}).get("_ns", "open")
            out += [f"    page_size = {ps}",
                    "    total = max(1, (len(items) + page_size - 1) // page_size)",
                    "    page = max(0, min(page, total - 1))",
                    "    for it in items[page * page_size:(page + 1) * page_size]:",
                    "        rows.append([InlineKeyboardButton(text=it.label,",
                    f'            callback_data=CB.pack("{ns}", "{tgt_ns}", it.id))])',
                    "    nav: list[InlineKeyboardButton] = []",
                    "    if page > 0:",
                    '        nav.append(InlineKeyboardButton(text="\\u2039",',
                    f'            callback_data=CB.pack("{ns}", "page", page - 1)))',
                    "    if total > 1:",
                    '        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total}",',
                    f'            callback_data=CB.pack("{ns}", "noop")))',
                    "    if page < total - 1:",
                    '        nav.append(InlineKeyboardButton(text="\\u203a",',
                    f'            callback_data=CB.pack("{ns}", "page", page + 1)))',
                    "    if nav:", "        rows.append(nav)"]
        for a in s.get("actions") or []:
            label, tgt = a["label"], a.get("to")
            icon = a.get("icon")
            icon_kw = f', icon_custom_emoji_id=CUSTOM.get("{icon}")' if icon else ""
            if a.get("web_app"):
                out.append(f'    rows.append([InlineKeyboardButton(text="{label}", '
                           f'web_app=WebAppInfo(url=WEBAPP_URL + "{a["web_app"]}"){icon_kw})])')
            elif a.get("url"):
                out.append(f'    rows.append([InlineKeyboardButton(text="{label}", '
                           f'url="{a["url"]}"{icon_kw})])')
            elif a.get("switch_inline") is not None:
                out.append(f'    rows.append([InlineKeyboardButton(text="{label}", '
                           f'switch_inline_query="{a["switch_inline"]}"{icon_kw})])')
            else:
                act = a.get("action") or m["by_id"].get(tgt, {}).get("_ns", "act")
                out.append(f'    rows.append([InlineKeyboardButton(text="{label}", '
                           f'callback_data=CB.pack("{ns}", "{act}"){icon_kw})])')
        esc = s.get("escape")
        if esc in ("back", "home"):
            out += [f'    rows.append([InlineKeyboardButton(text=ESCAPE_LABEL["{esc}"],',
                    f'        callback_data=CB.pack("nav", "{esc}"))])']
        out += ["    return InlineKeyboardMarkup(inline_keyboard=rows)", "", ""]

    return "\n".join(out) + "\n"


def gen_routers(m: dict) -> str:
    out = [BANNER, "from aiogram import F, Router", "",
           "from .callbacks import CB", "",
           "router = Router(name=\"nav\")", "", ""]
    for sid, s in m["by_id"].items():
        ns = s["_ns"]
        for a in s.get("actions") or []:
            if not a.get("to"):
                continue
            act = a.get("action") or m["by_id"].get(a["to"], {}).get("_ns", "act")
            fn = f"on_{ns}_{act}"
            out += [f'@router.callback_query(F.data.startswith(CB.pack("{ns}", "{act}")))',
                    f"async def {fn}(cb, **kw):",
                    f'    """{s.get("title", sid)} -> {a["to"]} ({a["label"]})',
                    f'    jobs: {", ".join(m["by_id"][a["to"]].get("jobs") or []) or "-"}"""',
                    "    raise NotImplementedError  # implement, then delete this line",
                    "", ""]
    for sid, s in m["by_id"].items():
        for e in s.get("entry") or []:
            if str(e).startswith("/"):
                cmd = str(e)[1:]
                out += [f'@router.message(F.text.startswith("/{cmd}"))',
                        f"async def cmd_{cmd}(msg, state, **kw):",
                        f'    """entry -> {sid}. Clears FSM state (Law 10)."""',
                        "    await state.clear()",
                        "    raise NotImplementedError", "", ""]
    return "\n".join(out) + "\n"


def gen_help(m: dict) -> str:
    lines = [BANNER, "HELP_SECTIONS = ["]
    for sid, s in m["by_id"].items():
        cmds = [e for e in (s.get("entry") or []) if str(e).startswith("/")]
        if not cmds and s.get("surface") == "command":
            continue
        jobs = "; ".join(m["jobs"].get(j, j) for j in (s.get("jobs") or []))
        if not (cmds or jobs):
            continue
        lines.append(f'    {{"screen": "{sid}", "title": "{s.get("title", sid)}", '
                     f'"commands": {cmds!r}, "does": "{jobs}"}},')
    lines += ["]", "", "",
              "def render_help() -> list[tuple[str, str]]:",
              '    """(title, description) pairs — feed to tg_text.H, never to MarkdownV2."""',
              "    out = []",
              "    for s in HELP_SECTIONS:",
              "        cmds = \" \".join(s[\"commands\"])",
              "        out.append((s[\"title\"] + ((\" \" + cmds) if cmds else \"\"), s[\"does\"]))",
              "    return out", ""]
    return "\n".join(lines)


def gen_mermaid(m: dict) -> str:
    out = ["flowchart TD"]
    for sid, s in m["by_id"].items():
        title = str(s.get("title", sid)).replace('"', "'")
        d = m["depth"].get(sid, "?")
        shape = f'{sid}["{title}<br/><small>{s.get("surface")} · d{d}</small>"]'
        out.append("    " + shape)
    for sid, s in m["by_id"].items():
        for a in s.get("actions") or []:
            if a.get("to"):
                arrow = "-.->" if a.get("destructive") else "-->"
                out.append(f'    {sid} {arrow}|{a["label"]}| {a["to"]}')
    for r in sorted(m["roots"]):
        out.append(f"    style {r} stroke-width:3px")
    for sid, s in m["by_id"].items():
        if s.get("confirm"):
            out.append(f"    style {sid} stroke-dasharray: 4 3")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #

def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("contract", type=pathlib.Path)
    p.add_argument("--check", action="store_true", help="validate only")
    p.add_argument("--out", type=pathlib.Path, help="output package dir, e.g. app/nav")
    p.add_argument("--diagram", type=pathlib.Path, help="write a Mermaid graph here")
    a = p.parse_args(argv[1:])

    if not a.contract.exists():
        print(f"contract not found: {a.contract}", file=sys.stderr)
        return 2

    err, model = validate(load(a.contract))
    if err:
        print(f"{len(err)} contract error(s):", file=sys.stderr)
        for e in err:
            print(f"  - {e}", file=sys.stderr)
        return 1

    n = len(model["by_id"])
    maxd = max(model["depth"].values()) if model["depth"] else 0
    print(f"contract ok: {n} screens, {len(model['jobs'])} jobs, "
          f"{len(model['roots'])} root(s), max depth {maxd}")

    if a.diagram:
        a.diagram.parent.mkdir(parents=True, exist_ok=True)
        a.diagram.write_text(gen_mermaid(model), encoding="utf-8")
        print(f"wrote {a.diagram}")

    if a.check or not a.out:
        return 0

    a.out.mkdir(parents=True, exist_ok=True)
    files = {"__init__.py": BANNER, "callbacks.py": gen_callbacks(model),
             "keyboards.py": gen_keyboards(model), "routers.py": gen_routers(model),
             "help.py": gen_help(model)}
    for name, body in files.items():
        (a.out / name).write_text(body, encoding="utf-8")
        print(f"wrote {a.out / name}")
    print(f"implementation checklist: "
          f"{files['routers.py'].count('NotImplementedError')} stub(s) in routers.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
