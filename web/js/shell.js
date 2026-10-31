// The frame around every screen: router, theme, toasts, popovers, the top bar
// (lake, connection, recording), the command menu, shortcuts and the serial dock.

import { $, $$, ic, hydrate, play } from './icons.js';
import { S, on, emit, now } from './state.js';
import { post } from './api.js';
import { esc, hmsms, at, mmss } from './format.js';

const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
};
export { store };

/* ---------------- router ---------------- */
export const PAGES = ['live', 'route', 'logs', 'sessions', 'config', 'mission', 'checks'];   // the last two are the simulator's (sim.js)
let page = 'live';
const pageHooks = {};
export const currentPage = () => page;
export function onPage(name, fn) { (pageHooks[name] ||= []).push(fn); }

export function go(p, push = true) {
  if (!PAGES.includes(p)) p = 'live';
  page = p;
  const main = $('#main'), cur = $(`.page[data-page~="${p}"]`);   // a page may serve several tabs
  $$('.page').forEach(s => { s.hidden = s !== cur; });
  if (main.firstElementChild !== cur) main.prepend(cur);   // the shown screen's h1 comes first
  $$('.tab').forEach(t => {
    const isOn = t.dataset.page === p;
    if (isOn) t.setAttribute('aria-current', 'page'); else t.removeAttribute('aria-current');
    if (isOn && push) play(t);
    if (isOn) t.scrollIntoView({ block: 'nearest', inline: 'nearest' });   // a narrow row of tabs scrolls to the current one
  });
  if (push) { try { history.replaceState(null, '', '#' + p); } catch (e) {} }
  window.scrollTo(0, 0);
  (pageHooks[p] || []).forEach(fn => requestAnimationFrame(() => fn()));
}

/* ---------------- theme ---------------- */
export function theme() { return store.get('wb-theme') || 'system'; }
export function isDark() {
  const t = document.documentElement.getAttribute('data-theme');
  if (t) return t === 'dark';
  return matchMedia('(prefers-color-scheme: dark)').matches;
}
export function applyTheme(v, announce = false) {
  const root = document.documentElement;
  if (v === 'system') root.removeAttribute('data-theme'); else root.setAttribute('data-theme', v);
  $$('[data-theme-set]').forEach(b => b.setAttribute('aria-checked', String(b.dataset.themeSet === v)));
  store.set('wb-theme', v);
  emit('theme', isDark());
  if (announce) toast({ tone: 'info', icon: v === 'dark' ? 'moon' : v === 'light' ? 'sun' : 'monitor', title: `${v[0].toUpperCase()}${v.slice(1)} theme` });
}
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (theme() === 'system') emit('theme', isDark()); });

/* ---------------- toasts ---------------- */
export function toast({ title, desc = '', tone = 'pos', icon }) {
  const box = $('#toasts');
  const el = document.createElement('div');
  el.className = `toast ${tone}`;
  el.setAttribute('role', tone === 'neg' || tone === 'warn' ? 'alert' : 'status');
  const name = icon || (tone === 'pos' ? 'check' : tone === 'info' ? 'check' : 'alert');
  el.innerHTML = `${ic(name)}<div><div class="tt">${title}</div>${desc ? `<div class="td">${desc}</div>` : ''}</div><button class="x ica" aria-label="Dismiss">${ic('x')}</button>`;
  box.appendChild(el);
  while (box.children.length > 3) box.firstElementChild.remove();
  let timer;
  const close = () => { el.classList.add('out'); setTimeout(() => el.remove(), 260); };
  const arm = () => { timer = setTimeout(close, tone === 'neg' ? 7000 : 4200); };
  el.addEventListener('mouseenter', () => clearTimeout(timer));
  el.addEventListener('mouseleave', arm);
  $('.x', el).addEventListener('click', close);
  arm();
  const a = $('.ic-alert', el); if (a) a.classList.add('shake');
  return el;
}
// A command's answer as a toast: its message on failure, `okTitle` on success.
export function answer(res, okTitle, okDesc = '', icon) {
  if (res.ok) toast({ title: okTitle || res.message, desc: okDesc, icon });
  else toast({ tone: 'neg', title: res.message || 'That didn’t work' });
  return res.ok;
}
on('note', n => toast({ title: esc(n.title), desc: esc(n.desc || ''), tone: n.tone || 'info' }));

export function copyText(text, host) {
  const done = () => {
    if (host) { host.classList.add('copied'); setTimeout(() => host.classList.remove('copied'), 1400); }
    toast({ title: 'Copied', desc: `<code>${esc(text)}</code>` });
  };
  try { navigator.clipboard.writeText(text).then(done, done); } catch (e) { done(); }
}

/* ---------------- popovers ---------------- */
let openPop = null;
export function closePop() {
  if (openPop) { openPop.el.remove(); openPop.btn.setAttribute('aria-expanded', 'false'); openPop = null; }
}
export function popover(btn, html, wire) {
  if (openPop && openPop.btn === btn) { closePop(); return; }
  closePop();
  const el = document.createElement('div');
  el.className = 'pop'; el.innerHTML = html;
  document.body.appendChild(el); hydrate(el);
  const r = btn.getBoundingClientRect(), w = el.offsetWidth;
  el.style.left = Math.max(12, Math.min(r.left, innerWidth - w - 12)) + 'px';
  el.style.top = (r.bottom + 6) + 'px';
  btn.setAttribute('aria-expanded', 'true');
  openPop = { el, btn };
  if (wire) wire(el);
  const f = el.querySelector('button, select, input'); if (f) f.focus({ preventScroll: true });
}
document.addEventListener('pointerdown', e => {
  if (openPop && !openPop.el.contains(e.target) && !openPop.btn.contains(e.target)) closePop();
});

/* ---------------- top bar: lake ---------------- */
function renderLake() {
  const name = S.route ? S.route.lake : null;
  $('#lakeName').textContent = name || 'No depth grid';
}
$('#scopeBtn').addEventListener('click', e => {
  const cur = S.route ? S.route.lake : null;
  const items = S.lakes.map(l => `<button class="pop-item ica" data-lake="${esc(l.name)}">${ic('layers')}<span class="grow">${esc(l.name)}<span class="s">${l.max_depth_m != null ? `Deepest ${l.max_depth_m.toFixed(1)} m · ` : ''}${l.spacing_m ? `${l.spacing_m} m grid` : 'overlay only'}</span></span>${l.name === cur ? ic('check') : ''}</button>`).join('');
  popover(e.currentTarget, `<div class="pop-h">Depth for the route and map</div>${items || '<div class="pop-h">No lakes in lake_depth_maps/</div>'}<div class="pop-sep"></div><button class="pop-item ica" data-lake="">${ic('x')}<span class="grow">Off<span class="s">No depth along the route</span></span>${cur ? '' : ic('check')}</button>`, el => {
    $$('[data-lake]', el).forEach(b => b.addEventListener('click', async () => {
      closePop();
      answer(await post('lake', { name: b.dataset.lake || null }));
    }));
  });
});

/* ---------------- top bar: connection ---------------- */
function connLabel(c) {
  if (!c || c.state !== 'connected') return 'Not connected';
  return c.label || c.device;
}
function renderConn() {
  const c = S.conn; if (!c) return;
  const live = c.state === 'connected';
  $('#connDot').className = 'dot ' + (live ? 'live' : 'off');
  $('#connName').textContent = connLabel(c);
  $('#connBtn').title = live ? `${c.device} at ${c.baud} baud` : 'Choose a port';
  $('#connMeta').textContent = live && c.device !== S.config?.simulator_url ? String(c.baud) : '';
}
export async function connectTo(device, baud) {
  const res = await post('connection', { action: 'connect', device, baud });
  answer(res);
  return res.ok;
}
export async function disconnect() {
  answer(await post('connection', { action: 'disconnect' }), null, '', 'plug');
}
export async function rescan(btn) {
  const i = btn ? $('.ic-refresh', btn) : null; if (i) i.classList.add('spinning');
  const res = await post('connection', { action: 'rescan' });
  setTimeout(() => { if (i) i.classList.remove('spinning'); }, 500);
  answer(res, res.message, '', 'refresh');
}
function portRows(c) {
  return c.ports.map(p => `<button class="pop-item ica" data-port="${esc(p.device)}">${ic(p.sim ? 'cube' : 'plug')}<span class="grow">${esc(p.sim ? 'Simulator' : p.device)}<span class="s">${esc(p.sim ? p.device : p.description)}${p.stlink ? ' · ST-Link' : ''}</span></span>${c.state === 'connected' && c.device === p.device ? ic('check') : ''}</button>`).join('');
}
$('#connBtn').addEventListener('click', e => {
  const c = S.conn; if (!c) return;
  const live = c.state === 'connected';
  const bauds = (S.config?.baud_rates || []).map(b => `<option${b === c.baud ? ' selected' : ''}>${b}</option>`).join('');
  popover(e.currentTarget, `
    <div class="pop-h">${live ? `Connected to ${esc(connLabel(c))}` : 'Choose a port'}</div>
    ${portRows(c)}
    <div class="pop-sep"></div>
    <form class="pop-form" id="popUrl">
      <div class="field"><label for="pUrl">Port or URL</label><input class="input" id="pUrl" placeholder="COM5 or socket://host:port" autocomplete="off" spellcheck="false" style="font-family:var(--mono);font-size:12.5px"></div>
      <div class="pop-row"><div class="field" style="flex:1"><label for="pBaud">Baud</label><span class="selw"><select class="select num" id="pBaud">${bauds}</select>${ic('updown')}</span></div></div>
    </form>
    <div class="pop-sep"></div>
    <button class="pop-item ica" data-act="rescan">${ic('refresh')}<span class="grow">Rescan ports</span><kbd>⇧R</kbd></button>
    ${live ? `<button class="pop-item ica" data-act="disc">${ic('plug')}<span class="grow">Disconnect</span></button>` : ''}`, el => {
    const baud = () => +$('#pBaud', el).value;
    $$('[data-port]', el).forEach(b => b.addEventListener('click', () => { closePop(); connectTo(b.dataset.port, baud()); }));
    $('#popUrl', el).addEventListener('submit', ev => { ev.preventDefault(); const v = $('#pUrl', el).value.trim(); if (v) { closePop(); connectTo(v, baud()); } });
    $('#pBaud', el).addEventListener('change', () => post('connection', { action: 'baud', baud: baud() }));
    $$('[data-act]', el).forEach(b => b.addEventListener('click', () => {
      if (b.dataset.act === 'rescan') { rescan(b); return; }
      closePop(); disconnect();
    }));
  });
});

/* ---------------- top bar: recording ---------------- */
export async function setRecording(on) {
  const res = await post('record', { on });
  if (!res.ok) { answer(res); return; }
  if (on) toast({ tone: 'info', icon: 'record', title: 'Recording', desc: `Writing <code>${esc(S.rec?.path || '')}</code>` });
  else toast({ title: 'Recording saved', desc: esc(res.message) });
}
function renderRec() {
  const r = S.rec, b = $('#recBtn'); if (!r) return;
  b.classList.toggle('on', r.active);
  b.dataset.tip = r.active ? 'Stop recording · R' : 'Start recording · R';
  b.innerHTML = r.active
    ? '<span class="dot rec recdot"></span><span class="stopsq"></span><span class="rlabel" id="recLabel">00:00</span>'
    : `${ic('record')}<span class="rlabel" id="recLabel">Record</span>`;
  tickRec();
}
function tickRec() {
  const r = S.rec, l = $('#recLabel');
  if (r && r.active && l && r.started) l.textContent = mmss(now() - r.started);
}
$('#recBtn').addEventListener('click', () => setRecording(!(S.rec && S.rec.active)));

/* ---------------- command menu ---------------- */
const simKnown = () => Boolean(S.conn && S.conn.sim && S.conn.sim.state !== 'off');
const commands = [];
export function addCommands(list) { commands.push(...list); }
let cmdk = null, cmdIdx = 0, cmdList = [];
const lab = c => typeof c.l === 'function' ? c.l() : c.l;
function fuzzy(text, q) {
  if (!q) return true;
  text = text.toLowerCase(); let i = 0;
  for (const ch of q.toLowerCase()) { i = text.indexOf(ch, i); if (i < 0) return false; i++; }
  return true;
}
export function openCmdk() {
  if (cmdk) return; closePop();
  cmdk = document.createElement('div'); cmdk.className = 'cmdk';
  cmdk.innerHTML = `<div class="cmdk-box" role="dialog" aria-modal="true" aria-label="Command menu"><div class="cmdk-in">${ic('search')}<input id="cmdkQ" placeholder="Search screens and actions" autocomplete="off" aria-label="Search screens and actions" aria-controls="cmdkL"><kbd>Esc</kbd></div><div class="cmdk-list scroll-thin" id="cmdkL" role="listbox"></div><div class="cmdk-ft"><span><kbd>↑</kbd><kbd>↓</kbd>to move</span><span><kbd>↵</kbd>to run</span><span style="margin-left:auto">Windbound</span></div></div>`;
  document.body.appendChild(cmdk);
  cmdk.addEventListener('pointerdown', e => { if (e.target === cmdk) closeCmdk(); });
  const q = $('#cmdkQ');
  q.addEventListener('input', () => { cmdIdx = 0; renderCmdk(); });
  q.addEventListener('keydown', cmdKeys);
  q.focus(); renderCmdk();
}
function closeCmdk() { if (!cmdk) return; cmdk.remove(); cmdk = null; $('#searchBtn').focus({ preventScroll: true }); }
function renderCmdk() {
  const q = $('#cmdkQ').value.trim();
  cmdList = commands.filter(c => (!c.when || c.when()) && fuzzy(lab(c) + ' ' + c.g, q));
  let h = '', g = null;
  cmdList.forEach((c, i) => {
    if (c.g !== g) { g = c.g; h += `<div class="cmdk-g">${g}</div>`; }
    const hint = c.hint ? c.hint() : '';
    h += `<button class="cmdk-it ica${i === cmdIdx ? ' is-active' : ''}" role="option" aria-selected="${i === cmdIdx}" data-i="${i}">${ic(c.ic)}<span class="grow">${esc(lab(c))}</span>${hint ? `<span class="hint">${esc(hint)}</span>` : ''}${c.k ? `<span class="keys">${c.k.map(k => `<kbd>${k}</kbd>`).join('')}</span>` : ''}</button>`;
  });
  const L = $('#cmdkL');
  L.innerHTML = h || `<div class="cmdk-empty">Nothing matches “${esc(q)}”.</div>`;
  $$('.cmdk-it', L).forEach(b => {
    b.addEventListener('mousemove', () => { if (cmdIdx !== +b.dataset.i) { cmdIdx = +b.dataset.i; markCmd(); } });
    b.addEventListener('click', () => runCmd(+b.dataset.i));
  });
  markCmd(true);
}
function markCmd(silent) {
  $$('.cmdk-it').forEach(b => {
    const isOn = +b.dataset.i === cmdIdx;
    b.classList.toggle('is-active', isOn); b.setAttribute('aria-selected', String(isOn));
    if (isOn) { if (!silent) play(b); b.scrollIntoView({ block: 'nearest' }); }
  });
}
function cmdKeys(e) {
  const n = Math.max(cmdList.length, 1);
  if (e.key === 'ArrowDown') { e.preventDefault(); cmdIdx = (cmdIdx + 1) % n; markCmd(); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); cmdIdx = (cmdIdx - 1 + n) % n; markCmd(); }
  else if (e.key === 'Enter') { e.preventDefault(); runCmd(cmdIdx); }
  else if (e.key === 'Escape') { e.preventDefault(); closeCmdk(); }
}
function runCmd(i) { const c = cmdList[i]; if (!c) return; closeCmdk(); setTimeout(c.run, 30); }
$('#searchBtn').addEventListener('click', openCmdk);
if (!/Mac|iPhone|iPad/.test(navigator.platform)) $('#cmdKey').textContent = 'Ctrl K';

addCommands([
  { g: 'Go to', l: 'Live', ic: 'sail', k: ['1'], run: () => go('live') },
  { g: 'Go to', l: 'Route', ic: 'route', k: ['2'], run: () => go('route') },
  { g: 'Go to', l: 'Logs', ic: 'terminal', k: ['3'], run: () => go('logs') },
  { g: 'Go to', l: 'Sessions', ic: 'history', k: ['4'], run: () => go('sessions') },
  { g: 'Go to', l: 'Settings', ic: 'sliders', k: ['5'], run: () => go('config') },
  { g: 'Go to', l: 'Mission control', ic: 'cube', k: ['6'], when: simKnown, run: () => go('mission') },
  { g: 'Go to', l: 'Firmware checks', ic: 'checks', k: ['7'], when: simKnown, run: () => go('checks') },
  { g: 'Boat', l: () => S.rec && S.rec.active ? 'Stop recording' : 'Start recording', ic: 'record', k: ['R'], run: () => setRecording(!(S.rec && S.rec.active)) },
  { g: 'Connection', l: 'Connect to the simulator', ic: 'cube', when: () => S.conn && S.conn.state !== 'connected', run: () => connectTo(S.config.simulator_url, S.conn.baud) },
  { g: 'Connection', l: () => `Disconnect ${connLabel(S.conn)}`, ic: 'plug', when: () => S.conn && S.conn.state === 'connected', run: disconnect },
  { g: 'Connection', l: 'Rescan COM ports', ic: 'refresh', k: ['⇧', 'R'], run: () => rescan() },
  { g: 'Theme', l: 'Use system theme', ic: 'monitor', run: () => applyTheme('system') },
  { g: 'Theme', l: 'Use light theme', ic: 'sun', run: () => applyTheme('light') },
  { g: 'Theme', l: 'Use dark theme', ic: 'moon', run: () => applyTheme('dark') },
]);

/* ---------------- keyboard ---------------- */
document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); cmdk ? closeCmdk() : openCmdk(); return; }
  if (e.key === 'Escape') { closePop(); emit('escape'); }
  const typing = e.target.closest && e.target.closest('input, select, textarea, [contenteditable]');
  if (typing || cmdk || e.metaKey || e.ctrlKey || e.altKey) return;
  if (/^[1-7]$/.test(e.key) && !$(`.tab[data-page="${PAGES[+e.key - 1]}"]`).hidden) go(PAGES[+e.key - 1]);
  else if (e.key === 'r' || e.key === 'R') { if (e.shiftKey) rescan(); else setRecording(!(S.rec && S.rec.active)); }
});

/* ---------------- tabs and links ---------------- */
$$('a[href^="#"]').forEach(a => a.addEventListener('click', e => {
  const h = a.getAttribute('href').slice(1);
  if (PAGES.includes(h)) { e.preventDefault(); go(h); }
}));
$$('[data-theme-set]').forEach(b => b.addEventListener('click', () => applyTheme(b.dataset.themeSet, true)));

/* ---------------- serial dock ---------------- */
export function lineHTML(text, type) {
  const e = esc(text);
  if (type === 'ctrl') return e.replace(/(\w+)=(-?\d+)/g, '<span class="k">$1=</span><span class="v">$2</span>');
  if (type === 'xbee') return e.replace(/&quot;(\w+)&quot;:(-?[\d.]+)/g, '<span class="k">&quot;$1&quot;:</span><span class="v">$2</span>');
  return e;
}
function dockLine(l) { return `<div class="dl"><time>${hmsms(at(l.t))}</time>${lineHTML(l.text, l.type)}</div>`; }
on('lines', lines => {
  const last = lines[lines.length - 1];
  $('#dockLine').innerHTML = `<span class="t">${hmsms(at(last.t))}</span>${lineHTML(last.text, last.type)}`;
  const db = $('#dockBody');
  if (!db.hidden) {
    db.insertAdjacentHTML('beforeend', lines.map(dockLine).join(''));
    while (db.children.length > 150) db.firstElementChild.remove();
    db.scrollTop = db.scrollHeight;
  }
});
$('#dockTg').addEventListener('click', e => {
  const b = e.currentTarget, open = b.getAttribute('aria-expanded') !== 'true';
  b.setAttribute('aria-expanded', String(open));
  const db = $('#dockBody'); db.hidden = !open;
  if (open) { db.innerHTML = S.lines.slice(-80).map(dockLine).join(''); db.scrollTop = db.scrollHeight; }
});
function renderDockNote() {
  const c = S.conn;
  $('#dockNote').textContent = c && c.state === 'connected'
    ? (c.device === S.config?.simulator_url ? 'Simulator' : `${c.device} · ${c.baud}`) : 'Not connected';
}

/* ---------------- lost connection to the station ---------------- */
on('offline', () => { $('#lost').hidden = false; });
on('online', () => { $('#lost').hidden = true; });

on('hello', () => {
  renderLake(); renderConn(); renderRec(); renderDockNote();
  const last = S.lines[S.lines.length - 1];
  if (last) $('#dockLine').innerHTML = `<span class="t">${hmsms(at(last.t))}</span>${lineHTML(last.text, last.type)}`;
});
on('route', renderLake);
on('conn', () => { renderConn(); renderDockNote(); });
on('rec', renderRec);
on('tick', tickRec);
on('log_cleared', () => { $('#dockLine').innerHTML = ''; $('#dockBody').innerHTML = ''; });
