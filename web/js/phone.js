// Телефон игрока. Только текст: сцены и реплики показывает окно игры на хосте.
import { applyStrings, post, setStrings, t, escapeHtml, reportErrors } from './common.js';

const $ = (id) => document.getElementById(id);
const STORE = 'localdub.player';
const CODE_STORE = 'localdub.code';
const log = reportErrors('phone');

let me = null;
let last = null;
let source = null;
let editing = false;
let picked = [];

const SCREENS = ['join', 'wait', 'write', 'sent', 'vote', 'sfx'];
const show = (name) => {
  for (const s of SCREENS) $(`s-${s}`).classList.toggle('hidden', s !== name);
};

/* ---------- строки ---------- */

async function loadStrings() {
  try {
    const res = await fetch('/api/phone/strings');
    setStrings(await res.json());
  } catch (e) {
    log('строки не загрузились: ' + e);
  }
  applyStrings();
}

/* ---------- вход ---------- */

async function join(name, code, token) {
  const res = await post('/api/phone/join', { name, code, token });
  if (!res.ok) {
    $('joinErr').textContent = t({
      taken: 'phone_taken', bad_name: 'phone_bad_name',
      bad_code: 'phone_bad_code', no_room: 'phone_no_room',
    }[res.error] || 'phone_bad_name');
    return false;
  }
  if (code) localStorage.setItem(CODE_STORE, code);
  me = res.player;
  localStorage.setItem(STORE, me.token);
  $('meName').textContent = me.name;
  $('bar').classList.remove('hidden');
  $('leaveBtn').classList.remove('hidden');
  $('joinErr').textContent = '';
  listen();
  return true;
}

function listen() {
  source?.close();
  source = new EventSource(`/api/phone/events?token=${encodeURIComponent(me.token)}`);
  source.onmessage = (ev) => {
    $('offline').classList.add('hidden');
    try { apply(JSON.parse(ev.data)); } catch { /* битый кадр пропускаем */ }
  };
  source.onerror = () => {
    $('offline').textContent = t('phone_disconnected');
    $('offline').classList.remove('hidden');
  };
}

/* ---------- сцена ---------- */

// Текст сцены приходит из игры разметкой. Оставляем только безопасные теги.
function renderScene(html) {
  const box = $('scene');
  const clean = String(html || '')
    .replace(/<\s*(script|style|iframe|object|embed)[\s\S]*?>[\s\S]*?<\s*\/\s*\1\s*>/gi, '')
    .replace(/\son\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, '')
    .replace(/<\s*(?!\/?(b|i|u|em|strong|br|p|div|span|mark)\b)[^>]*>/gi, '');
  box.innerHTML = clean || `<span class="muted">${escapeHtml(t('phone_scene'))}</span>`;
}

/* ---------- отправка ---------- */

function buildWrite(state) {
  const input = $('dubInput');
  input.maxLength = state.max_length || 150;
  if (!editing) input.value = '';
  else input.value = state.submitted || '';
  updateCount();
  renderScene(state.script);
  updateSfxButton(state);
}

function updateCount() {
  const input = $('dubInput');
  $('dubCount').textContent = `${input.value.length}/${input.maxLength}`;
}

async function sendDub() {
  const text = $('dubInput').value.trim();
  if (!text) { $('writeErr').textContent = t('phone_empty'); return; }
  $('sendBtn').disabled = true;
  const res = await post('/api/phone/dub', { token: me.token, text });
  $('sendBtn').disabled = false;
  if (res.ok) { editing = false; $('writeErr').textContent = ''; }
  else $('writeErr').textContent = t(res.error === 'empty' ? 'phone_empty' : 'err_generic');
}

/* ---------- звуки из игры ---------- */

// Сколько звуков уже стоит в тексте: считаем вхождения их подписей.
function sfxUsed(text, sounds) {
  const lower = String(text || '').toLowerCase();
  return sounds.reduce((total, s) => {
    const mark = s.replacement.toLowerCase();
    return total + (mark ? lower.split(mark).length - 1 : 0);
  }, 0);
}

function updateSfxButton(state) {
  const sounds = state.sfx || [];
  const limit = state.max_sfx;
  const input = $('dubInput');
  const full = input.value.length >= (state.max_length || 150);
  const reached = limit != null && limit >= 0 && sfxUsed(input.value, sounds) >= limit;
  $('sfxBtn').classList.toggle('hidden', sounds.length === 0 || full || reached);
}

function openSfx(state) {
  $('sfxList').innerHTML = (state.sfx || []).map((s, i) => `
    <button data-i="${i}">${escapeHtml(s.display)}</button>`).join('');
  for (const btn of $('sfxList').querySelectorAll('button')) {
    btn.onclick = () => {
      insertAtCursor(state.sfx[Number(btn.dataset.i)].replacement);
      show('write');
      updateSfxButton(state);
    };
  }
  show('sfx');
}

// Вставляем туда, где стоит курсор, отделяя пробелами, — как в игре.
function insertAtCursor(text) {
  const input = $('dubInput');
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? input.value.length;
  let before = input.value.slice(0, start);
  if (before.length && !before.endsWith(' ')) before += ' ';
  input.value = before + text + ' ' + input.value.slice(end);
  const at = (before + text + ' ').length;
  input.focus();
  try { input.setSelectionRange(at, at); } catch { /* не везде поддерживается */ }
  updateCount();
}

/* ---------- голосование ---------- */

function renderVote(state) {
  const allowed = state.votes_allowed || 1;
  picked = picked.filter((id) => state.options.some((o) => o.id === id));
  $('voteHint').textContent = allowed > 1 ? `1…${allowed}` : '';
  $('voteList').innerHTML = state.options.map((o) => `
    <button class="vote-btn ${picked.includes(o.id) ? 'chosen' : ''}" data-id="${escapeHtml(o.id)}"
    >«${escapeHtml(o.dub)}»</button>`).join('');

  for (const btn of $('voteList').querySelectorAll('.vote-btn')) {
    btn.onclick = () => {
      const id = btn.dataset.id;
      if (allowed === 1) { picked = [id]; sendVote(); return; }
      picked = picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id].slice(-allowed);
      renderVote(state);
    };
  }
  $('voteBtn').classList.toggle('hidden', allowed === 1 || picked.length === 0);
}

async function sendVote() {
  if (!picked.length) return;
  const res = await post('/api/phone/vote', { token: me.token, ids: picked });
  if (!res.ok) log('голос не принят: ' + res.error);
}

/* ---------- управление партией с телефона ---------- */

function renderManage(state) {
  const row = $('manageRow');
  const buttons = [];
  // Начать, отменить старт и пропустить вступление может только хост.
  if (state.is_host && state.phase === 'intro') buttons.push(['skip_intro', 'phone_skip', true]);
  if (state.can_manage) {
    if (state.countdown && (state.phase === 'lobby' || state.phase === 'next')) {
      buttons.push(['cancel', 'phone_cancel_start', false]);
    } else if (state.phase === 'lobby') {
      buttons.push(['start', 'phone_start', true, state.players_needed > 0]);
    } else if (state.phase === 'next') {
      buttons.push(['next_game', 'phone_next_game', true]);
    }
  }
  row.innerHTML = buttons.map(([action, key, primary, off]) => `
    <button class="${primary ? 'primary' : ''}" data-action="${action}" ${off ? 'disabled' : ''}
    >${t(key)}</button>`).join('');
  for (const btn of row.querySelectorAll('button')) {
    btn.onclick = async () => {
      btn.disabled = true;
      await post('/api/phone/control', { token: me.token, action: btn.dataset.action });
      btn.disabled = false;
    };
  }
}

/* ---------- состояние ---------- */

function waitScreen(title, sub, emoji) {
  $('waitTitle').textContent = title;
  $('waitSub').textContent = sub || '';
  $('waitEmoji').textContent = emoji;
  show('wait');
}

function apply(state) {
  if (!state.known) {
    localStorage.removeItem(STORE);
    location.reload();
    return;
  }
  const prev = last;
  last = state;

  $('meName').textContent = state.me.name;
  $('roundLabel').textContent = state.round ? t('phone_round', state.round) : '';
  $('hostBadge').classList.toggle('hidden', !state.is_host);
  $('playerList').innerHTML = (state.players || []).map((p) => `
    <li class="${p.connected ? '' : 'gone'} ${p.host ? 'host' : ''}"
        title="${p.host ? escapeHtml(t('phone_host')) : ''}">${escapeHtml(p.name)}</li>`).join('');
  renderManage(state);

  if (state.phase !== 'write') editing = false;
  if (state.phase !== 'vote') picked = [];

  if (state.kicked) {
    waitScreen(t(state.kicked), '', '🚪');
    return;
  }
  if (!state.bridge) {
    waitScreen(t('phone_offline'), '', '🔌');
    return;
  }

  switch (state.phase) {
    case 'lobby':
      if (state.countdown) {
        waitScreen(t('phone_countdown'),
                   t(state.can_manage ? 'phone_countdown_host' : 'phone_countdown_guest'), '⏳');
      } else if (state.can_manage && state.players_needed > 0) {
        waitScreen(t('phone_you_host'), t('phone_need_players', state.players_needed), '🍿');
      } else if (state.no_score) {
        waitScreen(state.is_host ? t('phone_you_host') : t('phone_wait_host'), t('phone_no_score'), '🍿');
      } else {
        waitScreen(state.is_host ? t('phone_you_host') : t('phone_wait_host'),
                   t('phone_players'), '🍿');
      }
      break;
    case 'intro':
      waitScreen(t('phone_intro'), state.is_host ? '' : t('phone_intro_host'), '🎬');
      break;
    case 'watch':
      waitScreen(t('phone_watch'), '', '📺');
      break;
    case 'write':
      if (state.submitted && !editing) {
        $('sentText').textContent = `«${state.submitted}»`;
        show('sent');
      } else {
        if (prev?.phase !== 'write' || editing) buildWrite(state);
        // Не выдёргиваем игрока из списка звуков обновлением состояния.
        if (!$('s-sfx').classList.contains('hidden')) updateSfxButton(state);
        else show('write');
      }
      break;
    case 'vote':
      if (state.voted?.length) waitScreen(t('phone_voted'), t('phone_wait_others'), '🗳️');
      else { renderVote(state); show('vote'); }
      break;
    case 'scores': {
      const votes = state.my_score
        ? (state.my_score.rank0Votes || 0) + (state.my_score.rank1Votes || 0)
          + (state.my_score.rank2Votes || 0) + (state.my_score.audienceVotes || 0)
        : null;
      waitScreen(t('phone_watch'), votes == null ? '' : t('phone_score', votes), '📊');
      break;
    }
    case 'next':
      if (state.countdown) {
        waitScreen(t('phone_countdown'),
                   t(state.can_manage ? 'phone_countdown_host' : 'phone_countdown_guest'), '⏳');
        break;
      }
      waitScreen(
        state.winner ? (state.winner === state.me.pid ? t('phone_win') : t('phone_lose'))
                     : t('phone_wait_game'),
        '', state.winner === state.me.pid ? '🏆' : '🎬');
      break;
    default:
      waitScreen(t('phone_watch'), '', '📺');
  }
}

/* ---------- запуск ---------- */

async function main() {
  await loadStrings();
  show('join');

  $('codeInput').value = localStorage.getItem(CODE_STORE) || '';
  $('joinBtn').onclick = () => join($('nameInput').value, $('codeInput').value, null);
  $('nameInput').onkeydown = (e) => { if (e.key === 'Enter') $('codeInput').focus(); };
  $('codeInput').onkeydown = (e) => { if (e.key === 'Enter') $('joinBtn').click(); };
  $('dubInput').oninput = () => {
    updateCount();
    if (last) updateSfxButton(last);
  };
  $('sfxBtn').onclick = () => { if (last) openSfx(last); };
  $('sfxClose').onclick = () => show('write');
  $('sendBtn').onclick = sendDub;
  $('voteBtn').onclick = sendVote;
  $('editBtn').onclick = () => { editing = true; if (last) apply(last); };
  $('leaveBtn').onclick = async () => {
    await post('/api/phone/leave', { token: me?.token });
    localStorage.removeItem(STORE);
    location.reload();
  };

  const menu = $('menu');
  const toggleMenu = (open) => {
    menu.classList.toggle('hidden', !open);
    $('menuBtn').setAttribute('aria-expanded', String(open));
  };
  $('menuBtn').setAttribute('aria-label', t('phone_menu'));
  $('menuBtn').onclick = (e) => {
    e.stopPropagation();
    toggleMenu(menu.classList.contains('hidden'));
  };
  document.addEventListener('click', (e) => {
    if (!menu.contains(e.target)) toggleMenu(false);
  });
  $('reloadBtn').onclick = () => location.reload();

  const saved = localStorage.getItem(STORE);
  if (saved && !(await join('', '', saved))) {
    localStorage.removeItem(STORE);
    $('joinErr').textContent = '';
  }
}

main();
