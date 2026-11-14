// Запросы к серверу.
import { openLogin } from './equipment.js';
import { state } from './state.js';

// ---------------------------------------------------------------- утилиты

export async function api(path, opts = {}) {
  const userAtStart = state.user;
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* не JSON */ }
    if (r.status === 401 && !path.startsWith('/api/auth/') && state.user === userAtStart) {
      state.user = null;
      openLogin();
    }
    throw new Error(msg);
  }
  return (r.headers.get('content-type') || '').includes('json') ? r.json() : r;
}
export const postJson = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
