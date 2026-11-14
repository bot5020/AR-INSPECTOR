// Выбор и опознание оборудования, карточка агрегата, вход, измерения, отчёты, нормативы.
import { captureBlob, freeze, renderOverlay, unfreeze } from './camera.js';
import { api, postJson } from './net.js';
import { loadInspection, showInspectionShot, ensureInspection } from './inspection.js';
import { preparePlan, pausePlan } from './plan.js';
import { analysisShown, renderChrome } from './screen.js';
import { el, state } from './state.js';
import { closeSheet, compRow, historyRows, openSheet, paramRow, setProgress, statusPill, stopProgress, toast } from './ui.js';
import { $, buzz, esc, fmtDate, humanError, ic, LEVEL, pct, plural } from './util.js';

// ---------------------------------------------------------------- оборудование

export async function selectEquipment(id, how = '') {
  if (state.analysis?.running || state.identifying) return toast('Дождитесь завершения анализа.', 'info');
  pausePlan(); state.briefing = null; state.focusReturn = null; state.frameRevision++;
  state.eqId = id;
  state.selectedObservation = null; state.reviewedNodes = new Set();
  state.analysis = null;
  state.inspection = null;
  state.hintHidden = false;
  state.eqTab = 'overview';
  await refreshCard();
  if (state.eqId !== id) return;
  preparePlan();
  const session = await loadInspection();
  if (state.eqId !== id) return;
  if (session?.shots.length) {
    localStorage.setItem('inspectionEquipment', id);
    openSheet({ title: 'Продолжить осмотр?', subtitle: state.card.equipment.name,
      html: `<p>Осмотр №${session.id} · ${session.shots.length} снимков. Кадры и решения сохранены.</p><p class="footnote">При начале нового текущий осмотр сохранится в истории с имеющимися результатами.</p>`,
      foot: '<button class="btn" data-action="resume-inspection">Продолжить</button><button class="btn btn--glass" data-action="new-inspection">Начать новый</button>' });
  }
  if (how) toast(`${state.card.equipment.name} — ${how}`);
  renderOverlay();
  renderChrome();
}

export async function refreshCard() {
  if (!state.eqId) return;
  const eid = state.eqId;
  const card = await api(`/api/equipment/${eid}`);
  if (eid !== state.eqId) return;
  state.card = card;
  preparePlan();
  renderChrome();
}

export function resetEquipment() {
  if (state.analysis?.running || state.identifying) return toast('Дождитесь завершения анализа.', 'info');
  pausePlan(); state.briefing = null; state.focusReturn = null; state.frameRevision++;
  state.inspection = null; state.selectedObservation = null; state.reviewedNodes = new Set();
  state.auto = false; state.autoStable = 0; state.lastMain = null;
  localStorage.removeItem('inspectionEquipment');
  unfreeze();
  state.eqId = null; state.card = null; state.analysis = null; state.hintHidden = false;
  closeSheet();
  renderOverlay(); renderChrome();
}

// ---------------------------------------------------------------- идентификация

export async function identify() {
  pausePlan();
  let blob;
  try { blob = await captureBlob(1600, 0.85); } catch { toast('Нет кадра: включите камеру или выберите снимок', 'warn'); return; }
  if (!state.frozen) await freeze(blob);
  state.identifying = true;
  setProgress('ИИ узнаёт оборудование', 'обычно ~15 с');
  renderChrome();
  try {
    const fd = new FormData();
    fd.append('file', blob, 'frame.jpg');
    const res = await api('/api/identify', { method: 'POST', body: fd });
    buzz(res.equipment_id ? 40 : [80, 60, 80]);
    openSheet(identifySpec(res));
  } catch (e) {
    buzz(150);
    toast(humanError(e, 'identify'), 'warn', 5000);
    openChooser();
  } finally {
    state.identifying = false;
    stopProgress();
    renderChrome();
  }
}

export function identifySpec(res) {
  const method = { qr: 'по QR-коду', nameplate: 'по шильдику', visual: 'по внешнему виду' }[res.method] || res.method;
  if (!res.equipment_id) {
    return {
      title: 'Не удалось распознать',
      subtitle: res.what_is_it ? `Похоже на: ${res.what_is_it}` : '',
      html: `<div class="ident"><div class="ident__icon tone-orange">${ic('warn')}</div></div>
        ${res.evidence ? `<div class="note">${ic('info')}<span>${esc(res.evidence)}</span></div>` : ''}
        <div class="sec">Выберите оборудование</div><div id="eqList"></div>`,
      onRender: fillEquipmentList,
    };
  }
  const low = res.confidence < 0.6;
  const guess = res.method === 'visual';
  return {
    title: guess ? 'Предположительно' : 'Оборудование распознано',
    subtitle: guess && res.what_is_it ? `На снимке: ${res.what_is_it}` : '',
    html: `<div class="ident">
        <div class="ident__icon tone-${guess || low ? 'orange' : 'green'}">${ic(guess ? 'eye' : low ? 'warn' : 'check')}</div>
        <div class="ident__name">${esc(res.equipment.name)}</div>
        <div class="ident__meta">${esc(res.equipment.model)} · ${esc(res.equipment.location)}</div>
        <div class="chips"><span class="chip">${ic(res.method === 'qr' ? 'qr' : res.method === 'nameplate' ? 'doc' : 'eye', '')}${esc(method)}</span>
          ${guess ? `<span class="chip" title="Самооценка модели, не измеренная вероятность совпадения">оценка ИИ ${pct(res.confidence)}</span>` : `<span class="chip">${res.method === 'qr' ? 'Совпадение метки' : 'Совпадение номера'}</span>`}</div>
      </div>
      ${res.evidence ? `<div class="sec">Почему так решил ИИ</div><div class="group"><div class="row row__text">${esc(res.evidence)}</div></div>` : ''}
      ${res.nameplate_text ? `<div class="note">${ic('doc')}<span>Шильдик: ${esc(res.nameplate_text)}</span></div>` : ''}
      ${guess || low ? `<div class="note note--warn">${ic('warn')}<span>${low ? 'Низкая уверенность. ' : ''}Подтвердите, что это нужная карточка.</span></div>` : ''}`,
    foot: `<button class="btn btn--glass" data-action="choose">Другое</button>
      <button class="btn btn--wide" data-action="pick" data-id="${esc(res.equipment_id)}">${guess ? 'Выбрать эту карточку' : 'Открыть карточку'}</button>`,
  };
}

export async function fillEquipmentList() {
  const box = $('#eqList');
  if (!box) return;
  const list = await api('/api/equipment');
  list.sort((a, b) => Number(b.id === 'PUMP-001') - Number(a.id === 'PUMP-001'));
  box.innerHTML = `<div class="group">${list.map((e) => `
    <button class="row row--btn" data-action="pick" data-id="${esc(e.id)}">
      <span class="dot" data-level="${e.level ?? ''}"></span>
      <span class="row__main"><span class="row__title">${esc(e.name)}</span><span class="row__sub">${esc(e.model)} · ${esc(e.location)}</span></span>
      ${ic('chevR', 'row__chev')}</button>`).join('')}</div>`;
}

export function openChooser() {
  openSheet({
    title: 'Оборудование',
    subtitle: 'Выберите из списка или найдите по номеру',
    html: `<form id="codeForm" class="search" autocomplete="off">
        <label class="field" style="flex:1;margin:0"><input id="codeInput" placeholder="Заводской или инвентарный №" inputmode="text"></label>
        <button class="btn btn--icon" aria-label="Найти">${ic('search')}</button></form>
      <div class="sec">Все объекты</div><div id="eqList"><div class="group group--pad"><div class="skeleton"></div><div class="skeleton" style="width:60%"></div></div></div>`,
    onRender: () => {
      fillEquipmentList();
      $('#codeForm')?.addEventListener('submit', async (ev) => {
        ev.preventDefault();
        try {
          const res = await postJson('/api/resolve', { code: $('#codeInput').value });
          closeSheet();
          await selectEquipment(res.equipment_id, 'по номеру');
        } catch (e) { toast(e.message, 'warn'); }
      });
    },
  });
}
// ---------------------------------------------------------------- карточка оборудования

export const EQ_TABS = [['overview', 'Паспорт'], ['params', 'Измерения'], ['history', 'История']];

export function equipmentSpec(tab = state.eqTab) {
  const c = state.card; const e = c.equipment;
  state.eqTab = tab;
  return {
    title: e.name,
    subtitle: `${e.model} · ${e.location}`,
    html: `${accountBody()}<div class="seg" role="tablist">${EQ_TABS.map(([id, label]) =>
      `<button role="tab" aria-selected="${id === tab}" data-action="eq-tab" data-tab="${id}">${label}</button>`).join('')}</div>
      <div id="eqTabBody">${eqTabBody(tab)}</div>`,
    kind: 'equipment',
    rebuild: () => equipmentSpec(state.eqTab),
  };
}

export function eqTabBody(tab) {
  const c = state.card; const e = c.equipment;
  if (tab === 'params') {
    return `${measurementBody(c)}<div class="sec">Измерения</div>
      <div class="group" id="paramsList">${c.parameters.map(paramRow).join('') || '<div class="row muted">Нет данных</div>'}</div>
      <p class="footnote">Шкала: зелёный — норма, оранжевый — выше/ниже уставки предупреждения, красный — аварийная зона.</p>`;
  }
  if (tab === 'history') return `<div class="sec">Осмотры и обслуживание</div>${historyRows(c.history)}`;
  const legend = `<div class="legend"><span><i class="dot" data-level="0"></i>норма</span><span><i class="dot" data-level="1"></i>внимание</span>
    <span><i class="dot" data-level="2"></i>действие</span></div>`;
  return `<div class="hero">${statusPill(c.level, c.status_label, 'status--lg')}
      <div class="stats">
        <div class="stat" data-level="2"><b>${c.counts['2']}</b><span>требуют действий</span></div>
        <div class="stat" data-level="1"><b>${c.counts['1']}</b><span>требуют внимания</span></div>
        <div class="stat" data-level="0"><b>${c.counts['0']}</b><span>в норме</span></div>
      </div>${c.counts.unknown ? `<p class="footnote">Без данных для оценки: ${c.counts.unknown} ${plural(c.counts.unknown, 'узел', 'узла', 'узлов')} — нет наработки или норматива.</p>` : ''}</div>
    ${sourcesBlock(c)}
    ${measurementBody(c)}<details class="scenario-details"><summary>О данных учебного сценария</summary><p>${esc(e.data_origin || '')}</p></details>
    <p class="footnote">В последнем кадре осмотрено внешних узлов: ${c.coverage?.inspected ?? 0} из ${c.coverage?.external ?? 0}.</p>
    <section class="equipment-section"><div class="sec">Режим работы</div><div class="group">${kvRow('Состояние', e.run_state === 'running' ? 'В работе, моточасы учитываются' : 'Остановлен, моточасы не растут')}</div>
    <button class="btn btn--glass" data-action="operation" data-state="${e.run_state === 'running' ? 'stopped' : 'running'}">${e.run_state === 'running' ? 'Отметить остановку' : 'Отметить запуск'}</button></section>
    <div class="sec">Паспорт</div>
    <div class="group">
      ${kvRow('Тип', e.type)}${kvRow('Модель', e.model)}${kvRow('Заводской №', e.serial_number)}
      ${kvRow('Инвентарный №', e.inventory_number)}${kvRow('Ввод в эксплуатацию', fmtDate(e.commissioned_at))}
      ${Object.entries(e.specs).map(([k, v]) => kvRow(k, v)).join('')}
    </div>
    <div class="actions"><button class="btn btn--glass" data-action="change-eq">Выбрать другое оборудование</button></div>
    <p class="footnote">${esc(c.disclaimer)}</p>`;
}

export const kvRow = (k, v) => `<div class="row"><span class="row__label">${esc(k)}</span><span class="row__value">${esc(v)}</span></div>`;

const SOURCE = {
  resource: { icon: 'clock', tab: 'nodes' }, measurements: { icon: 'gauge', tab: 'params' },
  combined: { icon: 'sparkles', tab: 'nodes' }, visual: { icon: 'eye', tab: 'nodes' }, defects: { icon: 'flag', tab: 'history' },
};

/** Статус по источникам: общий цвет — максимум, а здесь видно, что именно его даёт. */
export function sourcesBlock(c) {
  if (!c.by_source?.length) return '';
  return `<div class="sec">Откуда статус</div><div class="group">${c.by_source.map((s) => {
    const sub = s.top[0] || (s.level == null ? s.note : s.level === 0 ? `Отклонений нет · ${s.note}` : s.note);
    const more = s.count > 1 ? ` · ещё ${s.count - 1}` : '';
    return `<button class="row row--btn row--top" data-action="${SOURCE[s.key]?.tab === 'nodes' ? 'inspection' : 'eq-tab'}" data-tab="${SOURCE[s.key]?.tab || 'nodes'}">
      <span class="row__icon" data-level="${s.level ?? ''}">${ic(SOURCE[s.key]?.icon || 'info')}</span>
      <span class="row__main"><span class="row__title">${esc(s.label)}</span><span class="row__sub">${esc(sub)}${more}</span></span>
      <span class="dot" data-level="${s.level ?? ''}" aria-label="${esc(s.status_label)}"></span></button>`;
  }).join('')}</div>`;
}

export function schemaSvg(card) {
  const s = card.equipment.schema || {};
  const comps = card.components.filter((c) => c.schema && !c.parent_id);
  if (!comps.length) return '<div class="muted small">Схема не задана</div>';
  const color = (lv) => ({ 0: '#30d158', 1: '#ff9f0a', 2: '#ff453a' }[lv] || '#8e8e93');
  return `<svg class="schema" viewBox="0 0 ${s.w || 100} ${s.h || 60}" role="img" aria-label="Мнемосхема оборудования">${comps.map((c) => {
    const [x, y, w, h] = c.schema;
    const fit = Math.max(3, Math.floor((w - 1.6) / 1.4));
    const label = c.name.length > fit ? `${c.name.slice(0, fit - 1)}…` : c.name;
    return `<g data-action="comp" data-id="${esc(c.id)}"><title>${esc(c.name)} — ${esc(LEVEL[c.rollup_level])}</title>
      <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="1.8" style="fill:${color(c.rollup_level)};fill-opacity:.2;stroke:${color(c.rollup_level)};stroke-width:.45"></rect>
      <circle cx="${x + w - 2}" cy="${y + 2}" r=".9" style="fill:${color(c.rollup_level)}"></circle>
      <text x="${x + 1.2}" y="${y + 3.6}">${esc(label)}</text></g>`;
  }).join('')}</svg>`;
}

export const loadBriefing = preparePlan;

export function openEquipmentSheet() {
  if (!state.card) return openChooser();
  openSheet(equipmentSpec(state.eqTab));
}
export function openGuide() {
  const g = state.card?.guidance || [];
  openSheet({
    title: 'Куда смотреть',
    subtitle: 'Приоритет по данным карточки',
    html: g.length ? `<div class="group">${g.map((x, i) => `<button class="row row--btn row--top" data-action="comp" data-id="${esc(x.component_id)}">
        <span class="row__num">${i + 1}</span>
        <span class="row__main"><span class="row__title">${esc(x.name)}</span><span class="row__sub">${esc(x.reason)}</span>
        ${x.look_for.length ? `<span class="row__sub" style="color:var(--text-3)">Смотреть: ${esc(x.look_for.join(', '))}</span>` : ''}</span>
        <span class="dot" data-level="${x.level}"></span></button>`).join('')}</div>
        <p class="footnote">Порядок учитывает ресурс, сроки ТО, отклонения параметров и открытые замечания.</p>`
      : '<div class="group"><div class="row muted">Узлов, требующих внимания, нет. Плановый осмотр это не отменяет.</div></div>',
  });
}
export function accountBody() {
  const roles = { engineer: 'Инженер', master: 'Мастер', admin: 'Администратор' };
  const name = state.user?.display_name || 'Гость', role = roles[state.user?.role] || '';
  return `<div class="row"><span class="row__main">${esc(name)}${role && role !== name ? ` · ${esc(role)}` : ''}</span>
    <button class="btn-sm" data-action="${state.user ? 'logout' : 'login'}">${state.user ? 'Выйти' : 'Войти'}</button></div>`;
}

export function openLogin() {
  // Фоновые запросы (детектор, статус) без входа получают 401 и зовут сюда же. Если окно входа уже открыто,
  // не перерисовываем его — иначе поле пароля пересоздаётся и клавиатура на телефоне закрывается.
  if (!el.sheet.hidden && el.sheet.classList.contains('is-open') && $('#loginForm')) return;
  openSheet({ title: 'Вход в АР-Инспектор', subtitle: 'Учетная запись локального стенда',
    html: `<form id="loginForm">
      <label class="field"><span>Имя пользователя</span><input id="loginName" required autocomplete="username" autocapitalize="none" value="engineer"></label>
      <label class="field"><span>Пароль</span><input id="loginPass" type="password" required autocomplete="current-password"></label>
      <p id="loginError" class="footnote" role="alert"></p></form>`,
    foot: '<button class="btn" type="submit" form="loginForm">Войти</button>',
    onRender: () => $('#loginForm').addEventListener('submit', async event => {
      event.preventDefault();
      const button = el.sheetFoot.querySelector('button'); button.disabled = true;
      try {
        const result = await postJson('/api/auth/login', { username: $('#loginName').value.trim(), password: $('#loginPass').value });
        state.user = result.user; state.author = result.user.display_name;
        closeSheet(); renderChrome(); openChooser();
      } catch (error) { if ($('#loginError')) $('#loginError').textContent = error.message; }
      finally { button.disabled = false; }
    }),
  });
}

export function measurementBody(card) {
  const m = card.measurement;
  if (!m) return card.parameters?.length ? '<p class="footnote">Показания имитируются для демонстрации.</p>' : '';
  return `<section class="equipment-section"><div class="sec">Источник измерений</div><div class="note"><span><b>Воспроизведение данных организаторов</b><br>${esc(m.timestamp)} · запись ${m.index + 1} из ${m.count}<br>${esc(m.notice)}</span></div>
    <button class="btn btn--glass" data-action="scenario">Выбрать момент измерений</button></section>`;
}

export async function openScenario() {
  const result = await api(`/api/equipment/${state.eqId}/scenario`);
  openSheet({ title: 'Измерения насоса', subtitle: 'Учебное воспроизведение, без подключения к датчикам',
    html: `<div class="actions">${result.presets.map(p => `<button class="btn btn--glass" data-scenario-index="${p.index}">${esc(p.label)}</button>`).join('')}</div>
      <form id="scenarioForm"><label class="field"><span>Номер записи от 1 до ${result.state.count}</span><input id="scenarioIndex" type="number" min="1" max="${result.state.count}" value="${result.state.index + 1}" required></label></form>
      <p class="footnote">Смена записи меняет только показания. Наработка и история не ускоряются вместе с воспроизведением.</p>`,
    foot: '<button class="btn" form="scenarioForm" type="submit">Показать измерения</button>',
    onRender: () => {
      const select = async index => {
        try { state.card = await postJson(`/api/equipment/${state.eqId}/scenario`, { index }); preparePlan(true); renderChrome(); state.eqTab = 'params'; openEquipmentSheet(); }
        catch (error) { toast(error.message, 'warn'); }
      };
      el.sheetBody.querySelectorAll('[data-scenario-index]').forEach(button => button.addEventListener('click', () => select(Number(button.dataset.scenarioIndex))));
      $('#scenarioForm').addEventListener('submit', event => { event.preventDefault(); select(Number($('#scenarioIndex').value) - 1); });
    },
  }, { push: true });
}

export function openReports(id) {
  const eid = state.eqId;
  openSheet({ title: 'Отчет об осмотре', subtitle: 'Отчет содержит данные на момент сохранения',
    html: `<p>В отчете: агрегат, исполнитель, измерения, источники нормативов, результаты осмотра и история работ.</p>
      <div class="actions"><a class="btn" href="/api/equipment/${encodeURIComponent(eid)}/inspections/${id}/report.pdf" target="_blank" rel="noopener">Скачать PDF</a>
      <a class="btn btn--glass" href="/api/equipment/${encodeURIComponent(eid)}/inspections/${id}/report.xlsx">Скачать Excel</a></div>
      ${['master', 'admin'].includes(state.user?.role) ? `<button class="btn btn--glass" data-action="approve" data-id="${id}">Подтвердить осмотр</button>` : '<p class="footnote">Осмотр может подтвердить мастер или администратор.</p>'}`,
  }, { push: true });
}

export function openNormForm(id) {
  const c = state.card.components.find(c => c.id === id);
  openSheet({ title: 'Нормативы узла', subtitle: c.name,
    html: `<form id="normForm">
      <label class="field"><span>Срок службы, лет</span><input name="years" type="number" min="0.01" step="any" value="${esc(c.normative_life_years ?? '')}"></label>
      <label class="field"><span>Ресурс, моточасов</span><input name="hours" type="number" min="1" step="any" value="${esc(c.normative_hours ?? '')}"></label>
      <label class="field"><span>Интервал ТО, моточасов</span><input name="service" type="number" min="1" step="any" value="${esc(c.maintenance?.interval_hours ?? '')}"></label>
      <label class="field"><span>Источник или допущение команды</span><textarea name="source" required minlength="5">${esc(c.normative_source || '')}</textarea></label>
      <p class="footnote">Пустое значение означает, что норматив не задан. Само по себе изменение интервала не задает счетчик на момент последнего ТО.</p>
      <p id="normError" role="alert"></p></form>`,
    foot: '<button class="btn" type="submit" form="normForm">Сохранить нормативы</button>',
    onRender: () => $('#normForm').addEventListener('submit', async event => {
      event.preventDefault(); const f = new FormData(event.target);
      const number = key => f.get(key) ? Number(f.get(key)) : null;
      try {
        state.card = await postJson(`/api/equipment/${state.eqId}/components/${id}/normative`, {
          normative_life_years: number('years'), normative_hours: number('hours'), maintenance_interval_hours: number('service'), source: f.get('source'),
        });
        preparePlan(true); openEquipmentSheet();
      } catch (error) { if ($('#normError')) $('#normError').textContent = error.message; }
    }),
  }, { push: true });
}

export async function resumeInspection() {
  const shot = state.inspection?.shots.at(-1);
  closeSheet();
  if (shot) await showInspectionShot(shot.analysis_id);
}
export async function newInspection() {
  if (state.inspection?.shots.length && state.inspection.status === 'draft') {
    state.card = await postJson(`/api/equipment/${state.eqId}/inspections`, { inspection_id: state.inspection.id, author: state.author });
    state.inspection = null;
  }
  state.selectedObservation = null; state.reviewedNodes = new Set();
  closeSheet(); unfreeze();
  await ensureInspection(); renderChrome();
}
