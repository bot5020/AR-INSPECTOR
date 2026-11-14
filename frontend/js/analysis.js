// ИИ-осмотр снимка (поток SSE) и разбор находок инженером.
import { applyView, captureBlob, freeze, renderOverlay } from './camera.js';
import { refreshAnalysisLevels, saveInspection } from './component.js';
import { identify } from './equipment.js';
import { postJson } from './net.js';
import { pausePlan, preparePlan } from './plan.js';
import { ensureInspection, refreshInspection } from './inspection.js';
import { analysisWarning, renderChrome } from './screen.js';
import { el, state } from './state.js';
import { openSheet, paintSheet, setProgress, stopProgress, toast } from './ui.js';
import { buzz, esc, humanError, ic, pct, plural, VISUAL, VISUAL_LEVEL } from './util.js';

// ---------------------------------------------------------------- ИИ-анализ (поток SSE)

let preparingFrame = false;

export async function runAnalysis() {
  if (!state.eqId) return identify();
  if (state.analysis?.running || preparingFrame) return;
  if ((!state.camera && !state.frozen) || !['camera', 'file'].includes(state.frameSource)) {
    toast('Включите камеру или выберите снимок.', 'warn');
    return;
  }
  let blob;
  const eid = state.eqId, revision = state.frameRevision;
  preparingFrame = true;
  try {
    pausePlan();
    await ensureInspection();
    if (eid !== state.eqId || revision !== state.frameRevision) return;
    blob = await captureBlob(1600, 0.88);
    if (eid !== state.eqId || revision !== state.frameRevision) return;
    if (!await freeze(blob)) return;
  } catch (error) {
    toast(error.message || 'Не удалось получить снимок', 'warn');
    return;
  } finally {
    preparingFrame = false;
  }
  state.analysis = { items: [], running: true, result: null };
  state.shownKeys.clear();
  state.hintHidden = false;
  setProgress('ИИ осматривает снимок', 'может занять около минуты');
  renderOverlay();
  renderChrome();
  const fd = new FormData();
  fd.append('file', blob, 'frame.jpg');
  fd.append('source', state.frameSource);
  fd.append('inspection_id', state.inspection.id);
  const t0 = performance.now();
  try {
    const r = await fetch(`/api/equipment/${state.eqId}/analyze`, { method: 'POST', body: fd });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, i); buf = buf.slice(i + 2);
        const ev = /event: (.*)/.exec(block)?.[1];
        const data = /data: (.*)/s.exec(block)?.[1];
        if (ev && data) handleAnalysisEvent(ev, JSON.parse(data), t0);
      }
    }
  } catch (e) {
    buzz(150);
    state.analysis.items = []; state.analysis.result = null;
    state.analysis.error = humanError(e);
  } finally {
    state.analysis.running = false;
    stopProgress();
    renderOverlay();
    renderChrome();
    preparePlan(true);
  }
}

export function handleAnalysisEvent(ev, data, t0) {
  const a = state.analysis;
  if (ev === 'item') {
    a.items.push(data);
    renderOverlay();
    state.progressBase = `Найдено узлов: ${a.items.length}`;
  }
  if (ev === 'error') {
    a.items = []; a.result = null;
    a.error = data.message;
    buzz(150);
  }
  if (ev === 'done') {
    if (data.inspection) state.inspection = data.inspection;
    a.result = data;
    a.items = data.items;
    for (const item of data.items) state.reviewedNodes?.delete(item.component_id);
    a.id = data.analysis_id;
    a.seconds = Math.round((performance.now() - t0) / 1000);
    state.card = data.card;
    state.controlsCollapsed = true;
    state.view = { scale: 1, x: 0, y: 0 }; applyView();
    // заметный сигнал окончания: двойной — если есть признаки дефекта
    buzz(data.items.some((x) => x.category === 'defect') ? [70, 50, 70] : 40);
  }
}
// Все точки входа используют общий осмотр и карточку узла.
export async function reviewItem(key) {
  const item = state.analysis?.items.find(i => i.key === key);
  if (!item) return;
  const { openItem } = await import('./component.js');
  return openItem(item);
}
export const findings = () => (state.analysis?.items || []).filter(i => i.component_id && i.category !== 'ok');
export const openFindings = () => findings().filter(i => !['confirmed', 'rejected'].includes(i.verdict));
export async function startReview() {
  const { reviewInspection } = await import('./inspection.js');
  return reviewInspection();
}
export async function decide(key, verdict) {
  const { decideObservation } = await import('./component.js');
  return decideObservation(state.analysis?.id, key, verdict);
}
export async function openResults() {
  const { openInspection } = await import('./inspection.js');
  return openInspection('nodes');
}
export async function requestSave() {
  if (state.inspection?.status === 'draft') {
    try { await refreshInspection(); } catch (error) { return toast(error.message, 'warn'); }
  }
  const left = state.inspection?.review.open ?? openFindings().length;
  if (!left || state.analysis?.saved) return saveInspection();
  openSheet({
    title: 'Сохранить без решения?',
    subtitle: `${left} ${plural(left, 'находка', 'находки', 'находок')} ИИ без решения инженера`,
    html: `<div class="note note--warn">${ic('warn')}<span>Мастер увидит в отчёте, что эти находки не разобраны. Лучше отметить каждую: это пара касаний.</span></div>`,
    foot: `<button class="btn btn--glass" data-action="save-anyway">Сохранить так</button>
      <button class="btn btn--wide" data-action="${state.inspection ? 'review-inspection' : 'review'}">${ic('list')}Разобрать</button>`,
  }, { push: !el.sheet.hidden });
}
