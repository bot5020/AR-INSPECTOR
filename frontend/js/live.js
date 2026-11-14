// Живой режим: QR-метки и быстрый детектор по кадрам камеры.
import { runAnalysis } from './analysis.js';
import { area, captureBlob, renderOverlay } from './camera.js';
import { selectEquipment } from './equipment.js';
import { api, postJson } from './net.js';
import { el, state } from './state.js';
import { toast } from './ui.js';
import { buzz, sleep } from './util.js';

// ---------------------------------------------------------------- живой режим: QR + YOLO

export let barcode = null;
if ('BarcodeDetector' in window) {
  try { barcode = new window.BarcodeDetector({ formats: ['qr_code'] }); } catch { barcode = null; }
}

export async function qrLoop() {
  for (;;) {
    await sleep(barcode ? 350 : 1400);
    if (!state.user || state.eqId || state.frozen || !state.camera || document.hidden || !el.sheet.hidden) continue;
    try {
      if (barcode) {
        const code = (await barcode.detect(el.video))[0]?.rawValue;
        if (!code) continue;
        const res = await postJson('/api/resolve', { code, source: 'qr' }).catch(() => null);
        if (res?.equipment_id) { buzz(40); await selectEquipment(res.equipment_id, 'по QR-коду'); }
        else { buzz([80, 60, 80]); toast('Этого QR-кода нет в базе оборудования', 'warn'); }
      } else {
        const blob = await captureBlob(960, 0.8);
        const res = await api('/api/qr', { method: 'POST', body: blob, headers: { 'Content-Type': 'image/jpeg' } });
        if (res.equipment_id) { buzz(40); await selectEquipment(res.equipment_id, 'по QR-коду'); }
      }
    } catch { /* следующий кадр */ }
  }
}

export async function detectLoop() {
  for (;;) {
    await sleep(300);
    if (!state.user || state.frozen || !state.camera || document.hidden || !state.status?.detector?.ready) continue;
    try {
      const blob = await captureBlob(640, 0.7);
      const res = await api('/api/detect', { method: 'POST', body: blob, headers: { 'Content-Type': 'image/jpeg' } });
      if (state.frozen) continue;
      state.hints = res.detections.filter((d) => d.conf >= 0.12);
      renderOverlay();
      checkAuto(res.detections.filter((d) => d.main && d.conf >= 0.12));
    } catch { await sleep(1500); }
  }
}

export const iou = (a, b) => {
  const ix = Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0]));
  const iy = Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
  const i = ix * iy; const u = area(a) + area(b) - i;
  return u > 0 ? i / u : 0;
};

/** Автоанализ: основной объект стабильно в кадре ~1 с — запускаем ИИ-осмотр. */
export function checkAuto(main) {
  if (!state.auto || !state.eqId || state.analysis?.running) return;
  const best = main.sort((a, b) => b.conf - a.conf)[0];
  if (!best) { state.autoStable = 0; state.lastMain = null; return; }
  state.autoStable = state.lastMain && iou(state.lastMain, best.box) > 0.75 ? state.autoStable + 1 : 0;
  state.lastMain = best.box;
  if (state.autoStable >= 4 && Date.now() - state.lastAuto > 20000) {
    state.lastAuto = Date.now();
    state.autoStable = 0;
    runAnalysis();
  }
}
