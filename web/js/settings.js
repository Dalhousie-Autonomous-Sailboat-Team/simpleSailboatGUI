// Settings: connection controls that apply at once, the values the code sets,
// the depth grids, the offline tile cache and the theme.

import { $, $$, play } from './icons.js';
import { S, on } from './state.js';
import { post } from './api.js';
import { answer } from './shell.js';
import { esc, nf, bytes } from './format.js';
import { STYLES, setStyle } from './route.js';

$$('.subnav button').forEach(b => b.addEventListener('click', () => {
  $$('.subnav button').forEach(x => x.setAttribute('aria-current', String(x === b)));
  $$('.sview').forEach(v => { v.hidden = v.dataset.view !== b.dataset.sv; });
  play(b);
}));

function render() {
  const c = S.config, conn = S.conn; if (!c || !conn) return;
  $('#sBaud').innerHTML = c.baud_rates.map(b => `<option${b === conn.baud ? ' selected' : ''}>${b}</option>`).join('');
  $('#sAuto').checked = conn.autoconnect;
  $('#simUrl').textContent = c.simulator_url;
  $('#cfgRate').textContent = `${c.sail_rate_dps.toFixed(1)} °/s`;
  $('#cfgDead').textContent = `±${c.sail_deadband.toFixed(1)}`;
  $('#cfgDraft').textContent = `${c.boat_draft_m.toFixed(1)} m`;
  $('#cfgSafe').textContent = `${c.safe_depth_m.toFixed(1)} m`;
  const cur = S.route ? S.route.lake : null;
  $('#lakeList').innerHTML = S.lakes.map(l => `<div class="p"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 9 5-9 5-9-5z"/><path d="m3 12.5 9 5 9-5"/><path d="m3 16.5 9 5 9-5"/></svg><div class="grow"><div>${esc(l.name)}</div><div class="s num">${l.cells ? `${nf(l.cells[0])} × ${nf(l.cells[1])} cells · ` : 'No depth grid · '}${l.spacing_m ? `${l.spacing_m} m spacing · ` : ''}${l.max_depth_m != null ? `deepest ${l.max_depth_m.toFixed(1)} m · ` : ''}±${l.uncertainty_m.toFixed(1)} m</div></div>${l.name === cur ? '<span class="pill pos"><span class="dot"></span>In use</span>' : '<span class="pill">Ready</span>'}</div>`).join('') || '<div class="p"><div class="grow">No lakes found.</div></div>';
  renderTiles();
  $('#sStyle').innerHTML = STYLES.map(s => `<option value="${s}">${s === 'Auto' ? 'Auto (muted to the theme)' : s === 'None' ? 'None (depth contours only)' : s}</option>`).join('');
  $('#sStyle').value = localStorage.getItem('wb-map-style') || 'Auto';
}
function renderTiles() {
  const t = S.tiles; if (!t) return;
  $('#tileCount').textContent = t.count != null ? nf(t.count) : '—';
  $('#tileSize').textContent = bytes(t.bytes);
  $('#tilePath').textContent = t.path;
}

$('#sBaud').addEventListener('change', async e => {
  answer(await post('connection', { action: 'baud', baud: +e.target.value }), `Baud set to ${e.target.value}`, 'Used the next time a port is opened.');
});
$('#sAuto').addEventListener('change', async e => {
  answer(await post('connection', { action: 'autoconnect', on: e.target.checked }), null, '', 'refresh');
});
$('#sStyle').addEventListener('change', e => setStyle(e.target.value));

on('hello', render);
on('conn', render);
on('route', render);
on('tiles', renderTiles);
