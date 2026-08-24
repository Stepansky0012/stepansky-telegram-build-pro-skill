# Layout, theme, gestures — the native-feel reference

## Theme tokens

Telegram exposes `themeParams` and mirrors them as CSS variables. Define **your** tokens once in terms of Telegram's, with fallbacks, and never reference a Telegram variable outside this block. One file, one place to audit.

```css
:root {
  /* Telegram-provided, with fallbacks for browser dev */
  --bg:            var(--tg-theme-bg-color, #ffffff);
  --bg-secondary:  var(--tg-theme-secondary-bg-color, #f1f1f1);
  --text:          var(--tg-theme-text-color, #000000);
  --hint:          var(--tg-theme-hint-color, #999999);
  --link:          var(--tg-theme-link-color, #2481cc);
  --accent:        var(--tg-theme-button-color, #2481cc);
  --accent-text:   var(--tg-theme-button-text-color, #ffffff);
  --destructive:   var(--tg-theme-destructive-text-color, #d14e4e);
  --header:        var(--tg-theme-header-bg-color, var(--bg));
  --section-bg:    var(--tg-theme-section-bg-color, var(--bg));
  --separator:     var(--tg-theme-section-separator-color, rgba(0,0,0,.08));

  /* Safe areas — 0 outside fullscreen, real values inside it */
  --safe-top:      var(--tg-safe-area-inset-top, 0px);
  --safe-bottom:   var(--tg-safe-area-inset-bottom, 0px);
  --content-top:   var(--tg-content-safe-area-inset-top, 0px);
  --content-bottom:var(--tg-content-safe-area-inset-bottom, 0px);

  /* Layout */
  --vh-stable:     var(--tg-viewport-stable-height, 100dvh);
  --edge-guard:    24px;   /* keep swipeable content out of the back-gesture zone */
}

html, body { background: var(--bg); color: var(--text); }

body {
  min-height: var(--vh-stable);
  padding-top:    calc(var(--safe-top) + var(--content-top));
  padding-bottom: calc(var(--safe-bottom) + var(--content-bottom));
  overscroll-behavior: none;      /* kills rubber-band that reads as a broken page */
}

[data-perf="low"] * {
  animation: none !important;
  transition: none !important;
  backdrop-filter: none !important;
}
```

Rules:
- **No hex outside this block.** `tg_lint.py --rules theme` fails the build on a hex literal in a component.
- Two safe-area families, and they are different: `safe-area-*` is the device (notch, home bar), `content-safe-area-*` is Telegram's own chrome in fullscreen. You need both, summed.
- `--vh-stable` over `100vh`: `viewportStableHeight` excludes the keyboard, so layout does not jump.
- `color-scheme: light dark` on `:root` so native form controls follow the theme too.
- Re-read tokens on `themeChanged` — the user can switch theme while your app is open.

## Boot sequence

Order matters; getting it wrong is the flicker.

```ts
import { init, viewport, themeParams, backButton, mainButton, miniApp } from '@telegram-apps/sdk';

init();                                  // 1. bind to the Telegram runtime
themeParams.mountSync();                 // 2. tokens BEFORE first paint
viewport.mount().then(() => {
  viewport.bindCssVars();                // 3. safe area + stable height as CSS vars
  viewport.expand();                     // 4. full height
});
themeParams.bindCssVars();
backButton.mount();
mainButton.mount();
miniApp.ready();                         // 5. LAST — tells Telegram to reveal the app
```

`ready()` last. It is the signal that you are painted; calling it first is what produces a visible white flash. Between `init()` and `ready()` render a skeleton in theme colours — never a spinner, never white.

## Gestures — the map

| Gesture | Telegram owns it | Consequence for you |
|---|---|---|
Horizontal swipe from the screen edge | back / close | **any horizontal scroller within `--edge-guard` of an edge will close your app instead of scrolling** |
Vertical swipe down from the top | minimize / close the app | call `disableVerticalSwipes()` when you own vertical scrolling; re-enable when you do not |
Swipe on the header | drag the sheet | never put controls in the header area |
Long press | text selection | `user-select: none` on non-text UI, or long-press feels broken |

Fixes for the horizontal case, in order of preference:
1. Do not use a horizontal scroller. A vertical list is almost always better on a phone inside a messenger.
2. Inset it: `margin-inline: var(--edge-guard)` so the touch never starts in the system zone.
3. If it must be full-bleed, add explicit prev/next controls so the gesture is never the only way to navigate.

Kanban boards, image sliders and product carousels are the recurring offenders. Test every one of them by swiping starting exactly at the screen edge.

## MainButton / BackButton contract

```ts
mainButton.setParams({ text: 'Оплатить', isVisible: true, isEnabled: true });
mainButton.onClick(async () => {
  mainButton.setParams({ isEnabled: false, isLoaderVisible: true });
  try { await submit(); }
  finally { mainButton.setParams({ isEnabled: true, isLoaderVisible: false }); }
});
```

- One MainButton per screen, carrying that screen's single primary action. If a screen has two equal primary actions, it is two screens.
- Colours from `themeParams` by default. Override only for destructive actions, using `--destructive`.
- Disable + loader during the request. An enabled button during an in-flight request produces duplicate submissions.
- `BackButton` visibility is derived from router depth, and its handler is the **same function** as the system gesture handler. Two code paths drift.

## Performance budget

| Budget | Value | Why |
|---|---|---|
First paint | ≤1.5 s on 4G | opened from a chat, on mobile, mid-conversation |
JS, gzipped, first route | ≤200 KB | everything else is code-split |
Web fonts on critical path | zero | the system font is the native font |
Images | WEBP/AVIF, sized to the actual slot, `loading="lazy"` below the fold | |
Long lists | virtualized past ~50 rows | |
Animations | ≤200 ms, and none at `data-perf="low"` | |

Read the Android performance class at boot, set `data-perf`, and branch in CSS. Branching in JS means you shipped the animation code anyway.

## Accessibility and input

- Tap targets ≥44×44 px. Thumb, moving vehicle, one hand.
- `inputmode` and `autocomplete` on every input — the mobile keyboard is most of the UX of a form.
- `enterkeyhint="done"` so the keyboard's action key does the right thing.
- Never a `<select>` with more than ~7 options; use a full-screen list route.
- Focus the first field on mount only when the keyboard is the point of the screen. Otherwise the keyboard covers your content on open.
- Respect `prefers-reduced-motion` in addition to the performance class.

## Fullscreen mode

Fullscreen (Bot API 9.0+) gives you the whole screen in both orientations, plus device motion. Consequences you must handle:
- `contentSafeAreaInset*` becomes non-zero — Telegram's controls now overlap your layout unless you pad.
- Lock orientation deliberately if a rotation would break the layout; do not leave it to chance.
- The close affordance may be over your content. Keep the top-right area free of controls.

Do not enter fullscreen by default. It is for games, media and maps; for a form or a list it removes the user's sense of where they are.
