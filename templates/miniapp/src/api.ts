/**
 * API client. One rule: identity comes from the server-verified session, never
 * from anything this file sends. The client may lie about who it is; the server
 * decides.
 *
 * Flow: initData -> POST /api/auth (server HMAC) -> short-lived session token
 *       -> every later call carries the token, not initData.
 */
import { initData, startParam } from './telegram';

export interface Session {
  token: string;
  ttl: number;
  user: { id: number; first_name?: string; username?: string };
  trace_id: string;
  expires_at: number;
}

let session: Session | null = null;
let inflight: Promise<Session> | null = null;

export class ApiError extends Error {
  constructor(readonly status: number, message: string, readonly traceId?: string) {
    super(message);
  }
}

async function authenticate(): Promise<Session> {
  const raw = initData();
  if (!raw) throw new ApiError(401, 'no initData — opened outside Telegram');
  const r = await fetch('/api/auth', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ init_data: raw, start_param: startParam() }),
  });
  if (!r.ok) throw new ApiError(r.status, 'auth failed');
  const s = (await r.json()) as Session;
  // Refresh a little early so a request never races the expiry.
  s.expires_at = Date.now() + Math.max(0, s.ttl - 60) * 1000;
  session = s;
  return s;
}

async function ensure(): Promise<Session> {
  if (session && Date.now() < session.expires_at) return session;
  inflight ??= authenticate().finally(() => {
    inflight = null;
  });
  return inflight;
}

export async function api<T>(
  path: string,
  init: RequestInit & { retryOn401?: boolean } = {},
): Promise<T> {
  const s = await ensure();
  const r = await fetch(`/api${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${s.token}`,
      // Same trace id as the chat session: one trace across bot and app.
      'X-Trace-Id': s.trace_id,
      ...(init.headers ?? {}),
    },
  });
  if (r.status === 401 && init.retryOn401 !== false) {
    session = null;
    return api<T>(path, { ...init, retryOn401: false });
  }
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new ApiError(r.status, body.slice(0, 300), r.headers.get('X-Trace-Id') ?? undefined);
  }
  return (await r.json()) as T;
}

/** Money routes re-validate raw initData with a 1h window, regardless of session. */
export async function apiMoney<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, {
    method: 'POST',
    body: JSON.stringify({ ...(body as object), init_data: initData() }),
  });
}

export const currentUser = (): Session['user'] | null => session?.user ?? null;
