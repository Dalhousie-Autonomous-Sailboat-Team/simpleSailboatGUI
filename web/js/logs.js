// Logs: every line from the controller, newest first, with filters and the
// parsed fields of each XBee packet.

import { $, $$ } from './icons.js';
import { S, on } from './state.js';
import { post } from './api.js';
import { onPage, currentPage, lineHTML, answer } from './shell.js';
import { nf, hmsms, at, neg, esc } from './format.js';

const TYPE_NAME = { ctrl: 'Ctrl', xbee: 'XBee', tx: 'TX', app: 'App', other: 'Other' };
const FIELDS = [['tb', 'Target bearing', 1], ['tlat', 'Target latitude'], ['tlon', 'Target longitude'], ['tsa', 'Target sail', 1], ['tfa', 'Target flap', 1], ['tra', 'Target rudder', 1], ['clat', 'Latitude'], ['clon', 'Longitude'], ['cb', 'Heading', 1], ['wa', 'Apparent wind', 1], ['sa', 'Sail angle', 1]];
const SHOWN = 300;
const types = { ctrl: true, xbee: true, tx: true, app: true, other: true };
let q = '', paused = false, CUT = 110;
const open = new Set();

function measureCut() {
  const b = $('#lbody'); if (!b || !b.clientWidth) return;
  CUT = innerWidth <= 760 ? 150 : Math.max(40, Math.min(220, Math.floor((b.clientWidth - 112 - 64 - 24 - 28 - 24) / 7.8)));
}
function rowHTML(l) {
  const isOpen = open.has(l.id), long = l.text.length > CUT;
  let det = '';
  if (isOpen && l.type === 'xbee') {
    const kv = {}; l.text.replace(/"(\w+)":(-?[\d.]+)/g, (m, k, v) => { kv[k] = v; });
    det = `<div class="ldet">${FIELDS.map(([k, n, d]) => `<div><span>${n} <span class="g">(${k})</span></span><b>${kv[k] !== undefined ? neg(kv[k]) + (d ? '°' : '') : '—'}</b></div>`).join('')}</div>`;
  } else if (isOpen) {
    det = `<div class="ldet ldet-full"><code>${lineHTML(l.text, l.type)}</code></div>`;
  }
  const msg = long ? lineHTML(l.text.slice(0, CUT), l.type) + '<span class="k">…</span>' : lineHTML(l.text, l.type);
  return `<div class="lrow ${l.type}${long ? ' long' : ''}${isOpen ? ' open' : ''}" data-id="${l.id}"><span class="meta-l"><time>${hmsms(at(l.t))}</time><span class="lt"><i class="t-${l.type}"></i>${TYPE_NAME[l.type] || l.type}</span></span><code class="msg">${msg}</code>${det}</div>`;
}
const matches = l => types[l.type] !== false && (!q || l.text.toLowerCase().includes(q) || (TYPE_NAME[l.type] || '').toLowerCase() === q);
function counts() {
  const c = { ctrl: 0, xbee: 0, tx: 0, app: 0, other: 0 };
  S.lines.forEach(l => { c[l.type] = (c[l.type] || 0) + 1; });
  Object.keys(c).forEach(k => { const el = $('#cnt-' + k); if (el) el.textContent = nf(c[k]); });
  const hit = S.lines.reduce((a, l) => a + (matches(l) ? 1 : 0), 0);
  $('#lmatch').textContent = `${nf(hit)} of ${nf(S.lines.length)} lines`;
}
function render() {
  measureCut();
  const shown = [];
  for (let i = S.lines.length - 1; i >= 0 && shown.length < SHOWN; i--) if (matches(S.lines[i])) shown.push(S.lines[i]);
  $('#lbody').innerHTML = shown.map(rowHTML).join('')
    || `<div class="lempty">${S.lines.length ? `No lines match “${esc(q)}”.` : 'Lines from the controller appear here as they arrive.'}</div>`;
  counts();
}

$('#lbody').addEventListener('click', e => {
  const r = e.target.closest('.lrow.xbee, .lrow.long'); if (!r) return;
  const id = +r.dataset.id;
  if (open.has(id)) open.delete(id); else open.add(id);
  const l = S.lines.find(x => x.id === id); if (l) r.outerHTML = rowHTML(l);
});
$$('.fchk input').forEach(c => c.addEventListener('change', () => { types[c.dataset.type] = c.checked; render(); }));
$('#lq').addEventListener('input', e => { q = e.target.value.trim().toLowerCase(); render(); });
$('#liveBtn').addEventListener('click', e => {
  paused = !paused;
  e.currentTarget.setAttribute('aria-pressed', String(!paused));
  $('#liveT').textContent = paused ? 'Paused' : 'Live';
  if (!paused) render();
});
$('#lclear').addEventListener('click', async () => {
  answer(await post('log', { action: 'clear' }), 'Cleared the log view', 'Recording to CSV carries on.', 'trash');
});

let lastCounts = 0;
on('lines', lines => {
  if (currentPage() !== 'logs') return;
  if (!paused) {
    const body = $('#lbody');
    const add = lines.filter(matches).reverse();
    if (add.length) {
      if (body.querySelector('.lempty')) body.innerHTML = '';
      body.insertAdjacentHTML('afterbegin', add.map(rowHTML).join(''));
      $$('.lrow', body).slice(0, add.length).forEach(r => r.classList.add('fresh-in'));
      while (body.children.length > SHOWN) body.lastElementChild.remove();
    }
  }
  const t = performance.now();
  if (t - lastCounts > 400) { lastCounts = t; counts(); }
});
on('hello', () => { if (currentPage() === 'logs') render(); });
on('log_cleared', () => { open.clear(); render(); });
onPage('logs', render);
addEventListener('resize', () => { if (currentPage() === 'logs') { const old = CUT; measureCut(); if (old !== CUT) render(); } });
