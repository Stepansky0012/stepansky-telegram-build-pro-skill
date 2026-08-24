/**
 * Entry point. Demonstrates the five seams that keep a Mini App and its bot
 * behaving like one product:
 *   identity  -> api.ts (server-verified session, never a client-supplied id)
 *   logic     -> every mutation calls the shared service through /api
 *   navigation-> the route comes from navigation.yaml (startParam -> route)
 *   round trip-> a completed action leaves a message in the chat
 *   words     -> copy comes from one generated module, not from literals
 */
import { createRoot } from 'react-dom/client';
import { useEffect, useState } from 'react';

import { api, ApiError } from './api';
import { backButton, boot, closeApp, haptic, mainButton, startParam } from './telegram';
import { COPY } from './copy';
import './theme.css';

interface Item {
  id: string;
  label: string;
  price_formatted: string;      // formatted SERVER-side: bot and app agree by construction
}

function routeFromStartParam(): string {
  const p = startParam() ?? '';
  const [screen] = p.split('_');
  // Routes are declared in navigation.yaml (surface: miniapp, app_route: ...).
  return { price: '/price', orders: '/orders' }[screen] ?? '/';
}

function Price() {
  const [items, setItems] = useState<Item[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Item[]>('/price')
      .then(setItems)
      .catch((e: ApiError) =>
        setError(`${COPY.error_generic}${e.traceId ? ` · ${e.traceId}` : ''}`));
  }, []);

  useEffect(() => {
    // BackButton and the system edge gesture must run the SAME function.
    backButton(closeApp);
    return () => backButton(null);
  }, []);

  useEffect(() => {
    if (!items) return;
    // Primary action on MainButton, never a custom bottom button.
    return mainButton(COPY.send_to_chat, async () => {
      await api('/price/share', { method: 'POST' });   // server posts to the chat
      haptic.confirm();
      closeApp();                                     // close AFTER the commit
    });
  }, [items]);

  if (error) return <p className="destructive">{error}</p>;
  if (!items) {
    return (
      <div style={{ display: 'grid', gap: 'var(--space-3)' }}>
        <div className="skeleton" style={{ height: 28, width: '60%' }} />
        <div className="skeleton" style={{ height: 72 }} />
        <div className="skeleton" style={{ height: 72 }} />
      </div>
    );
  }
  return (
    <>
      <h1>{COPY.price_title}</h1>
      <ul style={{ listStyle: 'none', padding: 0, display: 'grid', gap: 'var(--space-2)' }}>
        {items.map((i) => (
          <li key={i.id} className="section separator"
              style={{ padding: 'var(--space-3)', minHeight: 'var(--tap)' }}>
            <strong>{i.label}</strong>
            <span className="hint" style={{ float: 'right' }}>{i.price_formatted}</span>
          </li>
        ))}
      </ul>
      <p className="hint">{COPY.price_hint}</p>
    </>
  );
}

function App() {
  const route = routeFromStartParam();
  return route === '/price' ? <Price /> : <Price />;   // wire a real router here
}

// ownsScroll: this app scrolls vertically, so take the down-swipe rather than
// letting it minimize the app mid-list.
boot({ ownsScroll: true });
createRoot(document.getElementById('root')!).render(<App />);
