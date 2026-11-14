// Общие мелочи: форматирование, вибрация, тексты ошибок, значки, подписи статусов.

export const $ = (s, root = document) => root.querySelector(s);
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const fmtDate = (d) => (d ? new Date(d.length <= 10 ? `${d}T00:00:00` : d).toLocaleDateString('ru-RU') : '—');
export const fmtNum = (v, unit = '') => (v == null ? '—' : `${Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 2 })}${unit ? ` ${unit}` : ''}`);
export const pct = (x) => (x == null ? '—' : `${Math.round(x * 100)}%`);
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
/** Вибрация (Android): в шумном цеху звук не услышать, а событие должно быть заметно. */
export const buzz = (pattern) => { try { navigator.vibrate?.(pattern); } catch { /* не поддерживается */ } };
/** Технические ошибки сервера → понятный текст и следующий шаг. */
export function humanError(e, what = 'action') {
  const msg = String(e?.message || e || '');
  if (e instanceof TypeError || /Failed to fetch|NetworkError|Load failed/i.test(msg)) {
    return 'Нет связи с сервером. Проверьте, что телефон в той же Wi-Fi-сети.';
  }
  if (/модел|LM Studio|connection|недоступ|503/i.test(msg)) {
    return what === 'identify'
      ? 'ИИ сейчас недоступен. Выберите оборудование из списка.'
      : 'ИИ сейчас недоступен — анализ не выполнен. Ресурс, ТО и параметры в карточке доступны.';
  }
  return msg || 'Что-то пошло не так. Повторите ещё раз.';
}
export const plural = (n, one, few, many) => {
  const m10 = n % 10; const m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
};

export const LEVEL = { 0: 'Норма', 1: 'Требует внимания', 2: 'Требует действий', null: 'Нет данных' };
export const VISUAL = { ok: 'Видимых проблем нет', check: 'Рекомендуется проверка', defect: 'Признаки возможного дефекта' };
export const VISUAL_LEVEL = { ok: 0, check: 1, defect: 2 };
export const SOURCE_ICON = { visual: 'eye', resource: 'clock', maintenance: 'wrench', parameter: 'gauge', defect: 'flag', combined: 'sparkles' };
export const EVENT = {
  operation: { label: 'Режим работы', icon: 'clock', tone: 'blue' },
  configuration: { label: 'Нормативы изменены', icon: 'wrench', tone: 'blue' },
  approval: { label: 'Подтверждено мастером', icon: 'check', tone: 'green' },
  checked: { label: 'Проверено', icon: 'check', tone: 'green' },
  defect: { label: 'Замечание', icon: 'flag', tone: 'orange' },
  maintenance: { label: 'Обслуживание', icon: 'wrench', tone: 'blue' },
  replacement: { label: 'Замена', icon: 'swap', tone: 'purple' },
  resolved: { label: 'Замечание устранено', icon: 'check', tone: 'green' },
  inspection: { label: 'Осмотр сохранён', icon: 'save', tone: 'blue' },
};

// Линейные иконки в стиле SF Symbols (24×24).
export const ICON = {
  chevL: '<path d="M15 5l-7 7 7 7"/>',
  chevR: '<path d="M9 5l7 7-7 7"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  scan: '<path d="M4 9V6.5A2.5 2.5 0 0 1 6.5 4H9M15 4h2.5A2.5 2.5 0 0 1 20 6.5V9M20 15v2.5a2.5 2.5 0 0 1-2.5 2.5H15M9 20H6.5A2.5 2.5 0 0 1 4 17.5V15"/><path d="M8 12h8"/>',
  sparkles: '<path d="M11 3.5l1.7 4.6 4.6 1.7-4.6 1.7L11 16.1l-1.7-4.6-4.6-1.7 4.6-1.7z"/><path d="M18 14.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/>',
  photo: '<rect x="3" y="5" width="18" height="14" rx="3.5"/><circle cx="9" cy="10" r="1.7"/><path d="M21 15.5l-5.2-5.2L7 19"/>',
  camera: '<path d="M8 6l1.5-2h5L16 6h3a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z"/><circle cx="12" cy="13" r="4"/>',
  doc: '<rect x="5" y="3" width="14" height="18" rx="3.5"/><path d="M9 8.5h6M9 12h6M9 15.5h3.5"/>',
  list: '<path d="M9.5 6.5h10M9.5 12h10M9.5 17.5h10"/><circle cx="5" cy="6.5" r=".9"/><circle cx="5" cy="12" r=".9"/><circle cx="5" cy="17.5" r=".9"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  wrench: '<path d="M14.6 6.4a4.2 4.2 0 0 0-5.5 5.4L4 16.9V20h3.1l5.1-5.1a4.2 4.2 0 0 0 5.4-5.5l-2.5 2.5-2.4-.6-.6-2.4z"/>',
  gauge: '<path d="M4.2 16.5a8.5 8.5 0 1 1 15.6 0"/><path d="M12 15l3.5-5"/><circle cx="12" cy="15" r="1"/>',
  flag: '<path d="M5.5 21V4.5M5.5 4.5h11.5l-2.3 4.2 2.3 4.3H5.5"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.8v.2"/>',
  warn: '<path d="M12 4l9 15.5H3z"/><path d="M12 10v4.5M12 17.2v.2"/>',
  bolt: '<path d="M13 3L5.5 13.5H11L10 21l7.5-10.5H12z"/>',
  save: '<path d="M12 3.5v11M7.5 10L12 14.5 16.5 10"/><path d="M4 15.5v2a2.5 2.5 0 0 0 2.5 2.5h11a2.5 2.5 0 0 0 2.5-2.5v-2"/>',
  swap: '<path d="M4 8.5h14l-3.5-3.5M20 15.5H6l3.5 3.5"/>',
  more: '<circle cx="6" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18" cy="12" r="1.2"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
  camoff: '<path d="M3 3l18 18"/><path d="M9.5 5h5l1.6 2H19a2 2 0 0 1 2 2v7.5M17.5 19H5a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2h2"/><path d="M9.5 10.5a3 3 0 0 0 4 4"/>',
  qr: '<rect x="4" y="4" width="6" height="6" rx="1.2"/><rect x="14" y="4" width="6" height="6" rx="1.2"/><rect x="4" y="14" width="6" height="6" rx="1.2"/><path d="M14 14h2.5v2.5M20 14v6h-6M14 18v2"/>',
  cube: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9"/>',
  server: '<rect x="4" y="4" width="16" height="7" rx="2"/><rect x="4" y="13" width="16" height="7" rx="2"/><path d="M8 7.5h.01M8 16.5h.01"/>',
  phone: '<rect x="7" y="3" width="10" height="18" rx="2.5"/><path d="M11 17.5h2"/>',
  history: '<path d="M4 12a8 8 0 1 0 2.4-5.7L4 8.5"/><path d="M4 4v4.5h4.5M12 8v4l3 2"/>',
  retake: '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3L4.5 9"/><path d="M4.5 4.5V9H9"/><circle cx="12" cy="12" r="2.6"/>',
};
export const ic = (name, cls = '') => `<svg class="ic ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[name] || ''}</svg>`;
