// План готовится при изменении контекста, а открытие панели только читает результат.
import { api } from './net.js';
import { state } from './state.js';
import { esc, ic } from './util.js';

let controller, request = 0;
export function pausePlan() {
  request++;
  controller?.abort();
  if (state.briefing?.loading) state.briefing = { ...state.briefing, loading: false, deferred: true };
}
export function planMarkup() {
  const b = state.briefing;
  const labels = { visual: 'По снимку', parameter: 'Измерения', maintenance: 'Срок ТО', resource: 'Ресурс', defect: 'Открытое замечание', inspection: 'Плановый осмотр' };
  const rows = (b?.steps || []).map(s => `<button class="row row--btn row--top" data-action="comp" data-id="${esc(s.component_id)}">
    <span class="row__icon">${ic('eye')}</span><span class="row__main"><span class="row__title">${esc(s.name)}</span>
    <span class="row__text">${esc(s.action)}</span><span class="row__sub">${esc(labels[s.source] || 'По данным карточки')}: ${esc(s.reason)}</span></span>${ic('chevR')}</button>`).join('');
  return `<div class="sec">Что проверить дальше</div>${rows ? `<div class="group">${rows}</div>` : `<p class="muted">${b?.loading ? 'Формируется план осмотра…' : b?.error ? 'Не удалось подготовить план.' : 'Дополнительных указаний пока нет. Начните с общего снимка.'}</p>`}
    <p class="footnote">${b?.loading ? 'Обновляется…' : b?.source === 'ai' ? 'Порядок проверок выбран ИИ. Основания и действия взяты из данных оборудования.' : b?.deferred ? 'План по данным карточки. ИИ обновит его после анализа снимка.' : b?.error ? 'ИИ-план временно недоступен. Показаны проверки по данным карточки.' : ''}</p>
    ${b?.error || b?.deferred ? '<button class="btn-sm" data-action="retry-plan">Обновить ИИ-план</button>' : ''}`;
}
function paintPlan() {
  const box = document.getElementById('inspectionPlan');
  if (box) box.innerHTML = planMarkup();
}
export function preparePlan(force = false) {
  if (!state.eqId || !state.card || state.analysis?.running || state.identifying) return;
  const c = state.card;
  const key = JSON.stringify([state.eqId, c.last_analysis?.id, c.history?.[0]?.id, c.measurement?.index,
    c.guidance?.map(g => [g.component_id, g.level, g.reason])]);
  if (!force && state.briefing?.key === key) return;
  pausePlan();
  const n = ++request, eid = state.eqId;
  controller = new AbortController();
  state.briefing = { ...(state.briefing?.equipmentId === eid ? state.briefing : {}), key, equipmentId: eid, loading: true, error: null };
  paintPlan();
  return api(`/api/equipment/${eid}/briefing`, { signal: controller.signal }).then(b => {
    if (n !== request || eid !== state.eqId) return;
    state.briefing = { ...b, key, equipmentId: eid, loading: false };
    paintPlan();
  }).catch(error => {
    if (n !== request || eid !== state.eqId || error.name === 'AbortError') return;
    state.briefing = { ...state.briefing, loading: false, error: error.message };
    paintPlan();
  });
}
