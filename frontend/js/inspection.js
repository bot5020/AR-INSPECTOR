// Один осмотр, один список узлов, все наблюдения по кадрам.
import { api, postJson } from './net.js';
import { state } from './state.js';
import { openSheet, toast } from './ui.js';
import { planMarkup } from './plan.js';
import { esc, ic, plural } from './util.js';

export async function loadInspection() {
  const eid = state.eqId;
  const { session } = await api(`/api/equipment/${eid}/inspections/session`);
  if (state.eqId === eid) state.inspection = session;
  return session;
}
export async function ensureInspection() {
  if (state.inspection?.status === 'draft') return state.inspection;
  const eid = state.eqId;
  const { session } = await postJson(`/api/equipment/${eid}/inspections/session`, {});
  if (state.eqId !== eid) throw new Error('Оборудование изменилось. Повторите действие.');
  state.inspection = session;
  localStorage.setItem('inspectionEquipment', eid);
  return session;
}
export async function refreshInspection() {
  if (!state.inspection) return null;
  const eid = state.eqId, sid = state.inspection.id;
  const { session } = await api(`/api/equipment/${eid}/inspections/session/${sid}`);
  if (state.eqId === eid && state.inspection?.id === sid) state.inspection = session;
  return session;
}
export const observations = () => state.inspection?.review.findings || (state.analysis?.items || []).map(i => ({ ...i, analysis_id: state.analysis.id, shot_number: 1 }));
export const pendingObservations = () => observations().filter(i => i.component_id && i.category !== 'ok' && !['confirmed', 'rejected'].includes(i.verdict));
export async function showInspectionShot(aid) {
  if (!state.inspection?.shots.some(s => s.analysis_id === aid)) return;
  const { reopenAnalysis } = await import('./system.js');
  await reopenAnalysis(aid);
}
export async function reviewInspection() {
  await refreshInspection();
  const selected = state.selectedObservation;
  const pending = pendingObservations();
  const other = pending.filter(o => !selected || o.analysis_id !== selected.analysis_id || o.key !== selected.key);
  const next = [...(other.length && state.sheetSpec?.kind === 'component' ? other : pending)].sort((a, b) => (a.verdict === 'deferred') - (b.verdict === 'deferred'))[0];
  if (!next) return openInspection();
  const { openComponent } = await import('./component.js');
  openComponent(next.component_id, next);
}
function nodesBody() {
  const all = observations();
  const rows = [...(state.card?.components || [])].sort((a, b) => {
    const pending = c => pendingObservations().some(i => i.component_id === c.id);
    return Number(pending(b)) - Number(pending(a)) || (b.rollup_level || 0) - (a.rollup_level || 0);
  }).map(c => {
    const seen = all.filter(i => i.component_id === c.id);
    const left = seen.filter(i => i.category !== 'ok' && !['confirmed', 'rejected'].includes(i.verdict)).length;
    const current = state.analysis?.items.find(i => i.component_id === c.id);
    const n = current ? state.analysis.items.indexOf(current) + 1 : null;
    const frames = new Set(seen.map(i => i.analysis_id)).size;
    return `<button class="row row--btn row--top" data-action="comp" data-id="${esc(c.id)}">
      <span class="node-number" data-level="${c.rollup_level ?? ''}">${n || ic(c.visual_access === 'internal' ? 'gauge' : 'eye')}</span>
      <span class="row__main"><span class="row__title">${esc(c.name)}</span><span class="row__sub">${left ? `Ожидают решения: ${left}` : seen.length ? 'Есть результаты осмотра' : c.visual_access === 'internal' ? 'Проверяется измерениями' : 'Еще не отмечен на снимках'}${frames ? ` · ${frames} ${plural(frames, 'кадр', 'кадра', 'кадров')}` : ''}</span></span>${ic('chevR')}</button>`;
  }).join('');
  const unknown = all.filter(i => !i.component_id);
  return `<div id="inspectionPlan">${planMarkup()}</div><div class="sec">Узлы оборудования</div><div class="group">${rows}</div>
    ${unknown.length ? '<p class="footnote">Есть объекты без подтвержденной привязки к карточке. Откройте их метки на соответствующем снимке.</p>' : ''}`;
}
function shotsBody(s) {
  if (!s?.shots.length) return '<p>Добавьте первый снимок. Все последующие кадры сохранятся в этом осмотре.</p>';
  return `<div class="group">${s.shots.map(shot => `<button class="row row--btn" data-action="inspection-shot" data-id="${shot.analysis_id}">
    <img class="inspection-thumb" src="${shot.image_url}" alt="Снимок ${shot.number}"><span class="row__main"><span class="row__title">Снимок ${shot.number}</span>
    <span class="row__sub">${shot.items.length} ${plural(shot.items.length, 'узел', 'узла', 'узлов')}${shot.partial ? ' · ответ ИИ неполный' : ''}</span></span>${ic('chevR')}</button>`).join('')}</div>`;
}
function finishBody(s) {
  if (!s?.shots.length) return '<p>Для завершения добавьте и проверьте хотя бы один снимок.</p>';
  return `<div class="group"><div class="row"><span class="row__main">Снимков в осмотре</span><b>${s.shots.length}</b></div>
    <div class="row"><span class="row__main">Находок без решения</span><b>${s.review.open}</b></div>
    <div class="row"><span class="row__main">Подтверждено инженером</span><b>${s.review.confirmed}</b></div></div>
    ${s.not_covered.length ? `<div class="sec">Еще не отмечены на снимках</div><p>${s.not_covered.map(c => esc(c.name)).join(', ')}</p>` : ''}
    ${s.conflicts.length ? `<div class="note note--warn">На кадрах различаются оценки: ${s.conflicts.map(c => esc(c.name)).join(', ')}. Сопоставьте наблюдения.</div>` : ''}
    <p class="footnote">В отчет войдут все кадры и решения. Нерешенные находки и недостающие ракурсы также будут указаны.</p>`;
}
export function inspectionSpec(tab = state.inspectionTab) {
  state.inspectionTab = ['nodes', 'shots', 'finish'].includes(tab) ? tab : 'nodes';
  const s = state.inspection, pending = pendingObservations().length;
  const buttons = [['nodes', 'Узлы'], ['shots', 'Снимки'], ['finish', 'Завершение']];
  return { kind: 'inspection', title: s ? `Осмотр №${s.id}` : 'Осмотр оборудования',
    subtitle: `${state.card.equipment.name}${s?.status === 'closed' ? ' · завершен' : ''}`,
    html: `<div class="seg" role="tablist">${buttons.map(([id, label]) => `<button role="tab" aria-selected="${state.inspectionTab === id}" data-action="inspection-tab" data-tab="${id}">${label}</button>`).join('')}</div>
      ${state.inspectionTab === 'nodes' ? nodesBody() : state.inspectionTab === 'shots' ? shotsBody(s) : finishBody(s)}`,
    foot: s?.status === 'closed' ? `<button class="btn" data-action="report" data-id="${s.event_id}">Открыть отчет</button>`
      : state.inspectionTab === 'finish' ? `<button class="btn btn--glass" data-action="add-shot">${ic('camera')}Добавить снимок</button><button class="btn" data-action="save-inspection" ${s?.shots.length ? '' : 'disabled'}>Завершить осмотр</button>`
        : pending ? `<button class="btn" data-action="review-inspection">Проверить находки · ${pending}</button>` : '',
    rebuild: () => inspectionSpec(state.inspectionTab) };
}
export async function openInspection(tab = state.inspectionTab) {
  if (!state.card) return;
  try {
    await refreshInspection();
    openSheet(inspectionSpec(tab));
  } catch (error) { toast(error.message, 'warn'); }
}

// Один порядок узлов для списка, стрелок и перехода после записи.
export function reviewNodes() {
  const ids = new Set(observations().map(o => o.component_id).filter(Boolean));
  return (state.card?.components || []).filter(c => ids.has(c.id));
}
export function nextReviewTarget(currentId) {
  const nodes = reviewNodes(), visited = state.reviewedNodes || new Set();
  const current = nodes.findIndex(c => c.id === currentId);
  const ordered = [...nodes.slice(current + 1), ...nodes.slice(0, Math.max(0, current))];
  return ordered.find(c => !visited.has(c.id))?.id || null;
}
export async function navigateReview(id) {
  const found = observations().filter(o => o.component_id === id);
  const next = found.find(o => o.analysis_id === state.analysis?.id) || found.at(-1);
  if (!next) return;
  if (next.analysis_id !== state.analysis?.id) await showInspectionShot(next.analysis_id);
  state.selectedObservation = next;
  const { openComponent } = await import('./component.js');
  openComponent(id, next, true);
  const { renderOverlay } = await import('./camera.js');
  renderOverlay();
}
export async function advanceReview(id) {
  state.reviewedNodes ||= new Set();
  state.reviewedNodes.add(id);
  const target = nextReviewTarget(id);
  if (target) return navigateReview(target);
  return openInspection('finish');
}
