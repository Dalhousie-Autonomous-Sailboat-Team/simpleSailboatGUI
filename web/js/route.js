// Route: the map with the route coloured by depth, the boat and its track,
// and the panel to edit, check and send the waypoint list.

import { $, $$, ic } from './icons.js';
import { S, on, routeTarget } from './state.js';
import { getJSON } from './api.js';
import { onPage, currentPage, toast, copyText, popover, closePop, store, addCommands, go } from './shell.js';
import { SlippyMap, TileLayer } from './map.js';
import { isNum, km, coord, esc, hdg, wrap, r0 } from './format.js';
import { routeCmd, sendRoute, syncPill, underDraft, profileBar } from './routeops.js';

// CartoDB's free tiles now answer with an "API key required" image, so the
// base map is OpenStreetMap: muted to the theme (Auto), in full colour, or none.
const STYLES = ['Auto', 'OpenStreetMap', 'None'];
const STYLE_NOTE = { Auto: 'Muted to the theme', OpenStreetMap: 'Full colour', None: 'Depth contours only' };
let style = store.get('wb-map-style') || 'Auto';
if (!STYLES.includes(style)) style = 'Auto';
const baseUrl = () => style === 'None' ? null : (z, x, y) => `/tiles/base/OpenStreetMap/${z}/${x}/${y}`;
export function setStyle(s) {
  style = s; store.set('wb-map-style', s);
  $('#styleT').textContent = s;
  $('#lyrBase').classList.toggle('muted', s === 'Auto');
  base.setUrl(baseUrl());
  map.render(); renderAttribution();
  const sel = $('#sStyle'); if (sel) sel.value = s;
}
export { STYLES };

/* ---------------- the map ---------------- */
const mapEl = $('#map');
const map = new SlippyMap(mapEl, { onView: placeAll, onClick, onContext, onMove });
const base = map.addLayer(new TileLayer($('#lyrBase'), { url: baseUrl() }));
$('#lyrBase').classList.toggle('muted', style === 'Auto');
const depth = map.addLayer(new TileLayer($('#lyrDepth'), { url: null, mask: true }));
const sea = map.addLayer(new TileLayer($('#lyrSea'), { url: (z, x, y) => `/tiles/seamarks/${z}/${x}/${y}`, maxZoom: 18 }));
sea.setVisible(false);
let fitted = false, adding = false, ctxPt = null, moveId = null;

function lakeInfo() { return S.lakes.find(l => l.name === (S.route && S.route.lake)) || null; }
function setDepthLayer() {
  const l = lakeInfo();
  if (!l) { depth.url = null; depth.clear(); renderAttribution(); return; }
  // The calibrated overlay is rotated and scaled a little past its bounds.
  const padLat = (l.north - l.south) * 0.12, padLon = (l.east - l.west) * 0.12;
  depth.bounds = [l.north + padLat, l.south - padLat, l.west - padLon, l.east + padLon];
  depth.setUrl((z, x, y) => `/tiles/depth/${encodeURIComponent(l.name)}/${z}/${x}/${y}.png`);
  map.render(); renderAttribution();
}
function renderAttribution() {
  const parts = style === 'None' ? [] : ['© OpenStreetMap contributors'];
  if (sea.visible) parts.push('Seamarks © OpenSeaMap');
  if (depth.url && depth.visible) parts.push('Depth: NS Lake Inventory, approximate, not for navigation');
  $('#mattr').textContent = parts.join(' · ');
}

function fitView() {
  const pts = (S.route ? S.route.items.map(w => [w.lat, w.lon]) : []);
  if (S.tick && S.tick.position) pts.push(S.tick.position);
  if (pts.length >= 2 && map.fit(pts)) return;
  if (pts.length === 1) { map.setView(pts[0][0], pts[0][1], 16); return; }
  const l = lakeInfo() || S.lakes[0];
  if (l) map.fit([[l.north, l.west], [l.south, l.east]], 40);
}

/* ---------------- drawing in screen space ---------------- */
let markerEls = new Map();
function renderMarkers() {
  const box = $('#markers'); box.innerHTML = ''; markerEls = new Map();
  const r = S.route; if (!r) return;
  const { index } = routeTarget();
  r.items.forEach((w, i) => {
    const cls = index < 0 ? '' : i < index ? 'reached' : i === index ? 'active' : '';
    const el = document.createElement('div');
    el.className = 'wpm' + (w._new ? ' new' : '');
    el.innerHTML = `<span class="wpn ${cls}" title="Drag to move ${esc(w.name)}">${i + 1}</span><span class="lbl">${esc(w.name)}</span>`;
    el.addEventListener('mouseenter', () => hl(w.id));
    el.addEventListener('mouseleave', () => hl(null));
    dragMarker(el, w);
    box.appendChild(el); markerEls.set(w.id, el);
  });
  const u = underDraft(r);
  if (u) {
    const leg = r.legs[u.i], a = r.items[u.i], b = r.items[u.i + 1];
    let f = 0, mid = 0.5;
    for (const [c, frac] of leg.segments) { if (c === 'under') { mid = f + frac / 2; break; } f += frac; }
    const c = document.createElement('div');
    c.className = 'callout'; c.dataset.lat = a.lat + (b.lat - a.lat) * mid; c.dataset.lon = a.lon + (b.lon - a.lon) * mid;
    c.textContent = `${r0(u.m)} m under the draft`;
    box.appendChild(c);
  }
  const t = document.createElement('div'); t.className = 'tgtm'; t.id = 'tgtM'; t.hidden = true; box.appendChild(t);
  const b = document.createElement('div'); b.className = 'boatm'; b.id = 'boatM'; b.hidden = true;
  b.innerHTML = '<svg viewBox="0 0 36 36" aria-hidden="true"><circle class="halo" cx="18" cy="18" r="10"/><path class="hullm" d="M18 3.5 C 22.5 9, 24 15, 23.6 22 C 23.3 26.5, 22.4 29.5, 21.4 31.5 L 14.6 31.5 C 13.6 29.5, 12.7 26.5, 12.4 22 C 12 15, 13.5 9, 18 3.5 Z"/><circle cx="18" cy="15" r="1.6" fill="var(--surface)"/></svg>';
  box.appendChild(b);
  placeAll();
}
function placeAll() {
  if (currentPage() !== 'route') return;
  const r = S.route;
  if (r) r.items.forEach(w => {
    const el = markerEls.get(w.id); if (!el) return;
    const p = map.project(w.lat, w.lon);
    el.style.transform = `translate(${(p.x - 11).toFixed(1)}px, ${(p.y - 11).toFixed(1)}px)`;
  });
  const c = $('.callout');
  if (c) { const p = map.project(+c.dataset.lat, +c.dataset.lon); c.style.left = p.x + 'px'; c.style.top = p.y + 'px'; }
  const t = S.tick, boat = $('#boatM'), tgt = $('#tgtM');
  if (boat) {
    boat.hidden = !(t && t.position);
    if (t && t.position) {
      const p = map.project(t.position[0], t.position[1]);
      const h = isNum(t.telemetry.cb) ? t.telemetry.cb : 0;
      boat.style.transform = `translate(${p.x.toFixed(1)}px, ${p.y.toFixed(1)}px) rotate(${h.toFixed(1)}deg)`;
    }
  }
  if (tgt) {
    const show = !!(t && t.target && t.target_id == null);
    tgt.hidden = !show;
    if (show) { const p = map.project(t.target[0], t.target[1]); tgt.style.transform = `translate(${p.x.toFixed(1)}px, ${p.y.toFixed(1)}px)`; }
  }
  drawLines();
  const mpp = map.metresPerPixel(), target = 90 * mpp;
  const opts = [5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000, 20000];
  const L = opts.reduce((b, o) => Math.abs(o - target) < Math.abs(b - target) ? o : b, 100);
  $('#mscaleT').textContent = L >= 1000 ? `${L / 1000} km` : `${L} m`;
  $('#mscaleB').style.width = (L / mpp).toFixed(0) + 'px';
}
function drawLines() {
  const r = S.route, svg = $('#routeSvg');
  let h = '';
  const pt = (lat, lon) => { const p = map.project(lat, lon); return `${p.x.toFixed(1)},${p.y.toFixed(1)}`; };
  if ($('#trackTg').getAttribute('aria-pressed') === 'true' && S.track.length > 1) {
    h += `<polyline class="track" points="${S.track.map(p => pt(p[0], p[1])).join(' ')}"/>`;
  }
  if (r && r.items.length > 1) {
    const { index } = routeTarget();
    r.legs.forEach((leg, i) => { const a = r.items[i], b = r.items[i + 1]; h += `<polyline class="case" points="${pt(a.lat, a.lon)} ${pt(b.lat, b.lon)}"/>`; });
    r.legs.forEach((leg, i) => {
      const a = r.items[i], b = r.items[i + 1], done = index > 0 && i < index - 1;
      let f = 0;
      for (const [cls, frac] of leg.segments) {
        const f1 = f + frac;
        h += `<polyline class="leg ${cls}${done ? ' done' : ''}" points="${pt(a.lat + (b.lat - a.lat) * f, a.lon + (b.lon - a.lon) * f)} ${pt(a.lat + (b.lat - a.lat) * f1, a.lon + (b.lon - a.lon) * f1)}"/>`;
        f = f1;
      }
    });
  }
  const t = S.tick;
  if (t && t.position && t.target) h += `<polyline class="ahead" points="${pt(t.position[0], t.position[1])} ${pt(t.target[0], t.target[1])}"/>`;
  svg.innerHTML = h;
}
function hl(id) {
  $$('.wpm').forEach(el => el.classList.remove('hl')); $$('.wpr').forEach(el => el.classList.remove('hl'));
  if (id == null) return;
  const m = markerEls.get(id); if (m) m.classList.add('hl');
  const row = $(`.wpr[data-id="${id}"]`); if (row) row.classList.add('hl');
}

// Drag a waypoint marker to move it.
function dragMarker(el, w) {
  el.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    e.stopPropagation(); e.preventDefault();
    el.setPointerCapture(e.pointerId);
    const r = mapEl.getBoundingClientRect();
    let moved = false, last = null;
    const move = ev => {
      moved = true; el.classList.add('dragging');
      last = map.unproject(ev.clientX - r.left, ev.clientY - r.top);
      const p = map.project(last.lat, last.lon);
      el.style.transform = `translate(${(p.x - 11).toFixed(1)}px, ${(p.y - 11).toFixed(1)}px)`;
    };
    const up = async () => {
      el.removeEventListener('pointermove', move); el.removeEventListener('pointerup', up);
      el.classList.remove('dragging');
      if (moved && last) await routeCmd('update', { id: w.id, lat: last.lat, lon: last.lon });
    };
    el.addEventListener('pointermove', move); el.addEventListener('pointerup', up);
  });
}

/* ---------------- map interactions ---------------- */
function onMove(p) { $('#mread').textContent = coord(p.lat, p.lon, 5); }
async function onClick(p) {
  $('#ctx').hidden = true;
  if (adding) await addWaypoint(p.lat, p.lon);
}
function onContext(p, x, y) {
  ctxPt = p;
  const c = $('#ctx'); c.hidden = false;
  $('#ctxH').textContent = coord(p.lat, p.lon, 5);
  const r = S.route, near = r ? nearest(p) : null;
  moveId = near ? near.id : null;
  $('#ctxMove').hidden = !near;
  if (near) $('#ctxMoveT').textContent = `Move ${near.name} here`;
  c.style.left = Math.min(x, mapEl.clientWidth - 250) + 'px';
  c.style.top = Math.min(y, mapEl.clientHeight - 190) + 'px';
  c.querySelector('button').focus({ preventScroll: true });
}
// The waypoint nearest the pointer, if it's within 400 m.
function nearest(p) {
  let best = null, bd = 400;
  for (const w of S.route.items) {
    const d = Math.hypot((w.lat - p.lat) * 111195, (w.lon - p.lon) * 111195 * Math.cos(p.lat * Math.PI / 180));
    if (d < bd) { bd = d; best = w; }
  }
  return best;
}
document.addEventListener('pointerdown', e => { const c = $('#ctx'); if (!c.hidden && !c.contains(e.target)) c.hidden = true; });
$$('[data-ctx]').forEach(b => b.addEventListener('click', async () => {
  $('#ctx').hidden = true; const p = ctxPt; if (!p) return;
  const what = b.dataset.ctx;
  if (what === 'add') await addWaypoint(p.lat, p.lon);
  if (what === 'move' && moveId != null) await routeCmd('update', { id: moveId, lat: p.lat, lon: p.lon });
  if (what === 'depth') {
    const d = await getJSON(`/api/depth?lat=${p.lat}&lon=${p.lon}`);
    if (!d) toast({ tone: 'neg', title: 'Windbound isn’t answering' });
    else if (!d.ok) toast({ tone: 'warn', icon: 'layers', title: 'No depth here', desc: esc(d.reason) });
    else {
      const draft = S.config.boat_draft_m, safe = S.config.safe_depth_m;
      const tone = d.depth_m < draft ? 'neg' : d.depth_m < safe ? 'warn' : 'info';
      const verdict = d.depth_m < draft ? `Under the ${draft.toFixed(1)} m draft` : d.depth_m < safe ? `Under the ${safe.toFixed(1)} m safe depth` : 'Deep enough';
      toast({ tone, icon: 'layers', title: `About ${d.depth_m.toFixed(1)} m deep · ${verdict}`, desc: `±${d.uncertainty_m.toFixed(1)} m at ${coord(p.lat, p.lon, 5)}, from the ${esc(d.lake)} grid.` });
    }
  }
  if (what === 'copy') copyText(coord(p.lat, p.lon, 6).replace(/−/g, '-'), b);
}));
async function addWaypoint(lat, lon) {
  const res = await routeCmd('add', { lat, lon });
  if (res.ok) toast({ icon: 'pin', title: esc(res.message), desc: `${coord(lat, lon, 5)} · not sent yet` });
}
function setAdding(v) {
  adding = v; $('#addMode').setAttribute('aria-pressed', String(v)); mapEl.classList.toggle('adding', v);
}
$('#addMode').addEventListener('click', () => {
  setAdding(!adding);
  if (adding) toast({ tone: 'info', icon: 'pin', title: 'Click the map to add a waypoint', desc: 'Press Esc or click Add waypoint again to stop.' });
});
on('escape', () => { $('#ctx').hidden = true; if (adding) setAdding(false); });
$('#zIn').addEventListener('click', () => map.zoomBy(1));
$('#zOut').addEventListener('click', () => map.zoomBy(-1));
$('#zFit').addEventListener('click', fitView);
$('#depthTg').addEventListener('click', e => {
  const v = e.currentTarget.getAttribute('aria-pressed') !== 'true';
  e.currentTarget.setAttribute('aria-pressed', String(v)); depth.setVisible(v); map.render(); renderAttribution();
});
$('#opac').addEventListener('input', e => { $('#opacV').textContent = e.target.value + '%'; $('#lyrDepth').style.opacity = e.target.value / 100; });
$('#lyrDepth').style.opacity = 0.8;
$('#trackTg').addEventListener('click', e => { const v = e.currentTarget.getAttribute('aria-pressed') !== 'true'; e.currentTarget.setAttribute('aria-pressed', String(v)); drawLines(); });
$('#seaTg').addEventListener('click', e => {
  const v = e.currentTarget.getAttribute('aria-pressed') !== 'true';
  e.currentTarget.setAttribute('aria-pressed', String(v)); sea.setVisible(v); map.render(); renderAttribution();
});
$('#styleBtn').addEventListener('click', e => popover(e.currentTarget, `<div class="pop-h">Base map</div>${STYLES.map(s => `<button class="pop-item ica" data-style="${s}">${ic('map')}<span class="grow">${s}<span class="s">${STYLE_NOTE[s]}</span></span>${s === style ? ic('check') : ''}</button>`).join('')}`, el => {
  $$('[data-style]', el).forEach(b => b.addEventListener('click', () => { closePop(); setStyle(b.dataset.style); }));
}));

/* ---------------- the panel ---------------- */
let dragFrom = null, clearArm = 0;
function renderPanel() {
  const r = S.route; if (!r) return;
  const total = r.legs.reduce((a, l) => a + l.length_m, 0);
  $('#rpSub').textContent = r.items.length
    ? `${r.items.length} waypoint${r.items.length === 1 ? '' : 's'} · ${km(total)} · ${r.lake || 'no depth grid'}`
    : `No waypoints · ${r.lake || 'no depth grid'}`;
  syncPill($('#rpPill'), r);
  const draft = S.config.boat_draft_m, safe = S.config.safe_depth_m;
  $('#draftNote').textContent = `Draft ${draft.toFixed(1)} m`;
  $('#keyDeep').textContent = `${safe.toFixed(1)} m or deeper`;
  $('#keyShallow').textContent = `${draft.toFixed(1)}–${safe.toFixed(1)} m`;
  let nt = '';
  if (r.changes) nt += `<div class="notice warn">${ic('send')}<span>${r.changes} change${r.changes > 1 ? 's' : ''} since the route was last sent.</span></div>`;
  const u = underDraft(r);
  if (u) nt += `<div class="notice neg">${ic('alert', 'shake')}<span>Leg ${u.i + 1} → ${u.i + 2} runs <b>${r0(u.m)} m</b> under the ${draft.toFixed(1)} m draft. Drag ${esc(r.items[u.i + 1].name)} or add a waypoint to go round the shallows.</span></div>`;
  if (!r.lake && r.items.length > 1) nt += `<div class="notice warn">${ic('layers')}<span>No depth grid is selected, so the legs aren’t checked against the lake. Choose a lake in the top bar.</span></div>`;
  $('#notices').innerHTML = nt;

  const { index } = routeTarget();
  const list = $('#wpl');
  if (!r.items.length) {
    list.innerHTML = '<li class="empty-row" style="text-align:left;padding:12px 8px">Right-click the map, or use Add waypoint, to start a route.</li>';
  } else {
    list.innerHTML = r.items.map((w, i) => {
      const cls = index < 0 ? '' : i < index ? 'reached' : i === index ? 'active' : '';
      const chg = w.change ? { new: 'New', renamed: 'Renamed', moved: 'Moved' }[w.change] : '';
      const status = chg ? `<span class="st chg">${chg}</span>`
        : cls === 'active' ? `<span class="st act">${isNum(S.tick?.target_m) ? km(S.tick.target_m) + ' to go' : 'Target'}</span>`
          : cls === 'reached' ? '<span class="st passed">Passed</span>' : '<span class="st"></span>';
      return `<li class="wpr" draggable="true" data-id="${w.id}" data-i="${i}"><span class="grip" aria-hidden="true">${ic('grip')}</span><span class="wpn ${cls}">${i + 1}</span><span class="nmw"><input class="nm" value="${esc(w.name)}" aria-label="Name of waypoint ${i + 1}" maxlength="60"><span class="co">${coord(w.lat, w.lon, 5)}${isNum(w.depth_m) ? ` · ${w.depth_m.toFixed(1)} m` : ''}</span></span>${status}<button class="btn ghost sm icon rm ica" aria-label="Remove ${esc(w.name)}">${ic('x')}</button></li>`;
    }).join('');
  }
  $$('.wpr', list).forEach(row => {
    const id = +row.dataset.id;
    row.addEventListener('mouseenter', () => hl(id)); row.addEventListener('mouseleave', () => hl(null));
    const nm = $('.nm', row);
    nm.addEventListener('change', () => { const v = nm.value.trim(); if (v) routeCmd('rename', { id, name: v }); });
    nm.addEventListener('keydown', e => { if (e.key === 'Enter') nm.blur(); });
    $('.rm', row).addEventListener('click', async () => {
      const w = r.items.find(x => x.id === id);
      const res = await routeCmd('remove', { id });
      if (res.ok) toast({ tone: 'info', icon: 'trash', title: `Removed ${esc(w.name)}`, desc: r.sent ? 'Restore brings back the route last sent.' : '' });
    });
    row.addEventListener('dragstart', e => { dragFrom = +row.dataset.i; row.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; try { e.dataTransfer.setData('text/plain', String(id)); } catch (x) {} });
    row.addEventListener('dragend', () => { row.classList.remove('dragging'); $$('.wpr').forEach(x => x.classList.remove('drag-over')); });
    row.addEventListener('dragover', e => { e.preventDefault(); $$('.wpr').forEach(x => x.classList.remove('drag-over')); row.classList.add('drag-over'); });
    row.addEventListener('drop', e => {
      e.preventDefault(); const to = +row.dataset.i;
      if (dragFrom !== null && dragFrom !== to) routeCmd('move', { from: dragFrom, to });
      dragFrom = null;
    });
  });

  $('#legs').innerHTML = r.legs.map((leg, i) => {
    const m = leg.metres, under = m.under || 0, shallow = m.shallow || 0, unknown = m.unknown || 0;
    const low = isNum(leg.min_depth_m) ? ` · shallowest ${leg.min_depth_m.toFixed(1)} m` : '';
    const note = !r.lake ? '<div class="note">Not checked: no depth grid</div>'
      : under >= 1 ? `<div class="note bad">${r0(under)} m under the draft${low}</div>`
        : shallow >= 1 ? `<div class="note mid">${r0(shallow)} m between ${draft.toFixed(1)} and ${safe.toFixed(1)} m${low}</div>`
          : unknown >= leg.length_m - 1 ? '<div class="note">Outside the depth grid</div>'
            : `<div class="note">${low ? low.slice(3, 4).toUpperCase() + low.slice(4) : 'Deep enough'}${unknown >= 1 ? ` · ${r0(unknown)} m without grid values` : ''}</div>`;
    return `<div class="legr"><span class="n">${i + 1} → ${i + 2}</span>${profileBar(leg).replace('class="prof"', 'class="prof" role="img" aria-label="Depth along the leg"')}<span class="len">${km(leg.length_m)}</span>${note}</div>`;
  }).join('') || '<div class="note" style="color:var(--faint)">Add two waypoints to see the legs.</div>';

  $('#sendBtn').disabled = !r.items.length || (!r.changes && r.sent > 0);
  $('#restoreBtn').disabled = !r.changes || !r.sent;
  $('#clearBtn').disabled = !r.items.length;
}
$('#sendBtn').addEventListener('click', async e => {
  const b = e.currentTarget, old = b.innerHTML;
  b.classList.add('busy'); b.innerHTML = '<span class="spinner"></span>Sending';
  await sendRoute();
  b.classList.remove('busy'); b.innerHTML = old;
});
$('#restoreBtn').addEventListener('click', async () => {
  const res = await routeCmd('restore');
  if (res.ok) toast({ icon: 'undo', title: 'Restored the route last sent', desc: `${S.route.sent} waypoints` });
});
$('#clearBtn').addEventListener('click', async () => {
  const n = S.route ? S.route.items.length : 0;
  if (Date.now() - clearArm > 3000) {
    clearArm = Date.now(); $('#clearT').textContent = `Clear ${n}?`;
    setTimeout(() => { $('#clearT').textContent = 'Clear'; }, 3000);
    return;
  }
  clearArm = 0; $('#clearT').textContent = 'Clear';
  const res = await routeCmd('clear');
  if (res.ok) toast({ tone: 'warn', icon: 'trash', title: `Cleared ${n} waypoints`, desc: S.route.sent ? 'Restore brings back the route last sent.' : '' });
});

/* ---------------- wind badge ---------------- */
function renderWind() {
  const tel = S.tick ? S.tick.telemetry : {};
  const ok = isNum(tel.cb) && isNum(tel.wa);
  $('#windB').hidden = !ok;
  if (!ok) return;
  const from = wrap(tel.cb + tel.wa);
  $('#windBT').textContent = hdg(from);
  // the arrow points the way the wind blows, i.e. away from where it comes from
  $('#windBArrow').setAttribute('transform', `rotate(${from.toFixed(0)} 12 12)`);
}

/* ---------------- wiring ---------------- */
function renderAll() { renderPanel(); renderMarkers(); }
on('hello', () => { setDepthLayer(); renderAttribution(); renderAll(); if (currentPage() === 'route') { fitView(); fitted = true; } });
on('route', prev => { setDepthLayer(); renderAll(); });
on('tick', () => { if (currentPage() === 'route') { placeAll(); renderWind(); } });
let lastTarget;
on('packets', () => {
  if (currentPage() !== 'route') return;
  const id = S.tick ? S.tick.target_id : null;
  if (id !== lastTarget) { lastTarget = id; renderAll(); } else renderPanelStatus();
});
function renderPanelStatus() {
  const { index } = routeTarget();
  const row = index >= 0 ? $(`.wpr[data-i="${index}"] .st.act`) : null;
  if (row && isNum(S.tick?.target_m)) row.textContent = km(S.tick.target_m) + ' to go';
}
onPage('route', () => {
  map.resize();
  if (!fitted && S.ready) { fitView(); fitted = true; }
  map.render(); renderAll(); renderWind();
});
$('#styleT').textContent = style;

addCommands([
  { g: 'Route', l: 'Send route to boat', ic: 'send', hint: () => S.route && S.route.changes ? `${S.route.changes} changes` : '', run: sendRoute },
  { g: 'Route', l: 'Restore the route last sent', ic: 'undo', when: () => S.route && S.route.changes && S.route.sent, run: () => $('#restoreBtn').click() },
  { g: 'Map', l: 'Add a waypoint on the map', ic: 'pin', run: () => { go('route'); setTimeout(() => setAdding(true), 150); } },
  { g: 'Map', l: 'Fit the route', ic: 'crosshair', run: () => { go('route'); setTimeout(fitView, 150); } },
]);
