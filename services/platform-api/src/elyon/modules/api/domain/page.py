"""The app a phone loads.

One file, no network dependencies, no build step. It is served from the engine
itself, which is often a VPS with a firewall and no reason to reach a CDN.

Four screens, in the order somebody actually needs them:

1.  **Sign in.** Username and password, exchanged once for a session token. The
    password is never stored and never sent again.
2.  **Is it running, and does it hold anything?** The first thing anyone looks
    for is whether money is at risk right now.
3.  **Why is it not trading?** Almost always the real question. The
    stage-by-stage breakdown answers it without anyone having to guess.
4.  **Settings, and Start.** What the engine is set to do, what may be changed
    right now, and whether starting it would achieve anything.

Two rules run through the whole page.

**The server decides what may be changed; the page only draws it.** Every
setting arrives with an ``editable`` flag and, when it is false, the reason.
Nothing here re-derives that, so a control that would be refused is never
offered and a refusal never arrives as a surprise.

**Stop is always on screen.** It is the only control that is always safe, so it
is never behind a tab or a menu. There is no matching always-visible Start:
starting needs COMMAND, and an account without it gets no button rather than a
button that returns 403 -- a control that always errors teaches people to ignore
errors.
"""

from __future__ import annotations

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0b0d10">
<title>ELYON QUANT</title>
<style>
  :root {
    --bg: #0b0d10; --card: #151920; --line: #232935; --raised: #1c212a;
    --text: #e6e9ef; --dim: #8b93a3;
    --ok: #3ecf8e; --warn: #f0b429; --bad: #f05252; --idle: #5b6472;
    --accent: #5b8def;
    color-scheme: dark;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: env(safe-area-inset-top) 16px calc(env(safe-area-inset-bottom) + 150px);
  }
  header { padding: 20px 0 12px; display: flex; justify-content: space-between;
           align-items: baseline; gap: 12px; }
  h1 { font-size: 15px; letter-spacing: .14em; text-transform: uppercase;
       color: var(--dim); margin: 0; font-weight: 600; }
  .who { color: var(--dim); font-size: 12px; }
  .who b { color: var(--text); font-weight: 600; }
  .card { background: var(--card); border: 1px solid var(--line);
          border-radius: 14px; padding: 16px; margin-bottom: 12px; }
  .state { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }
  .big { font-size: 26px; font-weight: 650; letter-spacing: -.02em; }
  .sub { color: var(--dim); font-size: 13px; }
  .row { display: flex; justify-content: space-between; gap: 12px;
         padding: 9px 0; border-bottom: 1px solid var(--line); }
  .row:last-child { border-bottom: 0; }
  .row span:first-child { color: var(--dim); }
  .row span:last-child { font-variant-numeric: tabular-nums; text-align: right; }
  h2 { font-size: 11px; letter-spacing: .12em; text-transform: uppercase;
       color: var(--dim); margin: 0 0 10px; font-weight: 600; }
  .bar { height: 6px; background: var(--line); border-radius: 3px;
         overflow: hidden; margin-top: 6px; }
  .bar i { display: block; height: 100%; background: var(--idle); }
  .stage { margin-bottom: 12px; }
  .stage:last-child { margin-bottom: 0; }
  .stage .row { border: 0; padding: 0; }

  /* Tabs. Settings and Start live behind them; Stop never does. */
  nav { position: fixed; left: 16px; right: 16px;
        bottom: calc(env(safe-area-inset-bottom) + 80px);
        display: flex; gap: 6px; background: var(--raised);
        border: 1px solid var(--line); border-radius: 12px; padding: 4px; }
  nav button { flex: 1; padding: 10px 4px; border: 0; border-radius: 9px;
               background: transparent; color: var(--dim); font-size: 13px;
               font-weight: 600; cursor: pointer; }
  nav button.on { background: var(--line); color: var(--text); }

  .stop {
    position: fixed; left: 16px; right: 16px;
    bottom: calc(env(safe-area-inset-bottom) + 16px);
    padding: 18px; border: 0; border-radius: 14px;
    background: var(--bad); color: #fff; font-size: 17px; font-weight: 650;
    width: calc(100% - 32px); cursor: pointer;
  }
  .stop:disabled { background: var(--line); color: var(--dim); }
  .stop.armed { background: #7f1d1d; }

  button.go { width: 100%; padding: 16px; border: 0; border-radius: 12px;
              background: var(--ok); color: #04150d; font-size: 16px;
              font-weight: 650; cursor: pointer; }
  button.go:disabled { background: var(--line); color: var(--dim); }
  button.ghost { width: 100%; padding: 12px; border: 1px solid var(--line);
                 border-radius: 10px; background: transparent; color: var(--dim);
                 font-size: 14px; cursor: pointer; }

  .note { color: var(--dim); font-size: 12px; margin-top: 10px; }
  .warn { color: var(--warn); }
  .err  { color: var(--bad); }
  .good { color: var(--ok); }
  label { display: block; margin-bottom: 14px; }
  label .name { font-size: 13px; font-weight: 600; margin-bottom: 2px;
                display: flex; justify-content: space-between; gap: 8px; }
  label .why { color: var(--dim); font-size: 12px; margin-bottom: 6px; }
  input, select, textarea {
    width: 100%; padding: 13px; font-size: 16px; border-radius: 10px;
    border: 1px solid var(--line); background: #0f1319; color: var(--text);
    font-family: inherit;
  }
  input:disabled, select:disabled { color: var(--dim); background: #0c0f14; }
  .tag { font-size: 10px; letter-spacing: .08em; text-transform: uppercase;
         padding: 2px 7px; border-radius: 20px; border: 1px solid var(--line);
         color: var(--dim); font-weight: 600; white-space: nowrap; }
  .tag.danger { color: var(--bad); border-color: #4a2225; }
  .tag.locked { color: var(--warn); border-color: #4a3a16; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { padding: 8px 12px; border-radius: 20px; border: 1px solid var(--line);
          background: #0f1319; color: var(--dim); font-size: 13px;
          cursor: pointer; }
  .chip.on { background: #14301f; border-color: #1f5c3a; color: var(--ok); }
  .chip:disabled { opacity: .45; cursor: default; }
  .check { display: flex; gap: 10px; padding: 10px 0;
           border-bottom: 1px solid var(--line); }
  .check:last-child { border-bottom: 0; }
  .check .mark { flex: none; width: 18px; }
  .hidden { display: none; }
  .pending { border-color: var(--accent) !important; }
</style>
</head>
<body>

<header>
  <h1>Elyon Quant</h1>
  <span class="who" id="who"></span>
</header>

<!-- 1. Sign in ---------------------------------------------------------- -->
<div id="gate" class="card">
  <h2>Sign in</h2>
  <label>
    <div class="name">Username</div>
    <input id="username" autocomplete="username" autocapitalize="none"
           spellcheck="false" inputmode="text">
  </label>
  <label>
    <div class="name">Password</div>
    <input id="password" type="password" autocomplete="current-password">
  </label>
  <button class="go" id="signin">Sign in</button>
  <p class="note" id="gate-error"></p>
  <p class="note">The password is exchanged for a session that expires. It is
  never stored on this device and never sent again.</p>
</div>

<!-- 2. The app ---------------------------------------------------------- -->
<div id="app" class="hidden">

  <div id="tab-status">
    <div class="card">
      <div class="state">
        <span class="dot" id="dot"></span>
        <span class="big" id="headline">…</span>
      </div>
      <div class="sub" id="subline"></div>
      <div class="sub" id="feed"></div>
    </div>

    <div class="card hidden" id="position-card">
      <h2>Position</h2>
      <div id="position"></div>
    </div>

    <div class="card">
      <h2>Why it is not trading</h2>
      <div id="stages"></div>
      <p class="note">Where the pipeline stopped, per bar. “No trade” is not one
      answer.</p>
    </div>

    <div class="card">
      <h2>Session</h2>
      <div id="session"></div>
      <div id="warnings"></div>
    </div>
  </div>

  <div id="tab-settings" class="hidden">
    <div class="card">
      <h2>Settings</h2>
      <div id="settings"></div>
    </div>
    <div class="card hidden" id="confirm-card">
      <h2 class="err">This one is different</h2>
      <p class="note">Switching to LIVE sends orders to a real broker. Type
      <b id="confirm-phrase"></b> to confirm.</p>
      <input id="confirm" autocapitalize="characters" spellcheck="false">
    </div>
    <div class="card" id="apply-card">
      <button class="go" id="apply" disabled>Apply changes</button>
      <p class="note" id="apply-note">Nothing changed yet. Edits are applied
      together: a configuration is valid as a whole or not at all.</p>
    </div>
    <div class="card">
      <h2>Recent changes</h2>
      <div id="changes"></div>
    </div>
  </div>

  <div id="tab-start" class="hidden">
    <div class="card">
      <h2>Before starting</h2>
      <div id="preflight"></div>
      <p class="note">A bot that starts and then quietly does nothing is the
      failure that wastes the most time. This says what would stop it.</p>
    </div>
    <div class="card" id="start-card">
      <button class="go" id="start">Start trading</button>
      <p class="note" id="start-note"></p>
    </div>
    <div class="card">
      <button class="ghost" id="signout">Sign out</button>
    </div>
  </div>

  <nav>
    <button data-tab="status" class="on">Status</button>
    <button data-tab="settings">Settings</button>
    <button data-tab="start">Start</button>
  </nav>
  <button class="stop" id="stop">Stop trading</button>
</div>

<script>
const KEY = 'elyon.token';
let token = localStorage.getItem(KEY) || '';
let armed = false, armTimer = null;
let me = null, config = null, pending = {}, tab = 'status';

const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

async function api(path, method = 'GET', body) {
  const res = await fetch(path, {
    method,
    headers: {'Authorization': 'Bearer ' + token,
              'Content-Type': 'application/json'},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  // 401 on any call means the session is over -- expired, revoked, or the
  // engine restarted. Dropping straight back to the login form is the honest
  // response; retrying with a dead token just produces a page of blanks.
  if (res.status === 401 && path !== '/api/login') { forget(data.error); }
  return {ok: res.ok, status: res.status, data};
}

function forget(why) {
  localStorage.removeItem(KEY); token = ''; me = null; config = null;
  pending = {};
  $('gate').classList.remove('hidden'); $('app').classList.add('hidden');
  $('gate-error').textContent = why || '';
  $('gate-error').className = 'note warn';
}

function rows(target, pairs) {
  $(target).innerHTML = pairs.map(([k, v]) =>
    `<div class="row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`
  ).join('');
}

// -- signing in -----------------------------------------------------------

async function signIn() {
  const username = $('username').value.trim();
  const password = $('password').value;
  $('gate-error').textContent = 'signing in…';
  $('gate-error').className = 'note';
  const res = await fetch('/api/login', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username, password}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    $('gate-error').textContent = data.error || 'sign in failed';
    $('gate-error').className = 'note err';
    return;
  }
  token = data.token;
  localStorage.setItem(KEY, token);
  // The password field is cleared immediately: a form left populated is a
  // password sitting in a browser's memory for as long as the tab is open.
  $('password').value = '';
  await enter();
}

async function enter() {
  const who = await api('/api/whoami');
  if (!who.ok) { forget(who.data.error); return; }
  me = who.data;
  $('gate').classList.add('hidden');
  $('app').classList.remove('hidden');
  $('who').innerHTML = `<b>${esc(me.label)}</b> · ` +
    (me.canCommand ? 'owner' : 'watch &amp; stop');
  // No Start button for an account that cannot start. Rendering one that
  // always answers 403 teaches people that errors are normal.
  $('tab-start').querySelector('#start-card').classList.toggle(
    'hidden', !me.canCommand);
  document.querySelector('nav [data-tab="settings"]').classList.toggle(
    'hidden', !me.canConfigure);
  poll();
  loadConfig();
}

$('signin').onclick = signIn;
$('password').onkeydown = e => { if (e.key === 'Enter') signIn(); };
$('signout').onclick = async () => {
  await api('/api/logout', 'POST', {});
  forget('signed out');
};

// -- tabs -----------------------------------------------------------------

document.querySelectorAll('nav button').forEach(b => {
  b.onclick = () => {
    tab = b.dataset.tab;
    document.querySelectorAll('nav button').forEach(
      x => x.classList.toggle('on', x === b));
    ['status', 'settings', 'start'].forEach(t =>
      $('tab-' + t).classList.toggle('hidden', t !== tab));
    if (tab === 'settings') loadConfig();
    if (tab === 'start') loadPreflight();
  };
});

// -- status ---------------------------------------------------------------

function render(s) {
  const halted = s.halted, exposed = s.position && s.position.open;
  $('dot').style.background =
    halted ? 'var(--bad)' : exposed ? 'var(--warn)' : 'var(--ok)';
  $('headline').textContent =
    halted ? 'Halted' : exposed ? 'In a position' : 'Watching';
  $('subline').textContent =
    halted ? (s.haltReason || 'stopped') : (s.symbol + ' · ' + s.mode);

  // A stalled feed with a position open is the worst state to be unaware of,
  // so it is stated rather than left to be inferred from a stale number.
  if (s.feed) {
    const bad = s.feed !== 'LIVE';
    $('feed').innerHTML = bad
      ? `<span class="warn">Feed ${esc(s.feed)}` +
        (s.secondsSinceTick != null ? ` · ${s.secondsSinceTick}s silent` : '') +
        `</span>` + (s.feedDetail ? `<br>${esc(s.feedDetail)}` : '')
      : `Feed live · ${s.ticks} ticks`;
  } else {
    $('feed').textContent = '';
  }

  if (exposed) {
    $('position-card').classList.remove('hidden');
    const p = s.position;
    rows('position', [
      ['Side', p.direction], ['Size', p.quantity], ['Entry', p.entry],
      ['Stop', p.stop], ['Target', p.target],
      ['Locked in', p.lockedR + 'R'], ['Bars held', p.barsHeld],
    ]);
  } else {
    $('position-card').classList.add('hidden');
  }

  const stages = s.stoppedAt || {};
  const total = Object.values(stages).reduce((a, b) => a + b, 0) || 1;
  $('stages').innerHTML = Object.entries(stages).map(([name, count]) => {
    const pct = Math.round(count * 100 / total);
    return `<div class="stage">
      <div class="row"><span>${esc(name)}</span><span>${count}</span></div>
      <div class="bar"><i style="width:${pct}%"></i></div></div>`;
  }).join('') || '<p class="note">No bars evaluated yet.</p>';

  rows('session', [
    ['Bars seen', s.bars], ['Entries taken', s.entries],
    ['Positions closed', s.closed], ['Realized', s.realizedR + 'R'],
    ['Orders', s.orders], ['Dead letters', s.deadLetters],
  ]);

  $('warnings').innerHTML = (s.warnings || []).map(w =>
    `<p class="note warn">${esc(w)}</p>`).join('');

  $('stop').disabled = halted;
  $('stop').textContent = halted ? 'Already halted'
    : armed ? 'Tap again to confirm' : 'Stop trading';
  $('stop').classList.toggle('armed', armed && !halted);
  $('start').textContent = halted ? 'Start trading' : 'Already running';
  $('start').disabled = !halted;
}

// -- settings -------------------------------------------------------------

function fieldFor(s) {
  const locked = !s.editable;
  const value = s.value;
  const staged = pending[s.key] !== undefined;
  const shown = staged ? pending[s.key] : value;
  const cls = staged ? 'pending' : '';

  if (s.kind === 'strategies') {
    const on = new Set(shown || []);
    return `<div class="chips">` + s.choices.map(c =>
      `<button class="chip ${on.has(c) ? 'on' : ''}" data-multi="${s.key}"
        data-value="${esc(c)}" ${locked ? 'disabled' : ''}>${esc(c)}</button>`
    ).join('') + `</div>`;
  }
  if (s.kind === 'bool') {
    return `<select data-key="${s.key}" class="${cls}" ${locked ? 'disabled' : ''}>
      <option value="false" ${!shown ? 'selected' : ''}>off</option>
      <option value="true" ${shown ? 'selected' : ''}>on</option></select>`;
  }
  if (s.kind === 'choice') {
    return `<select data-key="${s.key}" class="${cls}" ${locked ? 'disabled' : ''}>` +
      s.choices.map(c => `<option ${c === shown ? 'selected' : ''}>${esc(c)}</option>`)
      .join('') + `</select>`;
  }
  const mode = (s.kind === 'int' || s.kind === 'decimal' || s.kind === 'percent')
    ? 'decimal' : 'text';
  return `<input data-key="${s.key}" class="${cls}" inputmode="${mode}"
    value="${esc(shown === null || shown === undefined ? '' : shown)}"
    ${locked ? 'disabled' : ''}>`;
}

function renderSettings() {
  if (!config) return;
  $('confirm-phrase').textContent = config.liveConfirmation;
  $('settings').innerHTML = config.settings.map(s => {
    const tags = [];
    if (s.dangerous) tags.push('<span class="tag danger">careful</span>');
    if (!s.editable) tags.push('<span class="tag locked">locked</span>');
    else if (s.scope !== 'LIVE') tags.push(`<span class="tag">${esc(s.scope)}</span>`);
    return `<label>
      <div class="name"><span>${esc(s.label)}</span><span>${tags.join(' ')}</span></div>
      <div class="why">${esc(s.blockedBecause || s.help)}</div>
      ${fieldFor(s)}
    </label>`;
  }).join('');

  $('settings').querySelectorAll('[data-key]').forEach(el => {
    el.onchange = () => { stage(el.dataset.key, el.value); };
  });
  $('settings').querySelectorAll('[data-multi]').forEach(el => {
    el.onclick = () => {
      const key = el.dataset.multi;
      const current = new Set(pending[key] !== undefined
        ? pending[key]
        : config.settings.find(s => s.key === key).value);
      current.has(el.dataset.value)
        ? current.delete(el.dataset.value)
        : current.add(el.dataset.value);
      stage(key, [...current]);
      renderSettings();
    };
  });

  $('changes').innerHTML = (config.recentChanges || []).slice().reverse()
    .map(c => `<div class="row"><span>${esc(c.who)} · ${esc(c.key)}</span>
      <span>${esc(JSON.stringify(c.before))} → ${esc(JSON.stringify(c.after))}</span>
      </div>`).join('') || '<p class="note">Nothing changed this session.</p>';

  refreshApply();
}

function stage(key, value) {
  const original = config.settings.find(s => s.key === key).value;
  // Typing a value and then typing the old one back is not a change. Sending
  // it anyway would fill the audit trail with edits nobody made.
  if (JSON.stringify(value) === JSON.stringify(original)) delete pending[key];
  else pending[key] = value;
  refreshApply();
}

function refreshApply() {
  const count = Object.keys(pending).length;
  const goingLive = pending.mode === 'LIVE';
  $('confirm-card').classList.toggle('hidden', !goingLive);
  $('apply').disabled = count === 0;
  $('apply').textContent = count ? `Apply ${count} change(s)` : 'Apply changes';
  if (!count) {
    $('apply-note').textContent =
      'Nothing changed yet. Edits are applied together: a configuration is ' +
      'valid as a whole or not at all.';
    $('apply-note').className = 'note';
  }
}

async function loadConfig() {
  const res = await api('/api/config');
  if (!res.ok) return;
  config = res.data;
  renderSettings();
}

$('apply').onclick = async () => {
  const res = await api('/api/config', 'POST', {
    changes: pending, confirm: $('confirm').value,
  });
  $('apply-note').textContent = res.data.error || res.data.message || '';
  // Applied-but-not-saved is not a success. The change is live, and it will be
  // gone after a restart -- which is the failure people notice weeks later,
  // when the engine is quietly running settings nobody chose.
  $('apply-note').className = 'note ' +
    (!res.ok ? 'err' : res.data.saved === false ? 'warn' : 'good');
  if (res.ok) {
    pending = {}; $('confirm').value = '';
    await loadConfig();
    poll();
  }
};

// -- preflight and starting ----------------------------------------------

async function loadPreflight() {
  const res = await api('/api/preflight');
  if (!res.ok) {
    $('preflight').innerHTML =
      `<p class="note">${esc(res.data.error || 'unavailable')}</p>`;
    return;
  }
  $('preflight').innerHTML = res.data.checks.map(c => {
    const mark = c.passed ? '<span class="good">✓</span>'
      : c.blocking ? '<span class="err">✕</span>'
      : '<span class="warn">!</span>';
    return `<div class="check"><span class="mark">${mark}</span>
      <span><b>${esc(c.name)}</b><br><span class="sub">${esc(c.detail)}</span></span>
      </div>`;
  }).join('');
  $('start-note').textContent = res.data.canStart
    ? 'Nothing blocking. Warnings above are still worth reading.'
    : 'Preflight is blocking. Starting will be refused.';
  $('start-note').className = 'note ' + (res.data.canStart ? '' : 'err');
}

$('start').onclick = async () => {
  const res = await api('/api/start', 'POST', {});
  $('start-note').textContent = res.data.message || res.data.error || '';
  $('start-note').className = 'note ' + (res.ok ? 'good' : 'err');
  loadPreflight();
  poll();
};

// Two taps, because a pocket is full of accidental single taps -- and the arm
// lapses so a phone left on a table does not stay one tap from a halt.
$('stop').onclick = async () => {
  if (!armed) {
    armed = true; render(window.last || {});
    armTimer = setTimeout(() => { armed = false; render(window.last || {}); }, 4000);
    return;
  }
  clearTimeout(armTimer); armed = false;
  const res = await api('/api/stop', 'POST', {reason: 'stopped from phone'});
  if (!res.ok) {
    // Older engines expose only /api/halt. Falling back keeps the one control
    // that must never fail working against either.
    const legacy = await api('/api/halt', 'POST', {reason: 'halted from phone'});
    if (!legacy.ok) alert(legacy.data.error || 'stop refused');
  }
  poll();
};

async function poll() {
  if (!token) return;
  try {
    const res = await api('/api/status');
    if (res.ok) { window.last = res.data; render(res.data); }
  } catch (err) { /* keep the last good view rather than blanking the screen */ }
}

if (token) { enter(); }
setInterval(poll, 5000);
</script>
</body>
</html>
"""


def render_page() -> str:
    return PAGE
