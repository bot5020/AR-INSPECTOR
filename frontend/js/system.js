// Снимки прошлых анализов, состояние ИИ и детектора.
import { freeze, renderOverlay } from './camera.js';
import { barcode } from './live.js';
import { api } from './net.js';
import { renderChrome } from './screen.js';
import { el, state } from './state.js';
import { closeSheet, openSheet } from './ui.js';
import { $, esc, fmtDate, ic, sleep } from './util.js';

export function openPhotos() {
  if (state.analysis?.running || state.identifying) return;
  el.file.value = '';
  el.file.click();
}


export function openAnalyses() {
  const last = state.card?.last_analysis;
  openSheet({
    title: 'Снимки анализов',
    subtitle: last ? `Последний — ${fmtDate(last.created_at)}` : '',
    html: last
      ? `<img class="analysis-img" src="/api/analyses/${last.id}/image" alt="Снимок последнего анализа">`
      : '<div class="group"><div class="row muted">Анализов ещё не было</div></div>',
    foot: last ? `<button class="btn" data-action="reopen-analysis" data-id="${last.id}">${ic('eye')}Показать с метками</button>` : '',
  }, { push: true });
}

export async function reopenAnalysis(id) {
  const eid = state.eqId, revision = ++state.frameRevision;
  state.frameLoading = true; state.analysis = null; state.focusReturn = null;
  el.still.hidden = true; renderOverlay(); renderChrome();
  try {
    const a = await api(`/api/analyses/${id}`);
    if (revision !== state.frameRevision || eid !== state.eqId) return false;
    if (a.equipment_id && a.equipment_id !== eid) throw new Error('Этот кадр относится к другому оборудованию.');
    const response = await fetch(a.image_url);
    if (!response.ok) throw new Error('Снимок недоступен');
    const blob = await response.blob();
    if (revision !== state.frameRevision || eid !== state.eqId) return false;
    if (!await freeze(blob, { revision })) return false;
    closeSheet();
    state.frameSource = 'history'; state.shownKeys.clear();
    state.analysis = { items: a.result.items || [], running: false, result: a.result, id: a.id, seconds: a.result.model?.seconds ?? '—',
      saved: state.inspection?.status === 'closed' };
    state.hintHidden = false;
    renderOverlay(); renderChrome(); return true;
  } catch (error) {
    if (revision === state.frameRevision) { state.frameLoading = false; renderChrome(); }
    throw error;
  }
}

export async function pollStatus() {
  for (;;) {
    try {
      const s = await api('/api/status');
      state.status = s;
      const ai = s.vlm.ready && s.vlm.loaded !== false;
      el.sysDot.dataset.state = ai && s.detector.ready ? 'ok' : !s.vlm.ready ? 'bad' : 'warn';
    } catch {
      el.sysDot.dataset.state = 'bad';
    }
    await sleep(10000);
  }
}

export function openSystem() {
  const s = state.status;
  const row = (icon, tone, title, sub) => `<div class="row"><span class="row__icon tone-${tone}">${ic(icon)}</span>
    <span class="row__main"><span class="row__title">${title}</span><span class="row__sub">${sub}</span></span></div>`;
  if (!s) { openSheet({ title: 'Система', html: '<div class="group"><div class="row muted">Сервер не отвечает</div></div>' }); return; }
  const ai = s.vlm.ready && s.vlm.loaded !== false;
  // Сначала адрес домашней/офисной сети (192.168.x → 10.x → 172.16–31.x), адреса VPN — в конце.
  const rank = (ip) => {
    const [a, b] = ip.split('.').map(Number);
    return a === 192 && b === 168 ? 0 : a === 10 ? 1 : a === 172 && b >= 16 && b <= 31 ? 2 : 3;
  };
  const hosts = (s.https_hosts || []).filter((h) => /^\d/.test(h) && h !== '127.0.0.1').sort((x, y) => rank(x) - rank(y));
  openSheet({
    title: 'Система',
    subtitle: s.offline ? 'Офлайн: всё работает на локальном сервере' : 'Облачный ИИ (режим разработки)',
    html: `${state.user?.role === 'admin' ? '<div class="actions"><a class="btn btn--glass" href="/labels" target="_blank" rel="noopener">Печать QR-меток оборудования</a></div>' : ''}<div class="group">
        ${row('sparkles', ai ? 'green' : s.vlm.ready ? 'orange' : 'red', 'ИИ-модель', ai ? `${esc(s.vlm.model)} — готова` : s.vlm.ready ? 'Сервер есть, модель не загружена' : 'Недоступна — запустите LM Studio')}
        ${row('scan', s.detector.ready ? 'green' : s.detector.loading ? 'orange' : 'red', 'Распознавание объектов', s.detector.ready ? `${esc(s.detector.device)}` : s.detector.loading ? 'Загружается…' : esc(s.detector.error || 'Ошибка'))}
        ${row('photo', state.camera ? 'green' : 'orange', 'Камера', state.camera ? 'Включена' : esc(state.cameraError || 'Недоступна'))}
        ${row('qr', 'blue', 'Сканер QR', barcode ? 'Встроен в браузер' : 'Через сервер')}
      </div>
      ${hosts.length ? `<div class="sec">Подключить телефон или планшет</div><div class="group">
        ${row('phone', 'blue', `https://${esc(hosts[0])}:${s.port}`, 'Та же Wi-Fi-сеть. Браузер один раз предупредит о сертификате — «Перейти на сайт».')}</div>
        ${hosts.length > 1 ? `<p class="footnote">Если не открывается, попробуйте: ${hosts.slice(1).map((h) => `https://${esc(h)}:${s.port}`).join(', ')}</p>` : ''}` : ''}`,
  });
}
