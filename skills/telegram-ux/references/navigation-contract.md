# The Navigation Contract

The single source of truth for a bot's interface. Lives at the repo root as `navigation.yaml`. Everything user-facing — keyboards, routers, `/help`, the FSM graph, the diagram in the README — is **generated** from it. Hand-editing generated files is a merge conflict waiting to happen and a spec drift guaranteed.

## Why a contract and not just code

Three things become checkable the moment navigation is data:

1. **Reachability.** A screen no path leads to is a bug you cannot see in code review.
2. **Depth.** "Every job in ≤2 taps" is a graph property, not a feeling.
3. **Traceability.** Each screen names the business jobs it serves. A job with no screen, or a screen with no job, is caught before implementation.

## Schema

```yaml
version: v1                      # callback protocol version; bump = old buttons keep working
bot: repair-bot
default_response_mode: plain     # plain | reply | edit | ephemeral

jobs:                            # from JSA Stage 1 — verb + object + outcome
  J1: submit a repair request
  J2: check status of my request
  J7: cancel a request

screens:
  - id: home                     # snake_case, unique, stable forever
    title: "Главное меню"
    surface: reply_keyboard      # see surfaces below
    jobs: []                     # a hub serves no job directly
    entry: ["/start", "/home"]   # commands, deep links (start=...), or parent screen ids
    escape: none                 # none allowed only on roots
    actions:
      - {label: "Новая заявка",  to: req_new}
      - {label: "Мои заявки",    to: req_list}
      - {label: "Прайс",         to: price, surface_hint: miniapp}
      - {label: "Помощь",        to: help}

  - id: req_new
    title: "Новая заявка"
    surface: fsm                 # multi-step wizard
    jobs: [J1]
    entry: [home, "/newrequest"]
    escape: home
    state: NewRequest            # FSM state group name
    steps:                       # one field per step, in order
      - {id: category, prompt: "Что случилось?", input: one_of_few,
         options_from: categories}
      - {id: address,  prompt: "Адрес?",         input: text, validate: min_len:5}
      - {id: photo,    prompt: "Фото, если есть", input: media, optional: true}
    on_complete: {to: req_created, response_mode: edit}

  - id: req_list
    title: "Мои заявки"
    surface: inline_paginated
    jobs: [J2]
    entry: [home, "/myrequests"]
    escape: home
    page_size: 5
    item_action: {to: req_card, arg: request_id}

  - id: req_card
    title: "Заявка #{request_id}"
    surface: inline
    jobs: [J2, J7]
    entry: [req_list, "start=req_{request_id}"]
    escape: back
    actions:
      - {label: "Отменить", to: req_cancel_confirm, destructive: true}
      - {label: "Написать мастеру", to: req_chat}

  - id: req_cancel_confirm
    title: "Отменить заявку #{request_id}?"
    surface: inline
    jobs: [J7]
    entry: [req_card]
    escape: back
    confirm: true                # REQUIRED for any screen reached by destructive: true
    confirm_object: "заявку #{request_id}"
    actions:
      - {label: "Да, отменить", to: req_cancelled, terminal: true}
```

## Surfaces

| `surface` | Generates | Notes |
|---|---|---|
`command` | command registration only | for `constant` + `none` jobs |
`reply_keyboard` | `ReplyKeyboardMarkup`, persistent | ≤4 buttons; only for the top jobs |
`inline` | `InlineKeyboardMarkup` | ≤5 actions + escape |
`inline_paginated` | keyboard + prev/next + optional search | `page_size` required |
`fsm` | state group, one handler per step, back at every step | ≤5 steps or the generator warns |
`miniapp` | `web_app` button + route mapping | needs `app_route` |
`inline_mode` | inline query handler stub | for `cross-chat` jobs |
`checklist` | `sendChecklist` payload builder | API 9.1+ |

## Validation — what the generator refuses

`gen_navigation.py` exits non-zero on:

| Rule | Message |
|---|---|
Duplicate `id` | `duplicate screen id` |
`to:` pointing nowhere | `dangling reference` |
Screen with no inbound path and no command/deep-link entry | `unreachable screen` |
Depth from nearest root > 2 | `depth 3 — pick a different surface (JSA Stage 3)` |
More than 5 non-escape actions | `too many actions` |
Missing `escape` on a non-root | `no escape` |
`destructive: true` whose target lacks `confirm: true` | `destructive action without confirmation` |
`confirm: true` without `confirm_object` | `confirmation must name the object` |
Packed `callback_data` over 64 bytes at max arg length | `callback overflow` |
Two screens claiming the same callback namespace | `namespace collision` |
A `jobs:` id absent from the `jobs:` map | `unknown job` |
A job in the map claimed by no screen | `orphan job` — the requirement has no interface |

The last two are the ones that make the contract worth having: they close the loop back to the business requirements.

## Generated artifacts

```
app/nav/__init__.py       # generated marker, do not edit
app/nav/callbacks.py      # CallbackProtocol + one factory per namespace
app/nav/keyboards.py      # one builder per screen, escape wired in
app/nav/routers.py        # router + filter stubs, raising NotImplementedError
app/nav/help.py           # /help text built from titles + jobs
nav.mmd                   # Mermaid graph for the README / PR review
```

Handlers import from `app/nav`; they never construct a keyboard inline. The `NotImplementedError` stubs are the implementation checklist.

## Change protocol

- **Adding a screen or action** — edit YAML, regenerate, implement the new stub.
- **Renaming a label** — YAML only. No code change.
- **Changing a namespace or removing an action** — bump `version:` to `v2`. Old messages in users' history still carry `v1:` data; keep a `v1` fallback router for one release that answers "this menu is out of date" and re-sends the current screen. Never let an old button produce a silent no-op.
- **Removing a screen** — remove it, regenerate, and check the generator's `orphan job` output. If a job lost its last screen, that is a product decision, not a cleanup.
