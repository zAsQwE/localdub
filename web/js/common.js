// Общие мелочи: строки интерфейса, запросы, отчёт об ошибках.

export const S = {};

export function setStrings(strings) {
  Object.assign(S, strings || {});
}

export function t(key, ...args) {
  let value = S[key] ?? key;
  for (const arg of args) value = value.replace('%s', arg);
  return value;
}

export function applyStrings(root = document) {
  for (const el of root.querySelectorAll('[data-i18n]')) {
    el.textContent = t(el.dataset.i18n);
  }
  for (const el of root.querySelectorAll('[data-i18n-ph]')) {
    el.placeholder = t(el.dataset.i18nPh);
  }
}

export async function post(path, body, hostKey) {
  const headers = { 'Content-Type': 'application/json' };
  if (hostKey) headers['X-Host-Key'] = hostKey;
  try {
    const res = await fetch(path, {
      method: 'POST',
      headers,
      body: JSON.stringify(body || {}),
    });
    return await res.json();
  } catch {
    return { ok: false, error: 'err_generic' };
  }
}

export function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

// Страница живёт в окне приложения, где консоль недоступна.
// Ошибки уезжают на сервер, чтобы их было видно в его логе.
//
// Здесь важна осторожность: если сообщать о сбоях сети, получается вечный цикл.
// Отказ fetch порождает unhandledrejection, тот пытается доложить о себе тем же
// fetch — и так до бесконечности. Поэтому сетевые ошибки не отправляем вовсе,
// повторы отбрасываем, число сообщений ограничено, а сам fetch гасит отказ.
const NETWORK_NOISE = /NetworkError|Failed to fetch|Load failed|NetworkTimeout|aborted/i;
const REPORT_LIMIT = 20;

export function reportErrors(where) {
  let count = 0;
  let previous = '';
  let busy = false;

  const send = (text) => {
    text = String(text || '');
    if (!text || busy || count >= REPORT_LIMIT) return;
    if (text === previous) return;
    if (NETWORK_NOISE.test(text)) return;

    previous = text;
    count += 1;
    busy = true;
    fetch('/api/log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ where, text }),
    }).catch(() => {}).then(() => { busy = false; });
  };

  window.addEventListener('error', (e) => {
    send(`${e.message} @ ${e.filename}:${e.lineno}`);
  });
  window.addEventListener('unhandledrejection', (e) => {
    send(`unhandled: ${e.reason?.message || e.reason}`);
  });
  return send;
}
