// Лаунчер: одна кнопка, настройки голоса и языка, адрес для телефонов.
import { applyStrings, post, setStrings, t, reportErrors } from './common.js';

const KEY = window.HOST_KEY;
const $ = (id) => document.getElementById(id);
const log = reportErrors('launcher');

let state = null;
let offline = false;
let savedTimer = null;
let poll = null;
let busy = false;
let copyTimer = null;

/* ---------- отрисовка ---------- */

function render(next) {
  state = next;
  setStrings(next.strings);
  document.documentElement.lang = next.settings.lang;
  applyStrings();

  renderMain(next);
  renderSettings(next);
}

function renderMain(s) {
  const btn = $('startBtn');
  const blocked = !s.game_ok;

  btn.classList.toggle('running', s.session);
  btn.textContent = busy ? t('starting') : s.session ? t('stop') : t('start');
  btn.disabled = blocked || busy;
  $('sessionBadge').textContent = busy ? t('session_starting') : s.session ? t('session_running') : t('session_stopped');
  $('sessionBadge').classList.toggle('running', s.session);
  $('heroTitle').textContent = s.session ? t('hero_running') : t('hero_idle');
  $('heroSubtitle').textContent = s.session ? t('hero_live_hint') : t('hero_hint');

  // Адрес держится, пока сессию не остановили: закрытая игра его не убирает.
  // Первый запуск идёт по шагам: сначала где игра, потом патч, потом Start.
  const needGame = !s.game_ok;
  const needsPatch = s.game_ok && s.patch !== 'on';
  $('findBox').classList.toggle('hidden', !needGame || s.session);
  $('installBox').classList.toggle('hidden', !needsPatch || s.session);
  $('findBrowse').classList.toggle('hidden', !s.can_pick_folder);
  if (document.activeElement !== $('findPath')) $('findPath').value = s.settings.game_dir || '';

  const code = (s.relay || {}).code || s.settings.room_code || '';
  $('roomBox').classList.toggle('hidden', !code);
  $('roomCode').textContent = code;

  $('joinBox').classList.toggle('is-idle', !s.session);
  $('qrBox').classList.toggle('hidden', !s.session);
  $('copyAddress').disabled = !s.session;
  if (s.session && $('joinUrl').textContent !== s.join_url) {
    $('joinUrl').textContent = s.join_url;
    refreshQr(s.join_url);
  }
  if (!s.session) $('joinUrl').textContent = t('join_idle');

  renderNotice(s);

  const line = $('statusLine');
  line.classList.remove('bad');
  if (blocked) {
    // Про отсутствующую игру уже сказано карточкой выше — не дублируем.
    line.textContent = '';
  } else if (s.error) {
    line.textContent = s.error;
    line.classList.add('bad');
  } else if (s.session) {
    line.textContent = s.game_running ? t('running') : t('game_closed');
  } else {
    line.textContent = '';
  }
}

// Замечание про сборку игры: перевод от TDoT тоже подменяет Assembly-CSharp,
// поэтому важно вслух сказать, что именно сейчас установлено.
function renderNotice(s) {
  const game = s.game || {};
  const box = $('noticeLine');
  const flavour = (name) => t(name === 'ru' ? 'flavour_ru' : 'flavour_orig');
  let text = '';

  if (game.notice === 'flavour_changed') {
    text = t('notice_flavour_changed', flavour(game.base_flavour), flavour(game.flavour));
  } else if (game.notice === 'low_redirects') {
    text = t('notice_low_redirects', game.redirects);
  } else if (game.notice === 'no_backup') {
    text = t('notice_no_backup');
  } else if (game.notice === 'tts_port_changed') {
    text = t('notice_tts_port_changed');
  }

  box.textContent = text;
  box.classList.toggle('warn', Boolean(text) && game.notice !== 'needs_patch');
}

function renderSettings(s) {
  const cfg = s.settings;
  const piper = s.engines.piper || {};

  // Piper предлагается сразу после установки: модель можно указать здесь же.
  const options = [
    ['espeak', 'voice_espeak', s.engines.espeak?.available, s.engines.espeak?.status],
    ['piper', 'voice_piper', piper.installed, piper.status],
    ['none', 'voice_none', true, 'ok'],
  ];
  const suffix = (status) => {
    if (status === 'not_installed') return ' — ' + t('voice_missing');
    if (status === 'no_voice') return ' — ' + t('voice_no_model');
    return '';
  };
  const current = cfg.tts_engine === 'auto' ? s.active_voice : cfg.tts_engine;
  $('voiceSel').innerHTML = options.map(([value, key, on, status]) => `
    <option value="${value}" ${on ? '' : 'disabled'} ${value === current ? 'selected' : ''}
    >${t(key)}${suffix(status)}</option>`).join('');

  const showPiper = piper.installed && current === 'piper';
  $('piperRow').classList.toggle('hidden', !showPiper);
  if (showPiper) {
    if (document.activeElement !== $('piperDir')) $('piperDir').value = cfg.piper_dir || '';
    const using = piper.using ? piper.using.split('/').pop() : '';
    $('piperNote').textContent = using ? `${t('piper_using')}: ${using}` : t('piper_hint');
    // Модель ищется сама; если её всё же нет — скачиваем одной кнопкой.
    $('piperGetBtn').classList.toggle('hidden', Boolean(using));
  }

  // Что за система и чем запускать патчер.
  const sys = s.system || {};
  $('sysPill').textContent = `${sys.os || '?'} · Python ${sys.python || '?'}`;
  $('sysPill').className = `pill ${sys.dotnet ? 'ok' : 'bad'}`;
  if (document.activeElement !== $('dotnetPath')) $('dotnetPath').value = cfg.dotnet_path || '';
  $('sysNote').textContent = sys.dotnet ? `.NET: ${sys.dotnet}` : t('dotnet_hint');

  // В виртуальных сетях (Radmin, PortHole) нужный адрес — не тот, что выбрала
  // бы система, поэтому даём выбрать вручную.
  // В виртуальных сетях и туннелях адрес системе не известен, поэтому поле
  // свободное: найденные адреса лишь подсказка, вписать можно и host:port.
  const addresses = s.addresses || [];
  $('addrList').innerHTML = addresses.map((a) =>
    `<option value="${a.ip}">${a.iface && a.iface !== '?' ? a.iface : ''}</option>`).join('');
  if (document.activeElement !== $('addrInput')) $('addrInput').value = cfg.announce_ip || '';
  $('addrNote').textContent = cfg.announce_ip ? '' : t('address_hint');

  $('langSel').value = cfg.lang;
  if (document.activeElement !== $('roomInput')) $('roomInput').value = cfg.room_code || '';
  if (document.activeElement !== $('portInput')) $('portInput').value = cfg.port;
  if (document.activeElement !== $('gameDir')) $('gameDir').value = cfg.game_dir || '';

  $('gamePill').textContent = s.game_ok ? t('game_found') : t('game_missing');
  $('gamePill').className = `pill ${s.game_ok ? 'ok' : 'bad'}`;
  $('browseBtn').classList.toggle('hidden', !s.can_pick_folder);

  const game = s.game || {};
  $('flavourPill').textContent = game.flavour === 'ru' ? t('flavour_ru') : t('flavour_orig');
  $('flavourPill').className = `pill ${game.flavour === 'ru' ? 'ok' : ''}`;

  const on = s.patch === 'on';
  $('patchPill').textContent = on
    ? t(game.base_flavour === 'ru' ? 'patch_over_ru' : 'patch_over_orig')
    : t('patch_off');
  $('patchPill').className = `pill ${on ? 'ok' : 'bad'}`;
  $('patchBtn').textContent = on ? t('patch_remove') : t('patch_apply');
  $('patchBtn').disabled = !s.game_ok;
  $('patchNote').textContent = s.tools_missing.length
    ? 'нужно установить: ' + s.tools_missing.join(', ')
    : '';
  $('portNote').textContent = s.port_note || '';
}

async function refreshQr(url) {
  try {
    $('qrBox').innerHTML = await (await fetch(`/qr.svg?d=${encodeURIComponent(url)}`)).text();
  } catch {
    $('qrBox').innerHTML = '';
  }
}

/* ---------- действия ---------- */

async function reload() {
  try {
    render(await (await fetch('/api/state')).json());
    offline = false;
  } catch {
    // Сервер не отвечает — говорим об этом вслух и не заваливаем лог:
    // сообщать о сбое сети всё равно некому.
    if (!offline) {
      offline = true;
      const line = $('statusLine');
      line.textContent = t('app_offline');
      line.classList.add('bad');
    }
  }
}

async function save(changes) {
  const res = await post('/api/settings', changes, KEY);
  if (!res) return;
  // Сервер переехал на другой порт — уходим за ним, иначе страница ослепнет.
  if (res.redirect) {
    setTimeout(() => { location.href = res.redirect; }, 400);
    return;
  }
  if (res.state) render(res.state);
  else if (res.settings) render(res);
  if (res.ok === false && res.note) {
    ('room_code' in changes ? $('roomNote') : $('portNote')).textContent = res.note;
  } else {
    if ('room_code' in changes) $('roomNote').textContent = t('room_code_hint');
    flashSaved();
  }
}

function flashSaved() {
  $('savedNote').textContent = t('saved');
  $('savedNote').classList.add('show');
  clearTimeout(savedTimer);
  savedTimer = setTimeout(() => $('savedNote').classList.remove('show'), 1200);
}

async function onStart() {
  if (busy) return;
  const btn = $('startBtn');
  const stopping = state?.session;
  busy = true;
  btn.disabled = true;
  btn.textContent = t(stopping ? 'stopping' : 'starting');
  $('sessionBadge').textContent = t(stopping ? 'session_stopping' : 'session_starting');
  const res = await post(stopping ? '/api/stop' : '/api/start', {}, KEY);
  busy = false;
  if (res?.state) render(res.state);
  else if (state) render(state);
  if (res && res.ok === false) {
    const line = $('statusLine');
    line.classList.add('bad');
    line.textContent = t(res.error || 'err_generic');
  }
}

async function onPatch() {
  const action = state?.patch === 'on' ? 'remove' : 'apply';
  await runPatch(action, $('patchBtn'), $('patchNote'), t('patch_working'));
}

async function runPatch(action, btn, note, busyText) {
  const was = btn.textContent;
  btn.disabled = true;
  btn.textContent = busyText;
  const res = await post('/api/patch', { action }, KEY);
  btn.disabled = false;
  btn.textContent = was;
  if (res?.state) render(res.state);
  if (res && res.ok === false) note.textContent = res.note || t('err_generic');
  else note.textContent = '';
}

function openPanel(open) {
  const wasOpen = !$('panel').classList.contains('hidden');
  $('panel').classList.toggle('hidden', !open);
  $('overlay').classList.toggle('hidden', !open);
  $('gearBtn').setAttribute('aria-expanded', String(open));
  if (open) $('closeBtn').focus();
  else if (wasOpen) $('gearBtn').focus();
}

async function copyAddress() {
  if (!state?.session) return;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(state.join_url);
    } else {
      const field = document.createElement('textarea');
      field.value = state.join_url;
      field.style.cssText = 'position:fixed;left:-9999px';
      document.body.append(field);
      field.select();
      const copied = document.execCommand('copy');
      field.remove();
      if (!copied) throw Error('clipboard unavailable');
    }
    $('copyNote').textContent = t('address_copied');
  } catch {
    $('copyNote').textContent = t('copy_manual');
  }
  clearTimeout(copyTimer);
  copyTimer = setTimeout(() => { $('copyNote').textContent = ''; }, 2500);
}

/* ---------- запуск ---------- */

function wire() {
  $('startBtn').onclick = onStart;
  $('copyAddress').onclick = copyAddress;
  $('gearBtn').setAttribute('aria-controls', 'panel');
  $('gearBtn').setAttribute('aria-expanded', 'false');
  $('gearBtn').onclick = () => openPanel(true);
  $('closeBtn').onclick = () => openPanel(false);
  $('overlay').onclick = () => openPanel(false);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') openPanel(false);
    if (e.key === 'Tab' && !$('panel').classList.contains('hidden')) {
      const fields = [...$('panel').querySelectorAll('button, input, select')].filter(el => !el.disabled && el.offsetParent !== null);
      const first = fields[0], last = fields.at(-1);
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
      if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
    }
  });

  $('voiceSel').onchange = (e) => save({ tts_engine: e.target.value });
  $('langSel').onchange = (e) => save({ lang: e.target.value });
  $('roomInput').oninput = (e) => { e.target.value = e.target.value.toUpperCase(); };
  $('roomInput').onchange = (e) => save({ room_code: e.target.value.trim() });
  $('piperDir').onchange = (e) => save({ piper_dir: e.target.value.trim() });
  $('gameDir').onchange = (e) => save({ game_dir: e.target.value.trim() });
  $('portInput').onchange = (e) => save({ port: Number(e.target.value) || 8080 });
  $('addrInput').onchange = (e) => save({ announce_ip: e.target.value.trim() });
  $('dotnetPath').onchange = (e) => save({ dotnet_path: e.target.value.trim() });
  $('piperGetBtn').onclick = async () => {
    const btn = $('piperGetBtn');
    btn.disabled = true;
    btn.textContent = t('piper_downloading');
    const res = await post('/api/piper/download', {}, KEY);
    btn.disabled = false;
    btn.textContent = t('piper_download');
    if (res?.state) render(res.state);
    if (res && res.ok === false) $('piperNote').textContent = t('piper_download_failed');
  };
  $('patchBtn').onclick = onPatch;
  $('installBtn').onclick = () =>
    runPatch('apply', $('installBtn'), $('installNote'), t('installing'));
  const browse = async (note) => {
    const res = await post('/api/pick_folder', {}, KEY);
    if (res?.ok && res.path) await save({ game_dir: res.path });
    else if (note) note.textContent = '';
  };
  $('browseBtn').onclick = () => browse(null);
  $('findBrowse').onclick = () => browse($('findNote'));
  $('findPath').onchange = async (e) => {
    const path = e.target.value.trim();
    await save({ game_dir: path });
    $('findNote').textContent = state?.game_ok ? '' : t('find_bad');
  };
}

async function main() {
  await reload();
  wire();
  // Игру могли закрыть сами — состояние кнопки должно это заметить.
  // Пока сессия идёт, подтягиваем состояние: интересует, жива ли игра.
  poll = setInterval(() => { if (state?.session) reload(); }, 5000);
}

main();
