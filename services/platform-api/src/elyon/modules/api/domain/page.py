"""The page a phone loads.

One file, no network dependencies, no build step. It is served from the engine
itself, which is often a VPS with a firewall and no reason to reach a CDN.

The layout follows from what a person actually needs while away from the desk,
in order:

1.  **Is it running, and does it hold anything?** The first thing anyone looks
    for is whether money is at risk right now.
2.  **Why is it not trading?** Almost always the real question. The
    stage-by-stage breakdown answers it without anyone having to guess.
3.  **Stop.** One tap, always reachable, never behind a menu.

Stopping is the most prominent control on the page because it is the only one
that is always safe. There is no "start" button at all: resuming needs COMMAND,
a phone does not have it, and rendering a control that would only ever return
403 teaches people to ignore errors.
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
    --bg: #0b0d10; --card: #151920; --line: #232935;
    --text: #e6e9ef; --dim: #8b93a3;
    --ok: #3ecf8e; --warn: #f0b429; --bad: #f05252; --idle: #5b6472;
    color-scheme: dark;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: env(safe-area-inset-top) 16px calc(env(safe-area-inset-bottom) + 96px);
  }
  header { padding: 20px 0 12px; }
  h1 { font-size: 15px; letter-spacing: .14em; text-transform: uppercase;
       color: var(--dim); margin: 0; font-weight: 600; }
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
  /* Stopping is the only control that is always safe, so it is the only one
     that is always on screen. */
  .stop {
    position: fixed; left: 16px; right: 16px;
    bottom: calc(env(safe-area-inset-bottom) + 16px);
    padding: 18px; border: 0; border-radius: 14px;
    background: var(--bad); color: #fff; font-size: 17px; font-weight: 650;
    width: calc(100% - 32px); cursor: pointer;
  }
  .stop:disabled { background: var(--line); color: var(--dim); }
  .stop.armed { background: #7f1d1d; }
  .note { color: var(--dim); font-size: 12px; margin-top: 10px; }
  .warn { color: var(--warn); }
  input {
    width: 100%; padding: 14px; font-size: 16px; border-radius: 10px;
    border: 1px solid var(--line); background: #0f1319; color: var(--text);
  }
  .hidden { display: none; }
</style>
</head>
<body>

<header><h1>Elyon Quant</h1></header>

<div id="gate" class="card">
  <h2>Access token</h2>
  <input id="token" type="password" inputmode="text" autocomplete="off"
         placeholder="paste the token the engine printed">
  <p class="note">Kept in this browser only. Never sent anywhere but this
  engine.</p>
</div>

<div id="app" class="hidden">
  <div class="card">
    <div class="state">
      <span class="dot" id="dot"></span>
      <span class="big" id="headline">…</span>
    </div>
    <div class="sub" id="subline"></div>
    <div class="sub" id="feed"></div>
  </div>

  <div class="card" id="position-card">
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

  <button class="stop" id="stop">Stop trading</button>
</div>

<script>
const KEY = 'elyon.token';
let token = localStorage.getItem(KEY) || '';
let armed = false, armTimer = null;

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
  if (res.status === 401) { forget(); throw new Error('token rejected'); }
  return {ok: res.ok, status: res.status, data: await res.json()};
}

function forget() {
  localStorage.removeItem(KEY); token = '';
  $('gate').classList.remove('hidden'); $('app').classList.add('hidden');
}

function rows(target, pairs) {
  $(target).innerHTML = pairs.map(([k, v]) =>
    `<div class="row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`
  ).join('');
}

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
}

// Two taps, because a pocket is full of accidental single taps -- and the arm
// lapses so a phone left on a table does not stay one tap from a halt.
$('stop').onclick = async () => {
  if (!armed) {
    armed = true; render(window.last || {});
    armTimer = setTimeout(() => { armed = false; render(window.last || {}); }, 4000);
    return;
  }
  clearTimeout(armTimer); armed = false;
  const res = await api('/api/halt', 'POST', {reason: 'halted from phone'});
  if (!res.ok) alert(res.data.error || 'halt refused');
  poll();
};

$('token').onchange = async e => {
  token = e.target.value.trim();
  try {
    const res = await api('/api/whoami');
    if (!res.ok) { alert(res.data.error || 'rejected'); return; }
    localStorage.setItem(KEY, token);
    $('gate').classList.add('hidden'); $('app').classList.remove('hidden');
    poll();
  } catch (err) { alert(err.message); }
};

async function poll() {
  if (!token) return;
  try {
    const res = await api('/api/status');
    if (res.ok) { window.last = res.data; render(res.data); }
  } catch (err) { /* keep the last good view rather than blanking the screen */ }
}

if (token) {
  $('gate').classList.add('hidden'); $('app').classList.remove('hidden');
  poll();
}
setInterval(poll, 5000);
</script>
</body>
</html>
"""


def render_page() -> str:
    return PAGE
