# Bot ↔ Mini App stitching contract

A bot and its Mini App are one product. Consistency is achieved structurally — by sharing the artifacts that would otherwise drift — not by remembering to keep them in sync.

## Deployment shape

```
repo/
  navigation.yaml          # ONE contract: bot screens AND app routes
  app/                     # bot (Python)
    services/              # ← shared business logic
    repositories/
    nav/                   # generated: keyboards, routers, web_app buttons
  api/                     # Mini App backend — thin HTTP over services/
    auth.py                # initData -> session
    routes/                # one route per navigation.yaml app screen. No logic.
  miniapp/                 # frontend (Vite/React)
    src/theme.css          # generated from tokens.yaml
    src/routes/            # one route per navigation.yaml app screen
    src/copy.ts            # generated from copy.yaml
  tokens.yaml              # design tokens -> theme.css
  copy.yaml                # every user-visible string, both surfaces
```

`api/` is deliberately thin. The moment a business rule exists in `api/routes/` it exists twice, and one copy will be wrong.

## Seam 1 — Identity

One user table. `initData.user.id` **is** `message.from.id`.

```
POST /api/auth        body: { init_data }        -> { session_token, ttl, user, trace_id }
```

- Validate `initData` (see the skill), upsert the user, mint a short-lived session JWT (15–60 min), return it. Subsequent calls carry the JWT.
- The JWT claims `user_id`, `tg_chat_id`, `session_id`, `trace_id`. Nothing else — no roles baked in, because roles change while a session lives.
- Re-validate raw `initData`, not the JWT, at the moment of any money operation, with `max_age=3600`.
- On logout / token theft suspicion: session id blacklist in Redis, TTL = JWT TTL.
- **Never** accept `user_id` from a request body or query string. `tg_lint.py --rules miniapp` fails the build if a route reads a user identifier from anywhere but the verified session.

## Seam 2 — Logic

```python
# api/routes/requests.py — correct: thin
@router.post("/requests")
async def create(body: CreateRequest, ctx: Session = Depends(auth)):
    return await services.requests.create(ctx.user_id, body.category, body.address)
```

```python
# WRONG: a second implementation of the same rule
@router.post("/requests")
async def create(body, ctx):
    if body.category == "urgent" and not await repo.user_has_credit(ctx.user_id):
        raise HTTPException(402)          # this rule now exists twice and will diverge
    ...
```

The import-graph check enforces: `api/` may import `services/` and `models/`, and nothing else. Same rule as `handlers/`. Two thin adapters over one core is what makes the two surfaces agree.

## Seam 3 — Navigation

App routes live in the same `navigation.yaml`:

```yaml
  - id: price
    title: "Прайс"
    surface: miniapp
    jobs: [J5]
    entry: [home, "startapp=price"]
    app_route: /price
    escape: home
```

The generator emits, from that one row:
- the bot's `web_app` button with the right URL and `start_param`,
- the deep link `t.me/<bot>/<app>?startapp=price`,
- a frontend route stub at `/price`,
- the row in `/help`.

`startapp` payload rules: same 64-char base64url budget as `?start=`. Format `<screen>[_<id>]`. Log it as the acquisition channel — every external entry point becomes measurable for free.

## Seam 4 — Round trip

**An action completed in the app must leave a trace in the chat.** Users treat the chat as the record of what happened; work that is invisible there feels lost.

Three mechanisms, pick by payload:

| Mechanism | Payload | Flow |
|---|---|---|
`sendData(json)` | ≤4096 bytes, from a keyboard-button app only | app → bot receives `web_app_data` → bot writes and confirms in chat |
Server write + push | any size | app → your API → service → bot sends a confirmation message |
`answerWebAppQuery` | inline-mode apps | app → your API → `answerWebAppQuery` → the user posts the result into a chat |

Rules:
- The confirmation is sent **after** the write commits, and it names the object: "Заявка #81 создана". Optimistic confirmations become lies when the write fails.
- `sendData` closes the app. Do not use it for a step in the middle of a flow.
- The chat confirmation carries the same `trace_id`, so the app session and the chat message are one trace.
- If the app closes without committing, the bot says nothing. Silence is correct; a "maybe it worked" message is not.

## Seam 5 — Words and looks

`copy.yaml` is the single source for every user-visible string, keyed by full sentence, generated into `app/i18n/` and `miniapp/src/copy.ts`. `tokens.yaml` is the single source for spacing, radii, type scale and semantic colour roles, generated into `theme.css` and into the bot's formatting helpers (so a price is formatted identically in both).

The tells that these are two products, all caused by duplicated copy or tokens: a price formatted `1 200 ₽` in one place and `1200 руб.` in the other; a status called "В работе" in the bot and "Выполняется" in the app; a different accent colour on the primary button.

## Consistency checks in CI

| Check | Fails when |
|---|---|
Route coverage | a `surface: miniapp` screen has no frontend route, or a route has no contract row |
Copy coverage | a string literal appears in a component instead of a `copy` key |
Token purity | a hex colour or a raw px spacing value appears outside `tokens.yaml` |
Identity purity | a route reads a user id from anything but the session |
Layer purity | `api/` imports a repository or a vendor SDK |
Round-trip coverage | a mutating app route has no corresponding chat confirmation path |

The last one is the unusual check and the most valuable: it makes "the app must talk back to the chat" a build-time property rather than a code-review habit.

## Environments

| Concern | Rule |
|---|---|
Origin | one origin per app per environment; since 2026-07-20 Telegram blocks method calls from any other |
Bot token | one per environment; `file_id`s, sticker sets and Stars balances are all per-token |
App URL | set via BotFather per environment; a staging app pointed at a prod bot is a data-loss incident waiting |
`startapp` payloads | prefix with the environment during testing so analytics do not mix |
Session secret | separate per environment, rotated independently of the bot token |
