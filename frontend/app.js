// AR-Инспектор — клиент PWA. Без внешних зависимостей: работает в локальной сети без интернета.
// Интерфейс: камера на весь экран, поверх — плавающие стеклянные панели.
// Модули — в frontend/js/: точка входа связывает обработчики нажатий и запускает приложение.
import { decide, openResults, requestSave, runAnalysis, startReview } from './js/analysis.js';
import { applyView, focusItem, previewObservation, returnFromFocus, wholePhoto, freeze, renderOverlay, setZoom, startCamera, unfreeze } from './js/camera.js';
import { componentFoot, decideObservation, openComponent, openEventForm, openItem, saveInspection } from './js/component.js';
import { eqTabBody, equipmentSpec, identify, loadBriefing, openChooser, openEquipmentSheet, openGuide, openLogin, openNormForm, openReports, openScenario, resetEquipment, selectEquipment, resumeInspection, newInspection } from './js/equipment.js';
import { detectLoop, qrLoop } from './js/live.js';
import { api, postJson } from './js/net.js';
import { analysisShown, renderChrome, renderHint } from './js/screen.js';
import { inspectionSpec, observations, openInspection, reviewInspection, showInspectionShot, navigateReview } from './js/inspection.js';
import { pausePlan, preparePlan } from './js/plan.js';
import { el, state } from './js/state.js';
import { openAnalyses, openPhotos, openSystem, pollStatus, reopenAnalysis } from './js/system.js';
import { closeSheet, openSheet, paintSheet, paramRow, sheetBack, toast } from './js/ui.js';
import { $, buzz, humanError } from './js/util.js';

// ---------------------------------------------------------------- события интерфейса

document.addEventListener('click', async (ev) => {
  const t = ev.target.closest('[data-action]');
  if (!t) return;
  const a = t.dataset.action;
  if (t.tagName !== 'A') ev.preventDefault();
  try { switch (a) {
    case 'resume-inspection': return resumeInspection();
    case 'new-inspection': return newInspection();
    case 'inspection': return openInspection();
    case 'inspection-tab': return paintSheet(inspectionSpec(t.dataset.tab));
    case 'retry-plan': return preparePlan(true);
    case 'observation': return openComponent(t.dataset.id, observations().find(o => o.analysis_id === Number(t.dataset.aid) && o.key === t.dataset.key));
    case 'observation-verdict': return decideObservation(Number(t.dataset.aid), t.dataset.key, t.dataset.verdict);
    case 'focus-observation': return previewObservation(Number(t.dataset.aid), t.dataset.key);
    case 'focus-back': return returnFromFocus();
    case 'focus-overview': return wholePhoto();
    case 'add-shot':
      if (state.analysis?.running || state.identifying || state.frameLoading) return;
      return openSheet({ title: state.inspection?.status === 'closed' ? 'Новый осмотр' : 'Добавить снимок',
        subtitle: 'Выберите, откуда взять изображение',
        html: `<div class="stack add-source"><button class="btn btn--glass" data-action="camera">Сделать фото</button><button class="btn btn--glass" data-action="photos">Выбрать из фото</button>${state.eqId && state.camera && !state.frozen ? `<button class="btn btn--glass" data-action="auto" aria-pressed="${state.auto}">Автопроверка кадра: ${state.auto ? 'включена' : 'выключена'}</button>` : ''}</div>` }, { push: !el.sheet.hidden });
    case 'inspection-shot': return showInspectionShot(Number(t.dataset.id));
    case 'review-node': return navigateReview(t.dataset.id);
    case 'review-inspection': return reviewInspection();
    case 'focus-item': return focusItem(t.dataset.key);
    case 'toggle-dock': state.controlsCollapsed = !state.controlsCollapsed; return renderChrome();
    case 'toggle-marks': state.marksVisible = !state.marksVisible; return renderChrome();
    case 'zoom-in': return setZoom(state.view.scale * 1.5);
    case 'zoom-out': return setZoom(state.view.scale / 1.5);
    case 'zoom-reset': return wholePhoto();
    case 'login': return openLogin();
    case 'logout':
      if (state.analysis?.running || state.identifying) return toast('Дождитесь завершения анализа.', 'info');
      await postJson('/api/auth/logout', {}); state.user = null; resetEquipment(); return openLogin();
    case 'scenario': return openScenario();
    case 'operation':
      state.card = await postJson(`/api/equipment/${state.eqId}/operation`, { state: t.dataset.state });
      preparePlan(true); return openEquipmentSheet();
    case 'report': return openReports(Number(t.dataset.id));
    case 'approve':
      state.card = await postJson(`/api/equipment/${state.eqId}/inspections/${t.dataset.id}/approve`, {});
      toast('Осмотр подтвержден'); return openReports(Number(t.dataset.id));
    case 'edit-norm': return openNormForm(t.dataset.id);
    case 'primary':
      if (analysisShown()) return state.camera ? unfreeze() : openPhotos();   // «Новый снимок»
      return state.eqId ? runAnalysis() : identify();
    case 'eq-chip': return state.eqId ? openEquipmentSheet() : openChooser();
    case 'choose': return openChooser();
    case 'card': return openEquipmentSheet();
    case 'pick': closeSheet(); return selectEquipment(t.dataset.id, '');
    case 'change-eq': return resetEquipment();
    case 'eq-tab': {
      if (t.dataset.tab === 'nodes') return openInspection('nodes');
      state.eqTab = t.dataset.tab;
      for (const b of el.sheetBody.querySelectorAll('.seg button')) b.setAttribute('aria-selected', String(b.dataset.tab === t.dataset.tab));
      $('#eqTabBody').innerHTML = eqTabBody(state.eqTab);
      if (state.sheetSpec) state.sheetSpec = equipmentSpec(state.eqTab);
      if (state.eqTab === 'overview') loadBriefing();
      return undefined;
    }
    case 'comp': return openComponent(t.closest('[data-id]').dataset.id);
    case 'item': return openItem(state.analysis.items.find((x) => x.key === t.dataset.key));
    case 'results': return openResults();
    case 'event': return openEventForm(t.dataset.type, t.dataset.id, t.dataset.note || '');
    case 'quick': {
      // добавить/убрать готовую формулировку в комментарии
      const box = $('#evNote');
      // разделитель «;» — в самих формулировках и в тексте ИИ бывают запятые
      const parts = box.value.split(/\s*;\s*/).map((s) => s.trim()).filter(Boolean);
      const txt = t.dataset.text;
      const on = t.getAttribute('aria-pressed') !== 'true';
      const next = on ? [...parts.filter((p) => p !== txt), txt] : parts.filter((p) => p !== txt);
      box.value = next.join('; ');
      t.setAttribute('aria-pressed', String(on));
      buzz(15);
      return undefined;
    }
    case 'more':
    case 'less': {
      const c = state.card.components.find((x) => x.id === t.dataset.id);
      if (c) el.sheetFoot.innerHTML = componentFoot(c, state.selectedObservation?.component_id === c.id ? state.selectedObservation : null, a === 'more');
      return undefined;
    }
    case 'close': return closeSheet();
    case 'sheet-back': return sheetBack();
    case 'live': return unfreeze();
    case 'guide': return openGuide();
    case 'hide-hint': state.hintHidden = true; return renderHint();
    case 'auto':
      state.auto = !state.auto;
      t.textContent = `Автопроверка кадра: ${state.auto ? 'включена' : 'выключена'}`;
      t.setAttribute('aria-pressed', String(state.auto));
      renderChrome();
      return toast(state.auto ? 'Автоанализ: ИИ запустится, когда объект неподвижен в кадре' : 'Автоанализ выключен', state.auto ? 'check' : 'info');
    case 'save-inspection': return requestSave();
    case 'save-anyway': return saveInspection();
    case 'review': return startReview();
    case 'verdict': return decide(t.dataset.key, t.dataset.verdict);
    case 'review-open': return openComponent(t.dataset.id, state.analysis?.items.find((x) => x.key === t.dataset.key));
    case 'new-shot': closeSheet(); return state.camera ? unfreeze() : openPhotos();
    case 'photos': return openPhotos();
    case 'pick-file': return openPhotos();
    case 'camera': pausePlan(); closeSheet(); unfreeze(); return state.camera ? undefined : startCamera();
    case 'analyses': return openAnalyses();
    case 'reopen-analysis': return reopenAnalysis(t.dataset.id);
    case 'system': return openSystem();
    default: return undefined;
  } } catch (error) { toast(humanError(error), 'warn'); }
});

el.scrim.addEventListener('click', closeSheet);
el.file.addEventListener('change', async () => {
  const file = el.file.files?.[0];
  if (!file) return;
  if (file.type && !file.type.startsWith('image/')) return toast('Выберите изображение.', 'warn');
  try {
    pausePlan();
    if (!await freeze(file)) return;
    state.frameSource = 'file';
    state.analysis = null;
    state.shownKeys.clear();
    state.hints = [];
    state.hintHidden = false;
    closeSheet();
    renderOverlay(); renderChrome();
  } catch (error) { toast(error.message, 'warn'); }
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') state.focusReturn ? returnFromFocus() : closeSheet(); });

// Обновление телеметрии на вкладке «Параметры».
setInterval(async () => {
  if (!state.eqId || el.sheet.hidden || !$('#paramsList')) return;
  try {
    state.card = await api(`/api/equipment/${state.eqId}`);
    const box = $('#paramsList');
    if (box) box.innerHTML = state.card.parameters.map(paramRow).join('');
  } catch { /* сеть */ }
}, 5000);
// ---------------------------------------------------------------- старт

if ('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}
renderChrome();
pollStatus();
startCamera().then(() => { qrLoop(); detectLoop(); });
const startParams = new URLSearchParams(location.search);
api('/api/auth/me').then(({ user }) => {
  state.user = user;
  if (!user) openLogin();
  else if (startParams.get('eq') || localStorage.getItem('inspectionEquipment')) selectEquipment(startParams.get('eq') || localStorage.getItem('inspectionEquipment'), '');
}).catch(error => toast(humanError(error), 'warn'));
