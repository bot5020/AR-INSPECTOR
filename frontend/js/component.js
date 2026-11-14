// Карточка узла и запись работ.
import { renderOverlay } from './camera.js';
import { kvRow, openReports } from './equipment.js';
import { api, postJson } from './net.js';
import { observations, pendingObservations, refreshInspection, advanceReview, reviewNodes } from './inspection.js';
import { preparePlan } from './plan.js';
import { renderChrome } from './screen.js';
import { el, state } from './state.js';
import { closeSheet, compRow, disc, historyRows, openSheet, paintSheet, paramRow, statusPill, toast } from './ui.js';
import { $, buzz, esc, EVENT, fmtDate, fmtNum, humanError, ic, pct, SOURCE_ICON, VISUAL, VISUAL_LEVEL } from './util.js';

// ---------------------------------------------------------------- карточка узла

export function openItem(it) {
  if (!it) return;
  if (it.component_id) return openComponent(it.component_id, { ...it, analysis_id: state.analysis?.id });
  openSheet({
    title: it.name,
    subtitle: 'Объект найден ИИ, в карточке его нет',
    html: visualBody({ ...it, analysis_id: state.analysis?.id }),
    foot: `<button class="btn btn--tint tone-orange" data-action="event" data-type="defect" data-note="${esc(it.reason)}">${ic('flag')}Зарегистрировать замечание</button>`,
  });
}

/** skipReason — причина уже показана в «Что обнаружено», не повторяем её. */
export function visualBody(v, skipReason = false) {
  if (!v) return '<p class="muted small" style="margin:0">ИИ-анализ этого узла ещё не выполнялся. Наведите камеру на узел и нажмите кнопку анализа.</p>';
  const lvl = VISUAL_LEVEL[v.category];
  return `${statusPill(lvl, VISUAL[v.category])}
    ${v.analysis_id && v.key ? `<button class="btn-sm photo-link" data-action="focus-observation" data-aid="${v.analysis_id}" data-key="${esc(v.key)}">${ic('eye')}На фото</button>` : ''}
    ${v.superseded ? `<div class="note">${ic('check')}<span>Узел проверен инженером после анализа — оценка ИИ учтена.</span></div>` : ''}
    ${v.reason && !skipReason ? `<p class="row__text" style="margin:12px 0 0">${esc(v.reason)}</p>` : ''}
    ${v.signs?.length ? `<div class="chips" style="margin-top:10px">${v.signs.map((s) => `<span class="chip">${esc(signName(s))}</span>`).join('')}</div>` : ''}
    <div class="meter tone-blue"><span style="width:${Math.round((v.confidence || 0) * 100)}%"></span></div>
    <div class="row__sub">Уверенность модели ${pct(v.confidence)} (самооценка)${v.created_at ? ` · ${fmtDate(v.created_at)}` : ''}</div>
    ${v.category === 'ok' ? `<div class="note">${ic('info')}<span>Видимых проблем нет — это не подтверждает исправность: внутренние дефекты по изображению не определяются.</span></div>` : ''}`;
}

export function resourceBody(c) {
  const r = c.resource;
  const lvl = { ok: 0, approaching: 1, exhausted: 2 }[r.status];
  return `${r.status !== 'unknown' ? `<div data-level="${lvl}">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span class="value">${pct(r.used_fraction)} <small>израсходовано</small></span>
        <span class="row__sub">осталось ${esc(r.remaining_text || '—')}</span></div>
      <div class="meter"><span style="width:${Math.min(100, Math.round((r.used_fraction || 0) * 100))}%"></span></div>
      <div class="row__sub">${r.estimated_end ? `Ориентировочно до ${fmtDate(r.estimated_end)} · ` : ''}ограничивает: ${esc((r.limiting_factor || '—').toLowerCase())}</div></div>` : ''}
    <div class="group" style="margin-top:12px">
      ${kvRow('Нормативный срок', r.normative || 'не задан')}
      ${kvRow('Установлен', `${fmtDate(r.installed_at)}${r.age_text ? ` (${r.age_text})` : ''}`)}
      ${c.operating_hours != null ? kvRow('Наработка', fmtNum(c.operating_hours, 'ч')) : ''}
      ${c.cycles != null ? kvRow('Рабочие циклы', fmtNum(c.cycles)) : ''}
      ${r.factors.length > 1 ? r.factors.map((f) => kvRow(f.label, `${f.actual} из ${f.normative}`)).join('') : ''}
    </div>
    ${r.limitations.map((l) => `<div class="note note--warn">${ic('warn')}<span>${esc(l)}</span></div>`).join('')}
    <p class="footnote" style="margin-top:10px">Источник: ${esc(c.normative_source || 'не задан')}. Оценка по данным учета.</p>
    ${state.user?.role === 'admin' ? `<button class="btn btn--glass" data-action="edit-norm" data-id="${esc(c.id)}">Изменить нормативы</button>` : ''}`;
}

export function maintenanceBody(m) {
  const tone = { ok: 0, soon: 1, overdue: 2 }[m.status];
  const text = { overdue: 'Срок ТО наступил', soon: 'ТО приближается', ok: 'Интервал не достигнут', unknown: 'Нет данных' }[m.status];
  return `<div class="group">${kvRow('Работа', m.name)}${kvRow('Периодичность', [m.interval_days ? `${m.interval_days} дн.` : '', m.interval_hours ? `${m.interval_hours} ч` : ''].filter(Boolean).join(' / '))}
    ${kvRow('Последнее', fmtDate(m.last_at))}
    ${m.hours_left != null ? kvRow('До ТО по наработке', fmtNum(m.hours_left, 'ч')) : ''}
    <div class="row"><span class="row__label">Следующее</span><span class="row__value">${fmtDate(m.due_at)}&nbsp; ${statusPill(tone, text)}</span></div></div>`;
}

export const signName = s => ({ corrosion: 'Коррозия', paint_damage: 'Повреждение покрытия', leakage: 'Следы утечки', leak: 'Следы утечки', crack: 'Трещина', cracks: 'Трещины', overheating: 'Признаки перегрева', dirt: 'Загрязнение', contamination: 'Загрязнение', mechanical_damage: 'Механическое повреждение', damaged_fins: 'Повреждение ребер', missing_guard: 'Нет защитного кожуха', missing_bolt: 'Отсутствует крепеж', burn: 'Нагар', soot: 'Нагар' }[s] || s);

export function chooseObservation(id, item = null) {
  const all = observations().filter(i => i.component_id === id);
  if (item) return all.find(i => i.analysis_id === item.analysis_id && i.key === item.key) || item;
  return all.find(i => i.category !== 'ok' && !['confirmed', 'rejected'].includes(i.verdict)) || all.at(-1) || null;
}

export function componentSpec(id, item = null) {
  const c = state.card?.components.find((x) => x.id === id);
  if (!c) return null;
  const parent = c.parent_id ? state.card.components.find((x) => x.id === c.parent_id) : null;
  const kids = state.card.components.filter((x) => x.parent_id === c.id);
  const isAsm = c.kind === 'assembly';
  const level = isAsm ? c.rollup_level : c.level;
  let reasons = isAsm ? c.rollup_reasons : c.reasons;
  const selected = chooseObservation(id, item);
  const visual = selected || c.visual;
  const obs = observations().filter(i => i.component_id === id);
  const closed = state.inspection?.status === 'closed';
  const res = c.resource;
  if (selected) reasons = [{ source: 'visual', level: VISUAL_LEVEL[selected.category], text: selected.reason || VISUAL[selected.category] },
    ...reasons.filter(r => r.source !== 'visual')];
  const uniqueReasons = new Set();
  reasons = reasons.filter(r => {
    const key = JSON.stringify([r.source, r.component_id || id, r.text.trim().replace(/\s+/g, ' ').toLowerCase()]);
    if (uniqueReasons.has(key)) return false;
    uniqueReasons.add(key); return true;
  });
  const recommendations = [...new Set([selected?.recommendation, ...c.recommendations].filter(Boolean))];
  const reasonRow = r => `<div class="row row--top" data-level="${r.level}"><span class="row__icon">${ic(SOURCE_ICON[r.source] || 'info')}</span>
        <span class="row__main"><span class="row__sub">${esc({ visual: 'На этом снимке', resource: 'Ресурс', maintenance: 'Срок ТО', parameter: 'Измерения', defect: 'Ранее зарегистрировано', combined: 'Гипотеза по данным' }[r.source] || 'По данным карточки')}</span><span class="row__text">${esc(r.source === 'defect' ? r.text.replace(/Открытое замечание:\s*/ig, '') : r.text)}</span></span></div>`;
  const currentReasons = reasons.filter(r => r.source !== 'defect');
  const previousReasons = reasons.filter(r => r.source === 'defect');
  const findings = currentReasons.length
    ? currentReasons.map(reasonRow).join('')
    : `<div class="row" data-level="${level ?? ''}"><span class="row__icon">${ic(level == null ? 'info' : 'check')}</span>
        <span class="row__main row__text">${previousReasons.length ? 'Есть ранее зарегистрированные замечания, см. раздел ниже' : level == null ? 'Недостаточно данных для оценки' : 'По данным учёта отклонений нет'}</span></div>`;
  const recs = recommendations.length
    ? `<div class="sec">Что сделать</div><div class="group">${recommendations.map((r, i) =>
      `<div class="row row--top"><span class="row__num">${i + 1}</span><span class="row__main row__text">${esc(r)}</span></div>`).join('')}</div>` : '';
  const resLvl = { ok: 0, approaching: 1, exhausted: 2 }[res.status];
  const resTone = resLvl === 2 ? 'red' : resLvl === 1 ? 'orange' : resLvl === 0 ? 'green' : 'gray';
  const visTone = visual ? ({ ok: 'green', check: 'orange', defect: 'red' }[visual.category]) : 'gray';

  return {
    kind: 'component', componentId: id, observation: selected,
    title: c.name === 'Насос (корпус и проточная часть)' ? 'Корпус насоса' : c.name,
    subtitle: '',
    headerHtml: `<div class="node-header">${statusPill(level, undefined, 'node-status')}${selected ? verdictBadge(selected) : ''}</div>`,
    headerActionsHtml: reviewNavigation(id),
    html: `
      ${selected?.analysis_id && selected.key ? `<button class="node-shot" type="button" data-action="focus-observation" data-aid="${selected.analysis_id}" data-key="${esc(selected.key)}" aria-label="Показать ${esc(c.name)} на снимке">
        <img src="/api/analyses/${selected.analysis_id}/crop/${encodeURIComponent(selected.key)}" alt="${esc(c.name)}: область, отмеченная ИИ на кадре ${selected.shot_number || '—'}">
        <span class="node-shot__tag">Кадр ${selected.shot_number || '—'} · область ИИ</span><span class="node-shot__open">${ic('eye')}На фото</span></button>` : ''}
      ${obs.length > 1 ? `<div class="sec">Наблюдения по кадрам</div><div class="observation-tabs">${obs.map(o => `<button class="btn-sm ${o.analysis_id === selected?.analysis_id && o.key === selected?.key ? 'is-selected' : ''}" data-action="observation" data-id="${esc(id)}" data-aid="${o.analysis_id}" data-key="${esc(o.key)}">Кадр ${o.shot_number} · ${esc({ ok: 'нет признаков', check: 'проверить', defect: 'признаки дефекта' }[o.category])}</button>`).join('')}</div>` : ''}
      ${state.inspection?.conflicts.some(f => f.component_id === id) ? '<div class="note note--warn">На разных кадрах оценки отличаются. Сопоставьте наблюдения перед решением.</div>' : ''}
      <div class="sec">${reasons.length ? 'Что обнаружено' : 'Состояние'}</div><div class="group node-findings">${findings}</div>
      ${recs}
      ${previousReasons.length ? disc('Ранее зарегистрировано', 'history', 'orange', `<div class="group node-findings">${previousReasons.map(reasonRow).join('')}</div>`, { meta: `${previousReasons.length}` }) : ''}
      ${selected && selected.category !== 'ok' && !closed ? `<button class="btn-sm" data-action="${pendingObservations().some(o => o.analysis_id !== selected.analysis_id || o.key !== selected.key) ? 'review-inspection' : 'inspection'}">${ic('list')}${pendingObservations().some(o => o.analysis_id !== selected.analysis_id || o.key !== selected.key) ? 'Следующая находка' : 'К списку находок'}</button>` : ''}
      ${workBlock(c)}
      ${disc('Осмотр ИИ', 'eye', visTone, c.visual_access === 'internal' ? '<p>Внутренний узел недоступен обычной камере. Используйте измерения и отдельную проверку инженера.</p>' : visualBody(visual, reasons.some((r) => r.source === 'visual')), { meta: c.visual_access === 'internal' ? 'не доступен камере' : visual ? ({ ok: 'нет видимых признаков', check: 'проверить', defect: 'дефект' }[visual.category]) : 'не осмотрен' })}
      ${disc('Ресурс', 'clock', resTone, resourceBody(c), { open: resLvl >= 1, meta: res.used_fraction != null ? pct(res.used_fraction) : 'нет данных' })}
      ${c.maintenance ? disc('Обслуживание', 'wrench', { ok: 'green', soon: 'orange', overdue: 'red' }[c.maintenance.status] || 'gray',
        maintenanceBody(c.maintenance), { meta: c.maintenance.status === 'overdue' ? 'просрочено' : fmtDate(c.maintenance.due_at) }) : ''}
      ${c.parameters.length ? disc('Параметры', 'gauge', c.parameters.some((p) => p.level) ? 'orange' : 'green',
        `<div class="group">${c.parameters.map(paramRow).join('')}</div>`, { meta: `${c.parameters.length}` }) : ''}
      ${kids.length ? disc('Комплектующие', 'cube', 'blue', `<div class="group">${kids.map((k) => compRow(k)).join('')}</div>`, { meta: `${kids.length}` }) : ''}
      ${parent || Object.keys(c.specs).length || c.inspect_for.length ? disc('Характеристики', 'doc', 'gray',
        `${parent ? kvRow('Входит в', parent.name) : ''}${Object.keys(c.specs).length ? `<div class="group">${Object.entries(c.specs).map(([k, v]) => kvRow(k, v)).join('')}</div>` : ''}
         ${c.inspect_for.length ? `<div class="sec" style="margin-top:14px">Что осматривать</div><div class="chips">${c.inspect_for.map((s) => `<span class="chip">${esc(s)}</span>`).join('')}</div>` : ''}`) : ''}
      ${disc('История', 'history', 'gray', historyRows(c.history, false), { meta: `${c.history.length}` })}
      <div style="height:6px"></div>`,
    foot: componentFoot(c, selected, false),
    rebuild: () => componentSpec(id, selected),
  };
}

const VERDICT_BADGE = {
  confirmed: ['red', 'flag', 'Подтверждено'], rejected: ['green', 'check', 'Не подтверждено'],
  deferred: ['gray', 'clock', 'Отложено'],
};
function verdictBadge(o) {
  const [tone, icon, text] = VERDICT_BADGE[o.verdict] || (o.category === 'ok' ? ['green', 'check', 'Без видимых признаков'] : ['orange', 'clock', 'Ждёт решения']);
  return `<span class="node-review-state tone-${tone}" title="Решение инженера по наблюдению на кадре ${o.shot_number || '—'}">${ic(icon)}${text}</span>`;
}
function workBlock(c) {
  if (state.inspection?.status === 'closed') return '';
  return `<div class="sec">Выполненные работы</div><div class="work-chips">
    <button class="btn-sm" data-action="event" data-type="maintenance" data-id="${esc(c.id)}">${ic('wrench')}ТО</button>
    <button class="btn-sm" data-action="event" data-type="replacement" data-id="${esc(c.id)}">${ic('swap')}Замена</button>
    ${c.open_defects.length ? `<button class="btn-sm" data-action="event" data-type="resolved" data-id="${esc(c.id)}">${ic('check')}Устранено</button>` : ''}</div>`;
}

function reviewNavigation(id) {
  const nodes = reviewNodes(), index = nodes.findIndex(c => c.id === id);
  if (index < 0) return '';
  return `<nav class="review-nav" aria-label="Узлы осмотра"><button data-action="review-node" data-id="${esc(nodes[index-1]?.id || '')}" ${index ? '' : 'disabled'} aria-label="Предыдущий узел">${ic('chevL')}</button><button class="review-nav__list" data-action="inspection" aria-label="Открыть список узлов">${index+1}<span>/${nodes.length}</span></button><button data-action="review-node" data-id="${esc(nodes[index+1]?.id || '')}" ${index+1<nodes.length ? '' : 'disabled'} aria-label="Следующий узел">${ic('chevR')}</button></nav>`;
}
export function componentFoot(c, item) {
  if (state.inspection?.status === 'closed') return '<span class="footnote">Осмотр завершен. Решения сохранены в отчете.</span>';
  const note = esc(item && item.category !== 'ok' ? item.reason || '' : '');
  const on = (v) => (item?.verdict === v ? ' is-on' : '');
  const attrs = (v) => `data-action="observation-verdict" data-aid="${item.analysis_id}" data-key="${esc(item.key)}" data-verdict="${v}"`;
  const decisions = item?.analysis_id && item.category !== 'ok'
    ? `<button class="btn decide__yes${on('confirmed')}" ${attrs('confirmed')}>${ic('flag')}Подтверждаю</button>
       <button class="btn btn--glass decide__no${on('rejected')}" ${attrs('rejected')}>${ic('x')}Не подтверждаю</button>
       <button class="btn btn--glass btn--icon decide__later${on('deferred')}" ${attrs('deferred')} aria-label="Решить позже" title="Решить позже">${ic('clock')}</button>`
    : `<button class="btn btn--tint tone-green" data-action="event" data-type="checked" data-id="${esc(c.id)}">${ic('check')}Проверено</button>
       <button class="btn btn--tint tone-orange" data-action="event" data-type="defect" data-id="${esc(c.id)}" data-note="${note}">${ic('flag')}Замечание</button>`;
  return `<div class="review-decisions">${decisions}</div>`;
}

export function openComponent(id, item = null, replace = false) {
  if (item) state.selectedObservation = item;
  const spec = componentSpec(id, item);
  if (spec) {
    state.selectedObservation = spec.observation;
    if (!el.sheet.hidden && state.sheetSpec?.kind === 'component' && state.sheetSpec.componentId === id) paintSheet(spec);
    else openSheet(spec, { push: !replace && !el.sheet.hidden && state.sheetSpec?.kind !== 'component' });
    renderOverlay();
  }
}

export const EVENT_TITLE = {
  checked: 'Узел проверен', defect: 'Замечание', maintenance: 'Обслуживание выполнено',
  replacement: 'Замена компонента', resolved: 'Замечание устранено',
};
// Готовые формулировки: в перчатках проще нажать, чем набрать текст.
export const QUICK = {
  defect: ['Течь', 'Коррозия', 'Нагрев, потемнение', 'Шум, вибрация', 'Загрязнение', 'Механическое повреждение',
    'Ослаблен крепёж', 'Нет пломбы поверки'],
  checked: ['Замечаний нет', 'Нужен повторный осмотр'],
  maintenance: ['Смазка', 'Протяжка соединений', 'Очистка', 'Замена фильтра', 'Регулировка'],
  replacement: ['Плановая замена', 'Замена по дефекту'],
  resolved: ['Устранено на месте', 'Устранено при ремонте'],
};

export function openEventForm(type, componentId, note = '') {
  const c = componentId ? state.card.components.find((x) => x.id === componentId) : null;
  const t = EVENT[type];
  openSheet({
    title: EVENT_TITLE[type] || 'Запись',
    subtitle: c ? c.name : state.card?.equipment.name,
    html: `<form id="evForm" class="form" autocomplete="off">
      <div class="ident" style="padding-top:0"><div class="ident__icon tone-${t.tone}">${ic(t.icon)}</div></div>
      ${type === 'replacement' ? `<div class="note note--warn">${ic('warn')}<span>После замены дата установки и наработка обнулятся — ресурс пересчитается.</span></div>` : ''}
      ${type === 'checked' ? `<div class="note">${ic('info')}<span>Отметка инженера заменяет предварительную оценку ИИ для этого узла.</span></div>` : ''}
      <div class="field"><span>${type === 'defect' ? 'Что обнаружено — нажмите или напишите' : 'Комментарий — нажмите или напишите'}</span>
        <div class="quick">${(QUICK[type] || []).map((q) => `<button type="button" data-action="quick" data-text="${esc(q)}" aria-pressed="false">${esc(q)}</button>`).join('')}</div></div>
      <label class="field"><textarea id="evNote" placeholder="${type === 'defect' ? 'Что обнаружено' : 'Необязательно'}">${esc(note)}</textarea></label>
      <p class="footnote">Исполнитель: ${esc(state.user?.display_name || 'Войдите в приложение')}</p>
    </form>`,
    foot: `<button class="btn" type="submit" form="evForm">${ic('check')}Сохранить</button>`,
    onRender: () => $('#evForm')?.addEventListener('submit', (ev) => { ev.preventDefault(); submitEvent(type, componentId); }),
  }, { push: true });
}

export async function submitEvent(type, componentId) {
  state.author = state.user?.display_name || '';
  localStorage.setItem('author', state.author);
  try {
    const item = chooseObservation(componentId, state.selectedObservation?.component_id === componentId ? state.selectedObservation : null);
    const verdict = item?.analysis_id && item.category !== 'ok' ? { checked: 'rejected', defect: 'confirmed' }[type] : undefined;
    state.card = await postJson(`/api/equipment/${state.eqId}/events`, {
      type, component_id: componentId || null, note: $('#evNote').value, author: state.author, analysis_id: item?.analysis_id,
      ...(verdict ? { verdict, category: item.category } : {}),
    });
    if (verdict) { item.verdict = verdict;
      const displayed = state.analysis?.id === item.analysis_id && state.analysis.items.find(i => i.key === item.key);
      if (displayed) displayed.verdict = verdict;
    }
    if (verdict && state.inspection) await refreshInspection();
    preparePlan(true);
    renderChrome();
    buzz(40);
    toast('Записано в историю оборудования');
    refreshAnalysisLevels();
    if (componentId) await advanceReview(componentId);
    else closeSheet();
  } catch (e) { buzz(150); toast(humanError(e), 'warn', 5000); }
}

/** После записи работ пересчитываем цвета меток последнего анализа по свежей карточке. */
export function refreshAnalysisLevels() {
  if (!state.analysis?.items || !state.card) return;
  for (const it of state.analysis.items) {
    const c = state.card.components.find((x) => x.id === it.component_id);
    if (c) it.level = c.kind === 'assembly' ? (c.rollup_level ?? it.level) : (c.level ?? it.level);
  }
  renderOverlay();
}

export async function saveInspection() {
  if (!state.eqId) return;
  try {
    state.card = await postJson(`/api/equipment/${state.eqId}/inspections`, { author: state.author, analysis_id: state.analysis?.id,
      ...(state.inspection ? { inspection_id: state.inspection.id } : {}) });
    if (state.inspection) {
      state.inspection.status = 'closed'; state.inspection.event_id = state.card.saved_inspection_id;
      if (localStorage.getItem('inspectionEquipment') === state.eqId) localStorage.removeItem('inspectionEquipment');
    }
    if (state.analysis) state.analysis.saved = true;   // кнопка в итоге меняется на «Осмотр сохранён» — без повторного сохранения
    renderChrome();
    buzz(40);
    toast('Осмотр сохранён в карточке оборудования');
    openReports(state.card.saved_inspection_id);
  } catch (e) { buzz(150); toast(humanError(e), 'warn', 5000); }
}

let deciding = false;
export async function decideObservation(aid, key, verdict) {
  if (deciding || !['confirmed', 'rejected', 'deferred'].includes(verdict)) return;
  const item = observations().find(i => i.analysis_id === aid && i.key === key);
  if (!item?.component_id || state.inspection?.status === 'closed') return;
  deciding = true;
  try {
    state.card = await postJson(`/api/equipment/${state.eqId}/events`, {
      component_id: item.component_id, analysis_id: aid, verdict, category: item.category,
      type: verdict === 'confirmed' ? 'defect' : verdict === 'rejected' ? 'checked' : 'review',
      note: verdict === 'confirmed' ? item.reason || 'Находка подтверждена инженером' : verdict === 'rejected' ? 'Находка ИИ не подтверждена' : 'Решение отложено до дополнительной проверки',
    });
    item.verdict = verdict;
    const displayed = state.analysis?.id === aid && state.analysis.items.find(i => i.key === key);
    if (displayed) displayed.verdict = verdict;
    await refreshInspection();
    preparePlan(true); refreshAnalysisLevels(); renderChrome();
    await advanceReview(item.component_id);
    toast(verdict === 'deferred' ? 'Решение отложено' : 'Решение сохранено');
  } catch (error) { toast(humanError(error), 'warn'); }
  finally { deciding = false; }
}
