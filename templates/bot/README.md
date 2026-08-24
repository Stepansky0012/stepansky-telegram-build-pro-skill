# Bot skeleton

aiogram 3, Bot API 10.2. Everything here exists to satisfy one of the 30
invariants; nothing is decoration.

## Run

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                              # fill BOT_TOKEN
cp ../../scripts/tg_text.py app/tg/text.py        # already vendored, re-copy to update
python -m app.bot                                 # polling (single instance only)
```

## Before you claim it works

```bash
python ../../scripts/tg_preflight.py --project .
```

## Layout

| Path | Rule it enforces |
|---|---|
`app/config.py` | secrets from env; cross-field validation at startup, not at first use |
`app/observability.py` | JSON logs, allow-list redaction, token filter that also covers tracebacks |
`app/tg/limiter.py` | token buckets; `penalize()` applies Telegram's own `retry_after` |
`app/tg/errors.py` | six named actions; unknown code -> `ALERT`, never `IGNORE` |
`app/tg/gateway.py` | the only place the Bot API is called; `edit_status` is throttled separately |
`app/tg/raw.py` | methods aiogram does not wrap yet; `api_version` is mandatory so `grep` finds them |
`app/tg/presence.py` | three-channel Presence, terminal state guaranteed by `__aexit__` |
`app/tg/text.py` | `H` (HTML) and `E` (entities); MarkdownV2 is unreachable from here |
`app/middlewares.py` | trace -> error boundary -> dedup -> user -> i18n -> log |
`app/handlers/start.py` | reference handler: state clearing, Presence, callback answered in `finally` |
`app/nav/` | GENERATED from `navigation.yaml`. Never edited by hand |
`tests/test_reference.py` | the outgoing API calls are the unit under test |

## Next

1. Edit `navigation.yaml` (derive it via `telegram-ux`, do not invent it).
2. `python ../../scripts/gen_navigation.py navigation.yaml --out app/nav --diagram nav.mmd`
3. Implement the `NotImplementedError` stubs in `app/nav/routers.py` — that is the task list.
4. Build the icon set: `python ../../scripts/build_process_pack.py --spec ../process-pack.spec.yaml --assets assets/process-emoji --env dev --out app/nav/custom_emoji.py`
