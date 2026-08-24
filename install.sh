#!/usr/bin/env bash
# install.sh — put the skills where the agent will find them, then prove the
# toolchain works. Idempotent: re-running overwrites the skills and nothing else.
#
#   ./install.sh
#   ./install.sh ~/.agents/skills
#   SKIP_VERIFY=1 ./install.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-$HOME/.claude/skills}"

echo "telegram-stack -> $TARGET"
mkdir -p "$TARGET"

for d in "$ROOT"/skills/*/; do
  name="$(basename "$d")"
  rm -rf "$TARGET/$name"
  cp -r "$d" "$TARGET/$name"
  echo "  installed $name"
done

# The scripts are referenced from the skills by relative path, so they travel
# with the repo rather than into the skills directory. Record where they live.
printf '%s\n' "$ROOT" > "$TARGET/telegram/STACK_ROOT"
echo "  scripts stay at $ROOT (recorded in telegram/STACK_ROOT)"

if [ "${SKIP_VERIFY:-0}" = "1" ]; then echo; echo "skipped verification"; exit 0; fi

# Pick an interpreter that actually runs. On Windows, `python3` is often the
# Microsoft Store stub, which exists on PATH and does nothing useful.
PY=""
for cand in "${PYTHON:-}" python3 python py; do
  [ -n "$cand" ] || continue
  command -v "$cand" >/dev/null 2>&1 || continue
  if ver="$("$cand" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)" \
     && [ -n "$ver" ]; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo; echo "no working python found - skipping verification"
  echo "set PYTHON=/path/to/python and re-run to verify"
  exit 0
fi
echo "  python $ver ($PY)"

echo
echo "verifying the toolchain (offline)"
export PYTHONIOENCODING=utf-8
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
failed=0
check() {
  local name="$1"; shift
  if "$PY" "$@" >/dev/null 2>&1; then echo "  PASS $name"
  else echo "  FAIL $name"; failed=$((failed + 1)); fi
}
check "initdata selftest"  "$ROOT/scripts/validate_initdata.py" --selftest
check "text builders"      "$ROOT/scripts/tg_text.py" --demo
check "contract validator" "$ROOT/scripts/gen_navigation.py" \
      "$ROOT/templates/navigation.example.yaml" --check
check "glyph generator"    "$ROOT/scripts/make_process_assets.py" --out "$TMP"
check "asset validator"    "$ROOT/scripts/validate_sticker_assets.py" "$TMP" \
      --kind custom_emoji

if [ "$failed" -gt 0 ]; then
  echo
  echo "$failed check(s) failed. 'pip install pyyaml' covers the usual cause."
  exit 1
fi
echo
echo "ready. Start with the 'telegram' skill; workflows/WORKFLOW.md is the process."
