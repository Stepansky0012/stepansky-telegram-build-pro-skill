# Степанский телеграм билд про скил

*Repository name: «Степанский телеграм билд про скил». The tool, its directory and
every path inside it are `telegram-stack` — that is what `--stack-path`, `STACK_ROOT`
and the generated adapters refer to.*

An agent skill suite for building Telegram bots and Mini Apps that are **predictable** — for users, for the business, and for whoever is on call.

Ten skills, nine executable gates, a code generator, a runnable bot skeleton, a Mini App skeleton, working animated emoji assets, and an eight-stage workflow. Pinned to **Bot API 10.2 (2026-07-14)**, with a version guard that tells you the moment that stops being true.

The design bet: **anything a script can enforce should not be left to prose.** Documentation persuades; a non-zero exit code decides. So each skill teaches the judgement calls, and `tg_preflight.py` refuses the rest.

---

## Install

```bash
git clone <this> telegram-stack
cd telegram-stack

# Install the skills and verify the whole toolchain offline (5 checks)
bash install.sh                       # or: powershell -ExecutionPolicy Bypass -File install.ps1
bash install.sh ~/.agents/skills      # Codex CLI / Copilot CLI / Gemini CLI

# Other harnesses (Cursor, Windsurf, Copilot-in-IDE, Zed, Aider, CI)
python scripts/gen_adapters.py --list
```

Requires Python 3.10+ and `pyyaml`. `ffprobe` is optional (WEBM validation is best-effort without it). Nothing else, no network, no token.

---

## What is in here

### Skills — `skills/`

| Skill | Owns |
|---|---|
**`telegram`** | Router. Step-0 version guard, the 30 invariants, the escalation ladder to the right documentation page. **Start here.** |
`telegram-ux` | JSA derivation (business jobs → surface → affordance), the Navigation Contract, callback protocol, the nine response modes |
`telegram-text` | Formatting that cannot break: HTML/entity builders, UTF-16 offsets, safe splitting. MarkdownV2 is banned |
`telegram-presence` | The Presence Protocol: reaction + status line + chat action, with a guaranteed terminal state |
`telegram-stickers` | Sticker and custom emoji formats, the idempotent pack pipeline, the Process Pack as an icon system |
`telegram-rich` | Rich Messages (10.1+), streaming without destroying formatting, ephemeral group replies (10.2+) |
`telegram-backend` | Layers, the single gateway, rate limits, error taxonomy, idempotency, structured logs, webhooks |
`telegram-miniapp` | initData HMAC, theme tokens, safe area, gesture conflicts, and total bot↔app stitching |
`telegram-money` | Stars/XTR, subscriptions, refunds, the real ~32% mobile fee, paywall placement |
`telegram-test` | Mocked-Bot-API handler tests, property tests for formatting, CI gates |

### Gates — `scripts/`

| Script | Verified working |
|---|---|
`tg_preflight.py` | orchestrates every gate below; the one command before claiming done |
`check_api_version.py` | fetches the changelog, compares to your pin — **confirmed live API is 10.2** |
`tg_lint.py` | 8 rule groups over Python/TS/CSS — **12/12 planted violations caught** |
`gen_navigation.py` | validates + compiles `navigation.yaml` — **13/13 planted contract errors caught; generated code compiles** |
`tg_text.py` | HTML/entity builders, UTF-16 offsets, `split_safe` — **demo + 51 assertions pass** |
`validate_initdata.py` | HMAC validation library + CLI — **7/7 selftest cases pass** |
`validate_sticker_assets.py` | offline format validation — **rejects wrong dimensions, ungzipped TGS, 30 FPS TGS, over-long animations** |
`make_process_assets.py` | generates 9 valid TGS glyphs — **all 9 pass validation, 339–379 bytes each** |
`build_process_pack.py` | idempotent set builder — **offline plan verified; live path needs a token** |
`gen_adapters.py` | repackages the skills for Cursor / Windsurf / Continue / Cline / Copilot / Codex / Zed / Aider + CI — **48 files generated, every YAML and frontmatter block parses** |

### Templates — `templates/`

| Path | Contents |
|---|---|
`bot/` | aiogram 3 skeleton: gateway, limiter, error taxonomy, Presence, middleware chain, config, logging, raw-method escape hatch, reference handler, reference tests. **Passes its own lint; 51 behavioural assertions verified** |
`miniapp/` | Vite/React skeleton: theme tokens, safe-area boot order, gesture policy, haptic policy, session auth client, FastAPI auth route. **Passes its own lint** |
`navigation.example.yaml` | a worked contract covering all eight surfaces and every JSA branch |
`process-pack.spec.yaml` | the nine-glyph icon system |
`assets/process-emoji/` | nine ready-to-upload TGS files |

### Workflow — `workflows/WORKFLOW.md`

Version guard → Brief → **Navigation Contract** → ADR → Skeleton → Implement → Verify → Ship, each with a checkable exit gate.

---

## Other harnesses

The knowledge is portable; only discovery is not. Nothing in `skills/`, `workflows/` or
`templates/` mentions Claude, Anthropic, MCP or any vendor, and the scripts are stdlib +
`pyyaml`.

| Harness | How |
|---|---|
Claude Code | `install.sh` → `~/.claude/skills` |
Codex CLI, Copilot CLI, Gemini CLI | `install.sh ~/.agents/skills` — same SKILL.md format, no adapter |
Cursor, Windsurf, Continue, Cline | `gen_adapters.py` → per-skill rule files; the harness still picks a skill by description, lazily |
Copilot-in-IDE, Codex `AGENTS.md`, Zed, Aider | `gen_adapters.py` → a **compressed router**: 1 521 words (6% of the full 24 000) carrying the routing table, the nine most-violated laws, the escalation ladder and the limits, with paths to everything else |
No agent at all | the gates alone. `tg_preflight.py` in CI catches 13 classes of violation in any Python Telegram project |

```bash
python scripts/gen_adapters.py --list
python scripts/gen_adapters.py --out . --stack-path vendor/telegram-stack
python scripts/gen_adapters.py --out . --targets cursor,ci
```

Concatenating 24 000 words into a single instructions file is the wrong answer for
single-file harnesses — it spends the context the work needs. Hence the router, and
hence the CI target: a harness that cannot hold the rules can still be held to them
by an exit code.

## The five ideas worth stealing even if you use none of the code

**1. The Navigation Contract.** Menus are not a taste question. Business jobs get classified on five axes, a matrix picks the surface, and the result is a YAML file that a validator checks for reachability, depth, escapes, confirmations and — the one that closes the loop — **orphan jobs: a requirement with no interface.** Keyboards, routers, `/help` and the diagram are generated from it. A button that cannot be traced to a contract row does not get merged.

**2. The Presence Protocol.** "typing…" says *alive*; it does not say *which phase*. Nine canonical states, each emitting three signals at once: a reaction on the user's message (👀 → 🔎 → ✍️ → ✅), a status line edited in place with an animated custom emoji, and the right chat action. A context manager guarantees a terminal state, because the real bug is the `return` path that leaves "Работаю…" in someone's chat forever.

**3. Formatting is a type, not a discipline.** MarkdownV2 has 18 reserved characters and three escaping contexts, so the failure is always data-dependent and always ships. The fix is not care — it is `H` (auto-escaping HTML `str` subclass, rejects `%`/`.format()`, rejects unsafe URLs) and `E` (entity builder with correct UTF-16 offsets and a custom-emoji validity check). A linter fails the build on the alternatives.

**4. One gateway, one taxonomy, and `ALERT` as the default.** Every outgoing call passes limiter → attempt → taxonomy → retry/degrade/raise, with one log line and one metric. Every API error maps to exactly one of six named actions, and an **unmapped error alerts** rather than being silently ignored — that alert is how you learn the API moved before your users do.

**5. Total stitching.** Bot and Mini App share `services/`, `navigation.yaml`, `copy.yaml`, `tokens.yaml` and one user identity, so they cannot disagree. Both are thin adapters over one core. And an action finished in the app must leave a message in the chat — users treat the chat as the record of what happened.

---

## Provenance and honesty

Built from a survey of what exists (BotForge, sickn33's skills, davila7's templates, the mcpmarket family, the awesome-lists, aiogram/grammY/PTB docs) plus the Bot API changelog 9.0→10.2 read directly. The gap it fills: **no existing Telegram skill covers Bot API 9.5–10.2 at all** — no Rich Messages, no ephemeral messages, no managed bots, no guest mode, no `icon_custom_emoji_id`, and no Mini App origin lock (enforced 2026-07-20). Nor does any of them treat stickers as a design surface or ship tests.

What is verified: everything in the "Gates" table above was executed in this environment with the results shown. `check_api_version.py` reached `core.telegram.org` and confirmed 10.2 live.

What is **not** verified: `pytest` is not installed here, so `templates/bot/tests/test_reference.py` has never been run by a test runner — its 51 assertions were executed by a standalone driver instead, and all passed. `build_process_pack.py`'s live API path needs a real `BOT_TOKEN`; only its offline plan is exercised. Fields marked ✱ in `telegram-rich` and `app/tg/raw.py` are Bot API 10.x names taken from the changelog and issue trackers, not from a rendered method reference — the skills instruct you to fetch and confirm them before first use, and that instruction is deliberate, not a placeholder.

`writing-skills` prescribes baseline testing with subagents before authoring; that was unavailable in this session, so discipline is enforced mechanically (linters, generators, preflight) rather than only by prose. That is the stronger form for everything mechanically checkable — a rule `python` decides is a rule an agent cannot argue with — but it does mean the *prose* sections have not been pressure-tested against a real agent. Do that when you can: run a task with the skills loaded, watch where the agent rationalizes, and add the counter to the relevant rationalization table.

MIT. Break it, extend it, and keep the pin honest.
