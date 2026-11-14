// Главный экран сохраняет место для фотографии; подробности находятся в одном осмотре.
import { renderOverlay } from './camera.js';
import { el, state } from './state.js';
import { esc, ic, plural } from './util.js';

export const analysisShown = () => !!(state.frozen && state.analysis?.result && !state.analysis.running && !state.frameLoading);
export function analysisWarning(r = {}) {
  return [r.image_quality === 'poor' ? 'Снимок нечеткий: переснимите для надежной оценки.' : '',
    r.matches_equipment === false ? 'Проверьте, соответствует ли снимок выбранному оборудованию.' : '',
    r.partial ? 'Ответ ИИ неполный. Добавьте еще один снимок.' : ''].filter(Boolean).join(' ');
}
export function renderChrome() {
  const busy = !!(state.analysis?.running || state.identifying || state.frameLoading);
  const done = analysisShown(), focus = !!state.focusReturn;
  el.app.dataset.mode = state.eqId ? 'inspect' : 'scan';
  el.app.dataset.busy = busy ? '1' : '0';
  el.app.dataset.result = done ? '1' : '0';
  el.app.dataset.focus = focus ? '1' : '0';
  el.backBtn.hidden = !state.eqId;
  el.backBtn.disabled = busy;
  el.backBtn.innerHTML = ic('chevL');
  el.eqName.textContent = state.card?.equipment.name || 'Выберите оборудование';
  el.eqSub.textContent = state.eqId ? 'Данные оборудования' : 'По QR, снимку или из списка';
  el.eqDot.dataset.level = state.card?.level ?? '';
  el.eqChev.innerHTML = ic('chevR');
  el.eqChip.disabled = busy;
  const shots = state.inspection?.shots || [];
  const shot = shots.find(s => s.analysis_id === state.analysis?.id);
  const sessionLabel = state.frameLoading ? 'Открывается снимок…' : state.inspection
    ? `Осмотр №${state.inspection.id} · ${shot ? `кадр ${shot.number}/${shots.length}` : `${shots.length} ${plural(shots.length, 'снимок', 'снимка', 'снимков')}`}${state.inspection.status === 'closed' ? ' · завершен' : ''}` : '';
  if (sessionLabel) el.eqSub.textContent = sessionLabel;
  el.eqChip.setAttribute('aria-label', `${el.eqName.textContent}. ${el.eqSub.textContent}. Открыть данные оборудования`);
  el.sessionInfo.hidden = true;
  el.sessionInfo.textContent = '';
  const pending = state.inspection?.review.open ?? (state.analysis?.items || []).filter(i => i.component_id && i.category !== 'ok' && !['confirmed', 'rejected'].includes(i.verdict)).length;
  el.addShot.innerHTML = `${ic('camera')}<span>Добавить снимок</span>`;
  el.addShot.disabled = busy;
  el.inspectBtn.innerHTML = `${ic(state.eqId ? 'list' : 'doc')}<span>${state.eqId ? 'Осмотр' : 'Оборудование'}</span>${pending ? `<b class="pending-count">${pending}</b>` : ''}`;
  el.inspectBtn.dataset.action = state.eqId ? 'inspection' : 'choose';
  el.inspectBtn.disabled = busy;
  el.inspectBtn.setAttribute('aria-label', state.eqId ? `Осмотр. ${pending} находок ожидают решения` : 'Выбрать оборудование');
  el.workbar.hidden = focus || busy;
  el.focusBar.hidden = !focus;
  for (const b of el.focusBar.querySelectorAll('button')) b.disabled = busy;
  el.captureAction.hidden = busy || done || focus || (!state.camera && !state.frozen);
  el.captureAction.innerHTML = `${ic(state.eqId ? 'camera' : 'scan')}<span>${state.eqId ? state.frozen ? 'Проверить снимок' : 'Сделать снимок и проверить' : 'Опознать оборудование'}</span>`;
  el.photoTools.hidden = !state.frozen || busy;
  el.marksToggle.hidden = !done;
  el.marksToggle.innerHTML = ic('eye');
  el.marksToggle.setAttribute('aria-label', state.marksVisible ? 'Скрыть метки' : 'Показать метки');
  el.marksToggle.setAttribute('aria-pressed', String(state.marksVisible));
  el.reticle.hidden = !(!state.eqId && state.camera && !state.frozen);
  el.empty.hidden = !!(state.camera || state.frozen || state.frameLoading);
  el.empty.innerHTML = `<div class="empty__icon">${ic('camera')}</div><h3>Начните со снимка</h3><p>${esc(state.cameraError || 'Сделайте фотографию или выберите готовую через «Добавить снимок».')}</p>`;
  renderHint();
  if (state.frozen) renderOverlay();
}
export function renderHint() {
  const message = state.analysis?.running || state.identifying || state.frameLoading || state.focusReturn ? ''
    : state.analysis?.error || (analysisShown() ? analysisWarning(state.analysis.result) : '');
  el.hint.hidden = !message || state.hintHidden;
  if (message) el.hint.innerHTML = `<div class="hint__body"><div class="hint__text">${esc(message)}</div></div><button class="hint__close" data-action="hide-hint" aria-label="Скрыть">${ic('x')}</button>`;
}
