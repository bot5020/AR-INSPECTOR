// Уведомления, индикатор ожидания, шторка и общие блоки разметки.
import { el, state } from './state.js';
import { $, esc, EVENT, fmtDate, fmtNum, ic, LEVEL } from './util.js';

export let toastTimer;
export function toast(text, icon = 'check', ms = 2800) {
  el.toast.innerHTML = `${ic(icon)}<span>${esc(text)}</span>`;
  el.toast.querySelector('.ic').style.color = icon === 'check' ? '' : 'var(--orange)';
  el.toast.hidden = false;
  el.toast.style.animation = 'none'; void el.toast.offsetWidth; el.toast.style.animation = '';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.toast.hidden = true; }, ms);
}

/** Прогресс со счётчиком секунд: 15–20 секунд ожидания без ориентира кажутся зависанием. */
export let progressTimer;
export function setProgress(text, expect = '') {
  clearInterval(progressTimer);
  el.progress.hidden = !text;
  if (!text) return;
  state.progressBase = text;
  state.progressT0 = state.progressT0 || performance.now();
  const tick = () => {
    const s = Math.round((performance.now() - state.progressT0) / 1000);
    el.progressText.textContent = state.progressBase;
    el.progressMeta.textContent = `${s} с${expect ? ` · ${expect}` : ''}`;
  };
  tick();
  progressTimer = setInterval(tick, 1000);
}
export function stopProgress() {
  clearInterval(progressTimer);
  state.progressT0 = 0;
  el.progress.hidden = true;
}
// ---------------------------------------------------------------- шторка

export function openSheet(spec, { push = false } = {}) {
  if (push && !el.sheet.hidden && state.sheetSpec) {
    state.sheetStack.push({ ...state.sheetSpec, scroll: el.sheetBody.scrollTop });
  } else if (!push) {
    state.sheetStack = [];
  }
  paintSheet(spec);
  clearTimeout(sheetHideTimer);
  el.scrim.classList.remove('is-leaving');
  el.sheet.hidden = false;
  el.scrim.hidden = false;
  // Принудительный пересчёт стилей вместо requestAnimationFrame: rAF не срабатывает, пока страница
  // не отрисовывается (свёрнута/заблокирована), и шторка оставалась за краем экрана.
  void el.sheet.offsetHeight;
  el.sheet.classList.add('is-open');
}
export let sheetHideTimer;

function reviewLayout(active) {
  if (!el.app) return;
  const mode = active ? '1' : '0';
  if (el.app.dataset.review === mode) return;
  el.app.dataset.review = mode;
  requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
}
export function paintSheet(spec) {
  reviewLayout(spec.kind === 'component');
  state.sheetSpec = spec;
  el.sheetTitle.textContent = spec.title;
  el.sheetTitle.title = spec.title;
  if (spec.headerHtml) el.sheetSub.innerHTML = spec.headerHtml;
  else el.sheetSub.textContent = spec.subtitle || '';
  el.sheetTools.innerHTML = spec.headerActionsHtml || '';
  el.sheetTools.hidden = !spec.headerActionsHtml;
  el.sheetBody.innerHTML = spec.html;
  el.sheetFoot.innerHTML = spec.foot || '';
  el.sheetFoot.hidden = !spec.foot;
  el.sheetBack.hidden = state.sheetStack.length === 0;
  el.sheetBack.innerHTML = ic('chevL');
  el.sheetClose.innerHTML = ic('x');
  el.sheetBody.scrollTop = spec.scroll || 0;
  spec.onRender?.();
}

export function sheetBack() {
  const prev = state.sheetStack.pop();
  if (!prev) return closeSheet();
  paintSheet(prev.rebuild ? { ...prev.rebuild(), scroll: prev.scroll } : prev);
}

export function closeSheet() {
  reviewLayout(false);
  if (el.sheet.hidden || !el.sheet.classList.contains('is-open')) return;
  el.sheet.classList.remove('is-open');
  el.scrim.classList.add('is-leaving');   // фон плавно светлеет вместе с уходом шторки
  state.sheetStack = [];
  state.sheetSpec = null;
  clearTimeout(sheetHideTimer);
  // прячем после завершения перехода (0,5 с), а не обрывая его на середине
  sheetHideTimer = setTimeout(() => {
    if (el.sheet.classList.contains('is-open')) return;
    el.sheet.hidden = true;
    el.scrim.hidden = true;
    el.scrim.classList.remove('is-leaving');
  }, 520);
}

// Смахивание шторки вниз за «ручку» или заголовок.
(() => {
  let startY = null; let dy = 0;
  const head = [el.sheet.querySelector('.sheet__grab'), el.sheet.querySelector('.sheet__head')];
  const down = (e) => {
    if (window.innerWidth >= 820 || e.target.closest('button')) return;
    startY = e.clientY; dy = 0;
    el.sheet.classList.add('is-dragging');
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const move = (e) => {
    if (startY == null) return;
    dy = Math.max(0, e.clientY - startY);
    el.sheet.style.transform = `translateY(${dy}px)`;
  };
  const up = () => {
    if (startY == null) return;
    startY = null;
    el.sheet.classList.remove('is-dragging');
    el.sheet.style.transform = '';
    if (dy > 110) closeSheet();
  };
  for (const h of head) {
    h.addEventListener('pointerdown', down);
    h.addEventListener('pointermove', move);
    h.addEventListener('pointerup', up);
    h.addEventListener('pointercancel', up);
  }
})();

// ---------------------------------------------------------------- общие блоки

export const statusPill = (level, label, cls = '') => `<span class="status ${cls}" data-level="${level ?? ''}">${esc(label ?? LEVEL[level])}</span>`;

export function compRow(x, child = false) {
  const lvl = child || x.kind !== 'assembly' ? x.level : x.rollup_level;
  const reasons = child || x.kind !== 'assembly' ? x.reasons : x.rollup_reasons;
  const sub = reasons[0]?.text || (lvl == null ? 'Нет данных для оценки' : 'Отклонений по данным нет');
  return `<button class="row row--btn ${child ? 'row--child' : ''}" data-action="comp" data-id="${esc(x.id)}">
    <span class="dot" data-level="${lvl ?? ''}"></span>
    <span class="row__main"><span class="row__title">${esc(x.name)}</span><span class="row__sub">${esc(sub)}</span></span>
    ${ic('chevR', 'row__chev')}</button>`;
}

export function rangeBar(p) {
  const vals = [p.value, p.nominal, p.warn_low, p.warn_high, p.alarm_low, p.alarm_high].filter((v) => v != null);
  if (p.value == null || !vals.length) return '';
  let lo = Math.min(...vals); let hi = Math.max(...vals);
  const span = hi - lo || Math.abs(hi) || 1;
  lo -= span * 0.18; hi += span * 0.18;
  if (p.warn_low == null && p.alarm_low == null && Math.min(...vals) >= 0) lo = Math.max(0, lo);
  const pos = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
  const G = 'var(--green)'; const O = 'var(--orange)'; const R = 'var(--red)';
  const seg = []; let cur = 0;
  const add = (to, color) => { if (to > cur) { seg.push(`${color} ${cur.toFixed(1)}% ${to.toFixed(1)}%`); cur = to; } };
  if (p.alarm_low != null) add(pos(p.alarm_low), R);
  if (p.warn_low != null) add(pos(p.warn_low), O);
  add(p.warn_high != null ? pos(p.warn_high) : p.alarm_high != null ? pos(p.alarm_high) : 100, G);
  if (p.warn_high != null) add(p.alarm_high != null ? pos(p.alarm_high) : 100, O);
  add(100, R);
  return `<div class="range" style="--zones: linear-gradient(90deg, ${seg.join(', ')})"><i style="left:${pos(p.value)}%"></i></div>`;
}

export function paramRow(p) {
  const lvl = p.level;
  const limits = [
    p.warn_low != null ? `от ${fmtNum(p.warn_low)}` : '', p.warn_high != null ? `до ${fmtNum(p.warn_high)}` : '',
  ].filter(Boolean).join(' ');
  return `<div class="row row--top" data-level="${lvl}">
    <span class="row__main"><span class="row__title">${esc(p.name)}</span>
      <span class="row__sub">${p.status === 'normal' && limits ? `В пределах уставки ${esc(limits)}` : esc(p.status_text)}</span>
      ${rangeBar(p)}</span>
    <span class="value">${fmtNum(p.value)} <small>${esc(p.unit || '')}</small></span></div>`;
}

export function historyRows(events, withComp = true) {
  if (!events?.length) return '<div class="group"><div class="row muted">Записей пока нет</div></div>';
  return `<div class="group">${events.slice(0, 30).map((h) => {
    const t = EVENT[h.type] || { label: h.type, icon: 'info', tone: 'gray' };
    return `<div class="row row--top"><span class="row__icon tone-${t.tone}">${ic(t.icon)}</span>
      <span class="row__main"><span class="row__title">${esc(t.label)}${withComp && h.component_name ? ` · ${esc(h.component_name)}` : ''}</span>
      <span class="row__sub">${esc(h.note || '')}</span>
      <span class="row__sub" style="color:var(--text-3)">${fmtDate(h.created_at)}${h.author ? ` · ${esc(h.author)}` : ''}</span>
      ${h.type === 'inspection' ? `<button class="btn-sm" data-action="report" data-id="${h.id}">Отчет и подтверждение</button>` : ''}</span></div>`;
  }).join('')}</div>`;
}

export function disc(title, icon, tone, body, { open = false, meta = '' } = {}) {
  return `<details class="disc" ${open ? 'open' : ''}><summary><span class="row__icon tone-${tone}">${ic(icon)}</span>
    <span class="disc__title">${title}</span>${meta ? `<span class="disc__meta">${meta}</span>` : ''}${ic('chevR', 'disc__chev')}</summary>
    <div class="disc__body">${body}</div></details>`;
}
