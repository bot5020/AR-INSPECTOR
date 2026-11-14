// Камера, снимок, масштаб и жесты, метки поверх изображения.
import { fitRect, limitView, zoomView, outlineSegments, layoutLabels, calloutLeader } from '../view-geometry.mjs';
import { openItem } from './component.js';
import { renderChrome } from './screen.js';
import { el, state } from './state.js';
import { closeSheet, openSheet, paintSheet } from './ui.js';
import { $, esc, ic } from './util.js';

// ---------------------------------------------------------------- камера и кадры

export async function startCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    state.cameraError = window.isSecureContext
      ? 'Браузер не даёт доступ к камере.'
      : `Камера работает только по HTTPS. Откройте https://${location.hostname}:8080/`;
    renderChrome();
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false,
    });
    el.video.srcObject = stream;
    await el.video.play();
    state.camera = true;
  } catch (e) {
    state.cameraError = e.name === 'NotAllowedError'
      ? 'Доступ к камере запрещён. Разрешите его в настройках сайта.'
      : 'Камера не найдена на этом устройстве.';
  }
  renderChrome();
}

export function mediaSize() {
  if (state.frozen) return state.stillSize;
  if (el.video.videoWidth) return { w: el.video.videoWidth, h: el.video.videoHeight };
  return null;
}

/** Прямоугольник изображения на экране (object-fit: contain). */
export function contentRect() {
  const size = mediaSize();
  if (!size) return null;
  const st = el.stage.getBoundingClientRect();
  return fitRect(size, { w: st.width, h: st.height }, state.frozen ? state.view : undefined);
}

export async function captureBlob(maxSide = 1600, quality = 0.85) {
  const src = state.frozen ? el.still : el.video;
  const w0 = state.frozen ? el.still.naturalWidth : el.video.videoWidth;
  const h0 = state.frozen ? el.still.naturalHeight : el.video.videoHeight;
  if (!w0) throw new Error('Нет изображения');
  const s = Math.min(1, maxSide / Math.max(w0, h0));
  const c = document.createElement('canvas');
  c.width = Math.round(w0 * s); c.height = Math.round(h0 * s);
  c.getContext('2d').drawImage(src, 0, 0, c.width, c.height);
  return new Promise((res) => c.toBlob(res, 'image/jpeg', quality));
}

export function freeze(blob, { revision = ++state.frameRevision } = {}) {
  if (revision !== state.frameRevision) return Promise.resolve(false);
  const previous = { analysis: state.analysis, view: state.view, size: state.stillSize, frozen: state.frozen, url: state.stillUrl };
  state.frameLoading = true; state.analysis = null; state.focusReturn = null; state.focusedKey = null;
  el.still.hidden = true; state.hints = [];
  renderOverlay(); renderChrome();
  const url = URL.createObjectURL(blob), image = new Image();
  return new Promise((resolve, reject) => {
    image.onload = async () => {
      if (revision !== state.frameRevision) { URL.revokeObjectURL(url); return resolve(false); }
      try {
        el.still.src = url;
        if (el.still.decode) await el.still.decode();
        if (revision !== state.frameRevision) { URL.revokeObjectURL(url); return resolve(false); }
        if (previous.url) URL.revokeObjectURL(previous.url);
        state.stillUrl = url; state.stillSize = { w: image.naturalWidth, h: image.naturalHeight };
        state.frozen = true; state.frameLoading = false; state.view = { scale: 1, x: 0, y: 0 };
        state.marksVisible = true; state.focusedKey = null;
        applyView(); el.still.hidden = false; el.video.hidden = true;
        renderOverlay(); renderChrome(); resolve(true);
      } catch (error) { image.onerror(error); }
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      if (revision !== state.frameRevision) return resolve(false);
      state.frameLoading = false; state.analysis = previous.analysis; state.view = previous.view;
      state.stillSize = previous.size; state.frozen = previous.frozen;
      if (previous.url) { el.still.src = previous.url; el.still.hidden = !previous.frozen; }
      renderChrome(); reject(new Error('Не удалось открыть снимок. Выберите JPEG, PNG или WebP.'));
    };
    image.src = url;
  });
}

export function unfreeze() {
  state.frameRevision++; state.frameLoading = false; state.focusReturn = null; state.focusedKey = null;
  state.frameSource = 'camera';
  state.frozen = false;
  state.analysis = null;
  state.view = { scale: 1, x: 0, y: 0 };
  state.controlsCollapsed = false;
  applyView();
  state.shownKeys.clear();
  state.hintHidden = false;
  el.still.hidden = true;
  el.video.hidden = false;
  state.hints = [];
  renderOverlay();
  renderChrome();
}

// ---------------------------------------------------------------- рамки поверх изображения

export function viewportSize() {
  return { w: el.stage.clientWidth, h: el.stage.clientHeight };
}

export function applyView() {
  const v = state.view;
  el.still.style.transform = `translate(${v.x}px, ${v.y}px) scale(${v.scale})`;
  el.stage.dataset.frozen = state.frozen ? '1' : '0';
  el.stage.dataset.zoomed = v.scale > 1 ? '1' : '0';
  el.zoomReset.textContent = `${Number(v.scale.toFixed(1))}×`;
  el.photoTools.querySelector('[data-action="zoom-out"]').disabled = v.scale <= 1;
  el.photoTools.querySelector('[data-action="zoom-in"]').disabled = v.scale >= 4;
}

export function setZoom(scale, point = { x: el.stage.clientWidth / 2, y: el.stage.clientHeight / 2 }) {
  if (!state.frozen || state.analysis?.running || state.identifying) return;
  state.view = zoomView(state.view, scale, point, state.stillSize, viewportSize());
  applyView(); renderOverlay();
}

export function focusItem(key, saved = null) {
  const item = state.analysis?.items.find(it => it.key === key);
  if (!item) return;
  const snapshot = saved || { view: { ...state.view }, marksVisible: state.marksVisible, hintHidden: state.hintHidden,
    analysisId: state.analysis.id, spec: state.sheetSpec ? { ...state.sheetSpec, scroll: el.sheetBody.scrollTop } : null,
    stack: [...state.sheetStack] };
  closeSheet();
  state.focusReturn = snapshot;
  const b = item.box;
  const base = fitRect(state.stillSize, viewportSize());
  const scale = Math.min(4, Math.max(1.5, .65 * Math.min(el.stage.clientWidth / ((b[2] - b[0]) * base.w), el.stage.clientHeight / ((b[3] - b[1]) * base.h))));
  state.view = limitView({ scale, x: (.5 - (b[0] + b[2]) / 2) * base.w * scale,
    y: (.5 - (b[1] + b[3]) / 2) * base.h * scale }, state.stillSize, viewportSize());
  state.focusedKey = key;
  state.hintHidden = true; state.marksVisible = true; state.controlsCollapsed = true;
  applyView(); renderChrome();
}

export const pointers = new Map();
export let gesture = null, suppressPhotoClickUntil = 0, draggedPhoto = false;
export function beginGesture() {
  const p = [...pointers.values()];
  const midpoint = p.length > 1 ? { x: (p[0].x + p[1].x) / 2, y: (p[0].y + p[1].y) / 2 } : p[0];
  gesture = { view: { ...state.view }, midpoint, distance: p.length > 1 ? Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y) : 0 };
}
el.stage.addEventListener('pointerdown', e => {
  if (!state.frozen || state.analysis?.running || state.identifying || (e.pointerType === 'mouse' && e.button !== 0)) return;
  if (!pointers.size) draggedPhoto = false;
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  beginGesture();
});
el.stage.addEventListener('pointermove', e => {
  if (!pointers.has(e.pointerId) || !gesture) return;
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  const p = [...pointers.values()];
  const mid = p.length > 1 ? { x: (p[0].x + p[1].x) / 2, y: (p[0].y + p[1].y) / 2 } : p[0];
  let v = { ...gesture.view };
  if (p.length > 1 && gesture.distance > 0) {
    const distance = Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y);
    v = zoomView(v, v.scale * distance / gesture.distance, gesture.midpoint, state.stillSize, viewportSize());
  }
  const dx = mid.x - gesture.midpoint.x, dy = mid.y - gesture.midpoint.y;
  if (p.length === 1 && Math.hypot(dx, dy) <= 5) return;
  if (Math.hypot(dx, dy) > 5 || p.length > 1) {
    draggedPhoto = true;
    suppressPhotoClickUntil = performance.now() + 350;
    el.stage.setPointerCapture(e.pointerId);
  }
  state.view = limitView({ ...v, x: v.x + dx, y: v.y + dy }, state.stillSize, viewportSize());
  applyView(); renderOverlay();
});
for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) el.stage.addEventListener(name, e => {
  if (draggedPhoto) suppressPhotoClickUntil = performance.now() + 350;
  pointers.delete(e.pointerId);
  if (pointers.size) beginGesture(); else gesture = null;
});
el.stage.addEventListener('click', e => {
  if (performance.now() < suppressPhotoClickUntil) { e.preventDefault(); e.stopPropagation(); }
}, true);
el.stage.addEventListener('dblclick', e => {
  if (e.target.closest('.tag')) return;
  setZoom(state.view.scale > 1 ? 1 : 2, { x: e.clientX, y: e.clientY });
});
el.stage.addEventListener('wheel', e => {
  if (!state.frozen || state.analysis?.running || state.identifying) return;
  e.preventDefault();
  setZoom(state.view.scale * Math.exp(-e.deltaY * .002), { x: e.clientX, y: e.clientY });
}, { passive: false });

export const area = (b) => (b[2] - b[0]) * (b[3] - b[1]);

export function renderOverlay() {
  const r = contentRect();
  el.overlay.innerHTML = '';
  if (state.frameLoading) return;
  const scanning = !!(state.frozen && state.analysis?.running);
  el.scanline.hidden = !scanning || !r;
  if (scanning) el.stage.dataset.scanning = '1'; else delete el.stage.dataset.scanning;
  if (!r) return;
  if (scanning) Object.assign(el.scanline.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` });

  const place = (box, cls, tag, level, onClick) => {
    const [x1, y1, x2, y2] = box;
    const d = document.createElement('div');
    d.className = `box ${cls}`;
    if (level != null) d.dataset.level = level;
    Object.assign(d.style, {
      left: `${r.x + x1 * r.w}px`, top: `${r.y + y1 * r.h}px`,
      width: `${(x2 - x1) * r.w}px`, height: `${(y2 - y1) * r.h}px`,
    });
    if (!cls.includes('box--analysis') && y1 * r.h < 40) d.classList.add('box--below');
    if (tag) d.innerHTML = onClick ? `<button type="button" class="tag">${tag}</button>` : `<div class="tag">${tag}</div>`;
    if (onClick) {
      d.addEventListener('click', onClick);
    }
    el.overlay.appendChild(d);
  };

  if (state.frozen && state.analysis && state.marksVisible) {
    const all = state.analysis.items;
    const items = all.filter(it => !state.focusedKey || it.key === state.focusedKey);
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.classList.add('annotation-outlines');
    svg.setAttribute('width', el.stage.clientWidth); svg.setAttribute('height', el.stage.clientHeight);
    const groups = new Map();
    for (const it of items) {
      const id = it.component_id || it.key;
      if (!groups.has(id)) groups.set(id, []);
      groups.get(id).push(it);
    }
    const selectedKey = state.focusedKey || (state.selectedObservation && state.analysis.id && state.selectedObservation.analysis_id === state.analysis.id ? state.selectedObservation.key : null);
    svg.dataset.selection = selectedKey ? '1' : '0';
    for (const group of groups.values()) {
      const selected = group.some(it => it.key === selectedKey);
      if (selected) for (const it of group) {
        const rect = document.createElementNS(svg.namespaceURI, 'rect');
        rect.classList.add('annotation-fill'); rect.dataset.level = it.level ?? '';
        rect.setAttribute('x', r.x+it.box[0]*r.w); rect.setAttribute('y', r.y+it.box[1]*r.h);
        rect.setAttribute('width', (it.box[2]-it.box[0])*r.w); rect.setAttribute('height', (it.box[3]-it.box[1])*r.h);
        svg.appendChild(rect);
      }
      const path = document.createElementNS(svg.namespaceURI, 'path');
      path.dataset.level = group[0].level ?? '';
      path.dataset.selected = selected ? '1' : '0';
      path.setAttribute('d', outlineSegments(group.map(it => it.box)).map(([x1,y1,x2,y2]) => `M${r.x+x1*r.w},${r.y+y1*r.h}L${r.x+x2*r.w},${r.y+y2*r.h}`).join(''));
      svg.appendChild(path);
    }
    el.overlay.appendChild(svg);
    const bounds = labelBounds();
    const measure = document.createElement('canvas').getContext('2d');
    measure.font = '600 14px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
    const labels = items.map(it => {
      const c = state.card?.components.find(c => c.id === it.component_id);
      const title = c?.name || it.name;
      const short = it.key === selectedKey ? title : shortName(title);
      return { it, title, short, number: all.indexOf(it)+1,
        w: 76 + measure.measureText(short).width,
        box: [r.x+it.box[0]*r.w, r.y+it.box[1]*r.h, r.x+it.box[2]*r.w, r.y+it.box[3]*r.h] };
    }).filter(l => l.box[0] < el.stage.clientWidth && l.box[2] > 0 && l.box[1] < el.stage.clientHeight && l.box[3] > 0);
    for (const l of layoutLabels(labels, bounds)) {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'annotation-label'; b.dataset.level = l.it.level ?? '';
      b.setAttribute('aria-label', `${l.number}. ${l.title}`);
      b.title = l.title;
      b.dataset.selected = l.it.key === selectedKey ? '1' : '0';
      b.innerHTML = `<span class="annotation-number">${l.number}</span>${l.numbered ? '' : `<b>${esc(l.short)}</b>`}${l.numbered ? '' : l.it.verdict === 'confirmed' ? ic('flag') : l.it.verdict === 'rejected' ? ic('check') : ''}`;
      Object.assign(b.style, { left: `${l.x}px`, top: `${l.y}px`, width: `${l.w}px`, height: `${l.h}px` });
      b.onclick = () => openItem(l.it);
      const leader = document.createElementNS(svg.namespaceURI, 'path');
      leader.dataset.level = l.it.level ?? '';
      const [sx,sy,ex,ey] = calloutLeader(l);
      leader.classList.add('annotation-leader');
      leader.setAttribute('d', `M${sx},${sy}L${ex},${ey}`);
      const dot = document.createElementNS(svg.namespaceURI, 'circle');
      dot.dataset.level = l.it.level ?? '';
      dot.setAttribute('cx',ex); dot.setAttribute('cy',ey); dot.setAttribute('r',3);
      dot.setAttribute('fill','var(--c)');
      svg.appendChild(dot);
      svg.appendChild(leader); el.overlay.appendChild(b);
    }
  } else if (!state.frozen) {
    for (const h of state.hints.slice(0, 4)) place(h.box, 'box--hint', `${esc(h.label)}`, null, null);
  }
}
window.addEventListener('resize', () => {
  if (state.frozen) state.view = limitView(state.view, state.stillSize, viewportSize());
  applyView(); renderOverlay();
});

export function shortName(name) {
  const names = [['электродвигатель','Двигатель'],['муфта','Муфта'],['фланц','Фланец'],['уплотнение','Уплотнение'],['задвижка','Задвижка'],['манометр','Манометр'],['подшипник','Подшипник'],['насос','Насос']];
  return names.find(([part]) => name.toLowerCase().includes(part))?.[1] || name.split(' (')[0];
}
function labelBounds() {
  const st = el.stage.getBoundingClientRect();
  const visible = elements => elements.filter(e => e && !e.hidden && e.getClientRects().length);
  const top = Math.max(st.top, ...visible([document.querySelector('.top'), el.sessionInfo, el.hint]).map(e => e.getBoundingClientRect().bottom)) - st.top + 8;
  const bottom = Math.min(st.bottom, ...visible([el.workbar, el.focusBar, el.photoTools, el.captureAction, el.progress]).map(e => e.getBoundingClientRect().top)) - st.top - 8;
  return { left: 8, right: st.width - 8, top, bottom };
}
export function wholePhoto() {
  state.focusedKey = null; state.view = { scale: 1, x: 0, y: 0 }; applyView(); renderChrome();
}
export async function previewObservation(aid, key) {
  if (state.frameLoading || state.analysis?.running) return;
  const snapshot = { view: { ...state.view }, marksVisible: state.marksVisible, hintHidden: state.hintHidden,
    spec: state.sheetSpec ? { ...state.sheetSpec, scroll: el.sheetBody.scrollTop } : null, stack: [...state.sheetStack] };
  if (aid !== state.analysis?.id) {
    const revision = state.frameRevision;
    const origin = { analysis: state.analysis, frameSource: state.frameSource, frozen: state.frozen,
      blob: state.frozen && state.stillUrl ? await (await fetch(state.stillUrl)).blob() : null };
    if (revision !== state.frameRevision) return;
    const { reopenAnalysis } = await import('./system.js');
    if (!await reopenAnalysis(aid)) return;
    snapshot.origin = origin;
  }
  snapshot.analysisId = aid;
  focusItem(key, snapshot);
}
export async function returnFromFocus() {
  const saved = state.focusReturn;
  if (!saved || saved.analysisId !== state.analysis?.id) return;
  if (saved.origin) {
    if (saved.origin.blob) await freeze(saved.origin.blob);
    else unfreeze();
    state.analysis = saved.origin.analysis; state.frameSource = saved.origin.frameSource;
  }
  state.focusReturn = null; state.focusedKey = null;
  state.view = limitView(saved.view, state.stillSize, viewportSize());
  state.marksVisible = saved.marksVisible; state.hintHidden = saved.hintHidden;
  applyView(); renderChrome();
  if (saved.spec) {
    const spec = saved.spec.rebuild ? { ...saved.spec.rebuild(), scroll: saved.spec.scroll } : saved.spec;
    openSheet(spec); state.sheetStack = saved.stack; paintSheet(spec);
  }
}
