#!/usr/bin/env python3
"""tg_preflight — the gate you run before claiming a Telegram bot is done.

Runs every machine-checkable invariant in the stack and prints one verdict per
gate. Evidence before assertions: if this has not printed PASS, the work is not
finished, regardless of how it looked when you tried it by hand.

    python tg_preflight.py --project .
    python tg_preflight.py --project . --pinned 10.2 --offline
    python tg_preflight.py --project . --format json

Config (optional) — .tgstack.json at the project root:
    {"pinned_api": "10.2", "code": ["app", "api"], "assets": "assets/process-emoji",
     "sticker_kind": "custom_emoji", "contract": "navigation.yaml",
     "styles": ["miniapp/src"]}

Exit: 0 all gates pass, 1 one or more failed, 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_PINNED = "10.2"


class Gate:
    def __init__(self, name: str, why: str):
        self.name, self.why = name, why
        self.status = "skip"
        self.detail = ""
        self.output = ""

    def run(self, argv: list[str], *, cwd: pathlib.Path,
            ok_codes=(0,), skip_reason: str | None = None) -> "Gate":
        if skip_reason:
            self.detail = skip_reason
            return self
        try:
            r = subprocess.run([sys.executable, *argv], cwd=cwd, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=300)
        except (OSError, subprocess.SubprocessError) as e:
            self.status, self.detail = "fail", f"could not run: {e}"
            return self
        self.output = (r.stdout or "") + (r.stderr or "")
        self.status = "pass" if r.returncode in ok_codes else "fail"
        last = [ln for ln in self.output.strip().splitlines() if ln.strip()]
        self.detail = last[-1][:160] if last else f"exit {r.returncode}"
        return self


def structural(project: pathlib.Path, cfg: dict) -> Gate:
    g = Gate("structure", "secrets are not committed; layers exist")
    problems: list[str] = []

    gi = project / ".gitignore"
    if gi.exists():
        body = gi.read_text(encoding="utf-8", errors="replace")
        for needed in (".env", "__pycache__"):
            if needed not in body:
                problems.append(f".gitignore does not cover {needed}")
    else:
        problems.append("no .gitignore — a bot token will reach the repo")

    for tracked in (".env", ".env.prod", "app/nav/custom_emoji.py"):
        p = project / tracked
        if p.exists() and (project / ".git").exists():
            try:
                r = subprocess.run(["git", "ls-files", "--error-unmatch", tracked],
                                   cwd=project, capture_output=True, text=True,
                                   timeout=30)
                if r.returncode == 0:
                    problems.append(f"{tracked} is tracked by git")
            except (OSError, subprocess.SubprocessError):
                pass

    for d in cfg.get("code", ["app"]):
        base = project / d
        if not base.exists():
            continue
        if (base / "handlers").exists() and not (base / "services").exists():
            problems.append(f"{d}/handlers exists without {d}/services — "
                            f"business logic has nowhere to live but the handlers")
        if (base / "tg" / "gateway.py").exists() is False and (base / "handlers").exists():
            problems.append(f"{d}/tg/gateway.py is missing — "
                            f"every outgoing call must go through one gateway")

    g.status = "fail" if problems else "pass"
    g.detail = "; ".join(problems)[:400] if problems else "ok"
    g.output = "\n".join(problems)
    return g


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", type=pathlib.Path, default=pathlib.Path("."))
    p.add_argument("--pinned")
    p.add_argument("--offline", action="store_true", help="skip the API version gate")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("--verbose", action="store_true")
    a = p.parse_args(argv[1:])

    project = a.project.resolve()
    if not project.exists():
        print(f"project not found: {project}", file=sys.stderr)
        return 2

    cfg = {}
    cfgp = project / ".tgstack.json"
    if cfgp.exists():
        try:
            cfg = json.loads(cfgp.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"{cfgp}: {e}", file=sys.stderr)
            return 2
    pinned = a.pinned or cfg.get("pinned_api", DEFAULT_PINNED)
    code_dirs = [d for d in cfg.get("code", ["app", "api"]) if (project / d).exists()]
    contract = project / cfg.get("contract", "navigation.yaml")
    assets = project / cfg.get("assets", "assets/process-emoji")
    styles = [project / s for s in cfg.get("styles", ["miniapp/src"])]
    styles = [s for s in styles if s.exists()]

    gates: list[Gate] = []

    gates.append(Gate("api-version", "the pinned Bot API is still current").run(
        [str(HERE / "check_api_version.py"), "--pinned", pinned],
        cwd=project, ok_codes=(0,),
        skip_reason="--offline" if a.offline else None))

    gates.append(Gate("contract", "navigation is reachable, shallow, escapable").run(
        [str(HERE / "gen_navigation.py"), str(contract), "--check"],
        cwd=project,
        skip_reason=None if contract.exists() else f"no {contract.name}"))

    if code_dirs:
        for d in code_dirs:
            gates.append(Gate(f"lint:{d}", "formatting, callbacks, gateway, layers, secrets").run(
                [str(HERE / "tg_lint.py"), d,
                 "--rules", "formatting,callback,gateway,layers,secrets,presence"],
                cwd=project))
    else:
        gates.append(Gate("lint", "code rules").run([], cwd=project,
                                                     skip_reason="no code dirs found"))

    for s in styles or []:
        gates.append(Gate(f"miniapp:{s.name}", "theme tokens, viewport, identity").run(
            [str(HERE / "tg_lint.py"), str(s), "--rules", "theme,miniapp"], cwd=project))
    if not styles:
        gates.append(Gate("miniapp", "theme tokens, viewport, identity").run(
            [], cwd=project, skip_reason="no Mini App sources found"))

    gates.append(Gate("assets", "sticker/emoji specs").run(
        [str(HERE / "validate_sticker_assets.py"), str(assets),
         "--kind", cfg.get("sticker_kind", "custom_emoji")],
        cwd=project, skip_reason=None if assets.exists() else "no asset dir"))

    gates.append(Gate("initdata", "the initData validator still rejects tampering").run(
        [str(HERE / "validate_initdata.py"), "--selftest"], cwd=project))

    gates.append(structural(project, cfg))

    tests = project / "tests"
    gates.append(Gate("tests", "handler tests against a mocked Bot API").run(
        ["-m", "pytest", "-q", "--no-header", str(tests)], cwd=project,
        skip_reason=None if tests.exists() else "no tests/ directory"))

    failed = [g for g in gates if g.status == "fail"]
    skipped = [g for g in gates if g.status == "skip"]

    if a.format == "json":
        print(json.dumps([{"gate": g.name, "status": g.status, "why": g.why,
                           "detail": g.detail} for g in gates],
                         ensure_ascii=False, indent=1))
    else:
        width = max(len(g.name) for g in gates)
        print("tg_preflight\n" + "-" * (width + 58))
        for g in gates:
            mark = {"pass": "PASS", "fail": "FAIL", "skip": "skip"}[g.status]
            print(f"{mark:<5} {g.name:<{width}}  {g.detail}")
        print("-" * (width + 58))
        if failed:
            print(f"{len(failed)} gate(s) FAILED: {', '.join(g.name for g in failed)}")
            for g in failed:
                print(f"\n=== {g.name} ===\n{g.output.strip()[:4000]}")
            print("\nNot done. Fix these, re-run, then make the claim.")
        else:
            print(f"all gates pass ({len(skipped)} skipped: "
                  f"{', '.join(g.name for g in skipped) or '-'})")
        if a.verbose:
            for g in gates:
                if g.output and g.status == "pass":
                    print(f"\n--- {g.name} ---\n{g.output.strip()[:2000]}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
