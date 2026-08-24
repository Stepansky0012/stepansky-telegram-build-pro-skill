/**
 * Telegram runtime: boot order, safe wrappers, and the gesture/haptic policy.
 *
 * Boot order matters — ready() LAST, after the first paint, or the user sees a
 * white flash. Between init() and ready() render a skeleton in theme colours.
 *
 * Origin lock: since 2026-07-20 Telegram blocks Mini App method calls from any
 * origin other than the app's own domain. Failures are silent no-ops, so every
 * wrapper here degrades instead of throwing.
 *
 * Spec: skills/telegram-miniapp/SKILL.md
 */

type PerfClass = 'low' | 'medium' | 'high';

interface TgWebApp {
  initData: string;
  colorScheme: 'light' | 'dark';
  themeParams: Record<string, string>;
  viewportStableHeight: number;
  platform: string;
  version: string;
  ready(): void;
  expand(): void;
  close(): void;
  sendData(data: string): void;
  disableVerticalSwipes?(): void;
  enableVerticalSwipes?(): void;
  onEvent(e: string, cb: () => void): void;
  MainButton: {
    setParams(p: Record<string, unknown>): void;
    onClick(cb: () => void): void;
    showProgress(leave?: boolean): void;
    hideProgress(): void;
  };
  BackButton: { show(): void; hide(): void; onClick(cb: () => void): void };
  HapticFeedback: {
    impactOccurred(style: 'light' | 'medium' | 'heavy'): void;
    notificationOccurred(type: 'error' | 'success' | 'warning'): void;
    selectionChanged(): void;
  };
  CloudStorage: {
    setItem(k: string, v: string, cb?: (e: unknown, ok: boolean) => void): void;
    getItem(k: string, cb: (e: unknown, v: string) => void): void;
    removeItem(k: string, cb?: (e: unknown, ok: boolean) => void): void;
  };
}

const tg = (): TgWebApp | undefined =>
  (window as unknown as { Telegram?: { WebApp: TgWebApp } }).Telegram?.WebApp;

/** Every call is best-effort: a blocked method must not break the app. */
function safe<T>(fn: () => T, fallback?: T): T | undefined {
  try {
    return fn();
  } catch {
    return fallback;
  }
}

// --------------------------------------------------------------------------- //
// boot
// --------------------------------------------------------------------------- //

export function boot(opts: { ownsScroll?: boolean } = {}): void {
  const app = tg();
  if (!app) return;                              // browser dev: CSS fallbacks apply

  applyTheme();
  applyViewport();
  safe(() => app.expand());

  // If we own vertical scrolling, take the down-swipe so it does not minimize.
  if (opts.ownsScroll) safe(() => app.disableVerticalSwipes?.());

  safe(() => app.onEvent('themeChanged', applyTheme));
  safe(() => app.onEvent('viewportChanged', applyViewport));
  safe(() => app.onEvent('safeAreaChanged', applyViewport));

  document.documentElement.dataset.perf = perfClass();

  // LAST: reveal the app only once we have painted.
  requestAnimationFrame(() => safe(() => app.ready()));
}

function applyTheme(): void {
  const app = tg();
  if (!app) return;
  const root = document.documentElement;
  for (const [k, v] of Object.entries(app.themeParams ?? {})) {
    root.style.setProperty(`--tg-theme-${k.replace(/_/g, '-')}`, v);
  }
  root.dataset.scheme = app.colorScheme;
}

function applyViewport(): void {
  const app = tg();
  if (!app) return;
  document.documentElement.style.setProperty(
    '--tg-viewport-stable-height', `${app.viewportStableHeight}px`,
  );
}

function perfClass(): PerfClass {
  const nav = navigator as unknown as { deviceMemory?: number; hardwareConcurrency?: number };
  const mem = nav.deviceMemory ?? 4;
  const cores = nav.hardwareConcurrency ?? 4;
  if (mem <= 2 || cores <= 2) return 'low';
  return mem >= 6 && cores >= 6 ? 'high' : 'medium';
}

// --------------------------------------------------------------------------- //
// MainButton — the primary action of a screen lives here, never in the page
// --------------------------------------------------------------------------- //

export function mainButton(
  text: string,
  onClick: () => Promise<void> | void,
  opts: { destructive?: boolean } = {},
): () => void {
  const app = tg();
  if (!app) return () => undefined;
  app.MainButton.setParams({
    text,
    is_visible: true,
    is_active: true,
    ...(opts.destructive
      ? { color: getComputedStyle(document.documentElement).getPropertyValue('--destructive') }
      : {}),
  });
  const handler = async () => {
    app.MainButton.setParams({ is_active: false });
    safe(() => app.MainButton.showProgress(false));
    try {
      await onClick();                          // disabled during the request:
    } finally {                                 // an enabled button double-submits
      safe(() => app.MainButton.hideProgress());
      app.MainButton.setParams({ is_active: true });
    }
  };
  app.MainButton.onClick(handler);
  return () => app.MainButton.setParams({ is_visible: false });
}

/** BackButton and the system gesture must call the SAME function. */
export function backButton(onBack: (() => void) | null): void {
  const app = tg();
  if (!app) return;
  if (!onBack) {
    safe(() => app.BackButton.hide());
    return;
  }
  app.BackButton.onClick(onBack);
  safe(() => app.BackButton.show());
}

// --------------------------------------------------------------------------- //
// haptics — confirmations and state changes ONLY (battery, and meaning)
// --------------------------------------------------------------------------- //

export const haptic = {
  confirm: () => safe(() => tg()?.HapticFeedback.notificationOccurred('success')),
  reject: () => safe(() => tg()?.HapticFeedback.notificationOccurred('error')),
  warn: () => safe(() => tg()?.HapticFeedback.notificationOccurred('warning')),
  tap: () => safe(() => tg()?.HapticFeedback.impactOccurred('light')),
  // deliberately absent: anything for scroll or per-keystroke feedback
};

// --------------------------------------------------------------------------- //
// CloudStorage — per-viewer conveniences only. 1024 keys per user.
// --------------------------------------------------------------------------- //

const NS = 'v1';

export const cloud = {
  get(key: string): Promise<string | null> {
    const app = tg();
    if (!app) return Promise.resolve(null);
    return new Promise((resolve) => {
      try {
        app.CloudStorage.getItem(`${NS}:${key}`, (err, value) =>
          resolve(err ? null : value || null));
      } catch {
        resolve(null);                          // must render correctly with no value
      }
    });
  },
  set(key: string, value: string): Promise<boolean> {
    const app = tg();
    if (!app) return Promise.resolve(false);
    return new Promise((resolve) => {
      try {
        app.CloudStorage.setItem(`${NS}:${key}`, value, (err, ok) => resolve(!err && ok));
      } catch {
        resolve(false);
      }
    });
  },
};

export const initData = (): string => tg()?.initData ?? '';
export const startParam = (): string | null =>
  new URLSearchParams(location.search).get('tgWebAppStartParam');
export const closeApp = (): void => safe(() => tg()?.close());

/** Small results back to the bot. Closes the app — never use it mid-flow. */
export const sendToBot = (payload: unknown): void =>
  safe(() => tg()?.sendData(JSON.stringify(payload))) as void;
