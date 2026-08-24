---
name: telegram-miniapp
description: "Use when building or reviewing a Telegram Mini App / TWA / WebApp — initData validation, themeParams, safe area, fullscreen, MainButton and BackButton, haptics, CloudStorage, viewport height, swipe gesture conflicts, TON Connect, startapp deep links, sendData — or when the app flickers, closes on horizontal swipe, ignores the user's theme, breaks on iOS, or a user can impersonate another."
---

# Mini Apps — native feel, and total stitching with the bot

Two things make a Mini App good, and neither is visual polish:

1. **It feels like part of Telegram.** Native feel is the *absence* of things — no flicker, no gesture conflict, no theme mismatch, no scrollbar. Users cannot name it; they only notice when it is missing.
2. **It is the same product as the bot.** Same auth, same domain services, same navigation contract, same copy. A Mini App that disagrees with the bot about anything is two products with one icon.

## Security first — non-negotiable

**`initData` is attacker-controlled.** Anyone can `curl` your endpoint with an arbitrary `user.id`. Validate on the server, on **every** request, before any business logic.

```python
import hmac, hashlib, time
from urllib.parse import parse_qsl

def validate_init_data(init_data: str, bot_token: str, *, max_age: int = 86400) -> dict:
    pairs = parse_qsl(init_data, keep_blank_values=True)
    data = dict(pairs)
    received = data.pop("hash", "")
    data.pop("signature", None)                 # third-party Ed25519 field, not in the DCS
    if not received:
        raise AuthError("no hash")

    dcs = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise AuthError("bad hash")

    auth_date = int(data.get("auth_date", "0"))
    if max_age and time.time() - auth_date > max_age:
        raise AuthError("stale auth_date")
    return data
```

The four mistakes, in order of frequency:

| Mistake | Result |
|---|---|
Key and message swapped in the secret derivation | hash never matches; people then "fix" it by skipping validation |
`hash` left inside the data-check-string | never matches |
Re-encoding values before building the DCS | never matches — use the **raw** values as received |
No `auth_date` check | valid `initData` replays forever |

Then: exchange validated `initData` for your **own short-lived session token** once, and authenticate subsequent requests with that. Re-validating a 24-hour-old `initData` on every call is both slower and a wider replay window. Use `max_age=3600` for anything touching money, and re-validate at that moment regardless of session state.

`initData` is also available signed with Ed25519 (`signature`) so a third-party backend can verify it **without** your bot token. Verify the current field list and public key handling at `bots/webapps#validating-data-received-via-the-mini-app` before implementing.

**Origin lock:** since **2026-07-20** Telegram blocks Mini App method calls from any origin other than the app's own domain (Bot API 10.1 hardening). Symptom is silent no-ops, not errors. One origin per app, no embedding your app inside someone else's page, no `file://` testing of method calls.

## Native feel — the checklist that actually matters

Full layout rules, CSS, and the gesture map: `references/layout.md`. The five that break apps:

| Rule | Failure if broken |
|---|---|
Colors come **only** from `themeParams` CSS variables | app looks foreign in dark theme, or unreadable in a theme that ships next year |
Respect `safeAreaInset` **and** `contentSafeAreaInset` in fullscreen | header/notch overlaps your top bar |
No horizontally-swipeable element within ~24 px of a screen edge; `disableVerticalSwipes()` when you own the scroll | **the app closes instead of scrolling your carousel** — the single most common Mini App bug |
`viewportStableHeight`, not `100vh` | layout jumps when the keyboard opens |
Skeletons, never spinners; first paint before data | flicker, which reads as "broken web page inside my messenger" |

Haptics (`impactOccurred`, `notificationOccurred`, `selectionChanged`) on **confirmations and state changes only**. Not on scroll, not per keystroke — frequent haptics measurably drain battery and stop meaning anything.

Android performance class: reduce or drop animations and blurs on low-class devices. Read it once at boot and set a `data-perf="low"` attribute; branch in CSS, not in JS.

## MainButton and BackButton are the OS, not your buttons

- The primary action of a screen goes on **MainButton**, at the bottom, always — not a custom in-page button. Users look there.
- `MainButton.showProgress()` while working; never leave it enabled during a request.
- `BackButton` is shown when and only when there is somewhere to go back to, and it must do the same thing as the system gesture. Wire both to one router action.
- Closing the app is `close()` after the work is committed, not before — closing optimistically loses the result if the request fails.

## Total stitching with the bot

This is the part most projects get wrong, and the fix is structural, not disciplinary. Details and the contract: `references/stitching.md`. The five seams:

| Seam | Rule |
|---|---|
**Identity** | one user table. `initData.user.id` and `message.from.id` are the same key. Never two accounts for one person. |
**Logic** | the Mini App backend imports the same `services/` as the bot. Zero business logic in the app's own API layer. If a rule differs between bot and app, it is a bug by construction. |
**Navigation** | Mini App routes are declared in the same `navigation.yaml`. `startapp=<screen>_<id>` maps to a route; the bot's `web_app` buttons are generated from it. |
**Round trip** | app finishes → `sendData()` (small results) or a server write + `answerWebAppQuery` (results the bot must post) → the bot confirms **in the chat**. An action that leaves no trace in the chat did not happen, as far as the user is concerned. |
**Look and words** | one token file, one copy file. A price formatted one way in the bot and another in the app is the tell that these are two products. |

Deep-link entry: `t.me/<bot>/<app>?startapp=<payload>` opens the app directly with `start_param`. Use it for every external entry point — email, QR, ads — and log the payload as the acquisition channel. The payload obeys the same 64-char / base64url limit as `?start=`.

## CloudStorage

Per-bot, per-user key/value in Telegram's cloud. **1024 keys per user.** Good for: UI preferences, draft state, "seen this tip", last-used filter. Not for: business data (it is not queryable server-side and you cannot migrate it), anything you need for analytics, anything a support agent must read.

Wrap it: namespace keys (`v1:ui:theme`), always `try/catch` (it can be empty, and it can throw in preview/thumbnail contexts), and always render correctly with no stored value. A first-run path that depends on CloudStorage having data is broken on every fresh install.

## Stack

Vite + React + TypeScript + `@telegram-apps/sdk-react` is the default; the official `Telegram-Mini-Apps/reactjs-template` is a reasonable starting point. What actually matters more than framework choice:

- **Bundle budget: ≤200 KB gzipped for first paint.** Mini Apps open on mobile networks inside another app. Code-split everything below the first screen.
- No web fonts on the critical path — the system font is what Telegram itself uses, so it is also the most native choice.
- No router that requires the History API to be pristine; Telegram's WebView is not a browser tab.
- Test on a real iOS device before shipping. iOS is where the WebView differences live.

## Common mistakes

| Mistake | Symptom | Fix |
|---|---|---|
Trusting `initData` client-side | impersonation | server HMAC every request |
Hardcoded `#ffffff` | broken dark theme | `themeParams` tokens |
`100vh` | jumps with the keyboard | `viewportStableHeight` |
Carousel at the screen edge | app closes mid-swipe | inset it, or `disableVerticalSwipes` |
Custom submit button at the bottom | users look for MainButton and do not find it | MainButton |
Haptic on every scroll tick | battery drain, meaningless feedback | confirmations only |
Business rule duplicated in the app's API | bot and app disagree, silently | shared `services/` |
App finishes with no chat message | user is unsure it worked | `sendData`/`answerWebAppQuery` + bot confirmation |
Separate user record for app sessions | split history, broken payments | one identity |
Mini App methods called from another origin | silent no-ops since 2026-07-20 | one origin |

## Red flags

- A route handler that reads `user_id` from the request body.
- A hex colour in a component.
- `100vh` anywhere.
- Business logic in `api/` instead of `services/`.
- An app flow that ends without the bot saying anything in the chat.
- A first-run path that assumes CloudStorage has data.
