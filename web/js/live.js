// Live: the boat drawn from your sticks and the last packet, the link's health,
// the trend of the last two minutes, and the route at a glance.

import { $, $$, ic } from './icons.js';
import { S, on, now, routeTarget } from './state.js';
import { post } from './api.js';
import { go, onPage, currentPage, connectTo, disconnect, answer } from './shell.js';
import {
  wrap, adiff, r0, isNum, clamp, deg, hdg, sdeg, sint, nf, hms, at, mmss, km, coord, esc, windSide, wpLabel,
} from './format.js';
import { sendRoute, syncPill, underDraft, profileBar } from './routeops.js';

const GAP_WARN = 4;        // degrees between command and report before it's a gap
const GAP_FINDING = 10;    // degrees before the title line calls it out

/* ---------------- the boat drawing ---------------- */
(() => {   // compass ticks every 5°, majors every 30°
  let h = '';
  for (let a = 0; a < 360; a += 5) {
    const major = a % 30 === 0, r1 = 150, r2 = major ? 141 : 145, rad = a * Math.PI / 180;
    h += `<line class="tick${major ? ' major' : ''}" x1="${180 + r1 * Math.sin(rad)}" y1="${180 - r1 * Math.cos(rad)}" x2="${180 + r2 * Math.sin(rad)}" y2="${180 - r2 * Math.cos(rad)}"/>`;
  }
  [[0, '0°'], [90, '90°'], [270, '270°']].forEach(([a, t]) => {
    const rad = a * Math.PI / 180, r = 128;
    h += `<text x="${180 + r * Math.sin(rad)}" y="${180 - r * Math.cos(rad) + 4}" text-anchor="middle">${t}</text>`;
  });
  $('#ticks').innerHTML = h;
})();

const MAST = { x: 180, y: 160 }, BOOM = 112;
const RUDDER = { x: 180, y: 160 + (287 - 160) * 1.12 };
const disp = { cmd: null, act: null, rud: 0, tra: 0, wa: 0, rel: 0 };
const polar = (c, ang, r) => { const a = ang * Math.PI / 180; return { x: c.x + r * Math.sin(a), y: c.y - r * Math.cos(a) }; };
function arcPath(c, r, a0, a1) {
  const p0 = polar(c, a0, r), p1 = polar(c, a1, r), d = adiff(a1, a0);
  return `M${p0.x.toFixed(1)} ${p0.y.toFixed(1)} A${r} ${r} 0 0 ${d > 0 ? 1 : 0} ${p1.x.toFixed(1)} ${p1.y.toFixed(1)}`;
}
function ease(cur, target, dt, tau, angle) {
  if (cur === null || !isNum(cur)) return target;
  const k = 1 - Math.exp(-dt / tau);
  return angle ? wrap(cur + adiff(target, cur) * k) : cur + (target - cur) * k;
}
function setLine(sel, p, q) {
  const l = $(sel);
  l.setAttribute('x1', p.x.toFixed(1)); l.setAttribute('y1', p.y.toFixed(1));
  l.setAttribute('x2', q.x.toFixed(1)); l.setAttribute('y2', q.y.toFixed(1));
}
function place(sel, p) { const el = $(sel); el.setAttribute('x', p.x.toFixed(1)); el.setAttribute('y', (p.y + 4).toFixed(1)); }

function drawBoat(dt) {
  const t = S.tick; if (!t) return;
  const tel = t.telemetry || {};
  const hasSa = isNum(tel.sa);
  disp.cmd = ease(disp.cmd, t.cmd_sail, dt, 40, true);
  disp.act = hasSa ? ease(disp.act, wrap(tel.sa), dt, 140, true) : null;
  disp.rud = ease(disp.rud, t.cmd_rudder || 0, dt, 60, false);
  disp.tra = ease(disp.tra, isNum(tel.tra) ? tel.tra : 0, dt, 140, false);
  disp.wa = ease(disp.wa, isNum(tel.wa) ? wrap(tel.wa) : 0, dt, 260, true);
  disp.rel = ease(disp.rel, isNum(tel.tb) && isNum(tel.cb) ? adiff(tel.tb, tel.cb) : 0, dt, 260, false);

  setLine('#boomC', MAST, polar(MAST, disp.cmd, BOOM));
  const showAct = disp.act !== null;
  $('#boomA').style.display = showAct ? '' : 'none';
  $('#sailA').style.display = showAct ? '' : 'none';
  if (showAct) {
    const a = polar(MAST, disp.act, BOOM);
    setLine('#boomA', MAST, a);
    // the sail's belly bulges to leeward of the reported boom
    const side = adiff(disp.act, 180) > 0 ? -1 : 1;
    const bul = polar(polar(MAST, disp.act, BOOM * 0.55), disp.act + 90 * side, 18);
    $('#sailA').setAttribute('d', `M${MAST.x} ${MAST.y} Q${bul.x.toFixed(1)} ${bul.y.toFixed(1)} ${a.x.toFixed(1)} ${a.y.toFixed(1)} Z`);
  }
  const g = showAct ? adiff(disp.cmd, disp.act) : 0;
  if (Math.abs(g) > 2) {
    $('#gapArc').setAttribute('d', arcPath(MAST, 72, disp.act, disp.cmd));
    place('#gapT', polar(MAST, disp.act + g / 2, 54)); $('#gapT').textContent = sdeg(g);
  } else { $('#gapArc').setAttribute('d', ''); $('#gapT').textContent = ''; }

  setLine('#rudC', RUDDER, polar(RUDDER, 180 - disp.rud, 30));
  setLine('#rudA', RUDDER, polar(RUDDER, 180 - disp.tra, 30));
  $('#rudA').style.display = isNum(tel.tra) ? '' : 'none';

  const hasWind = isNum(tel.wa);
  $('#windG').style.display = hasWind ? '' : 'none'; $('#windT').style.display = hasWind ? '' : 'none';
  $('#windG').setAttribute('transform', `rotate(${disp.wa.toFixed(1)} 180 180)`);
  // the label sits beside the arrow's tail, outside the ring, clear of the booms
  place('#windT', polar({ x: 180, y: 180 }, disp.wa + 11, 162));

  const hasTgt = isNum(tel.tb) && isNum(tel.cb);
  $('#tgtG').style.display = hasTgt ? '' : 'none'; $('#tgtT').style.display = hasTgt ? '' : 'none';
  $('#tgtG').setAttribute('transform', `rotate(${disp.rel.toFixed(1)} 180 180)`);
  place('#tgtT', polar({ x: 180, y: 180 }, disp.rel, 166));
}

/* ---------------- figures ---------------- */
function barFill(sel, v) {
  const el = $(sel);
  if (!isNum(v)) { el.style.width = '0'; return; }
  const p = clamp(v / 45, -1, 1) * 50;
  el.style.left = (p >= 0 ? 50 : 50 + p) + '%'; el.style.width = Math.abs(p) + '%';
}
function targetLabel() {
  const { id, index } = routeTarget();
  if (id == null) return null;
  const w = S.route.items[index];
  return { n: index + 1, name: w.name };
}

function updateFigures() {
  const t = S.tick; if (!t) return;
  const tel = t.telemetry || {}, st = t.status;
  const hasSa = isNum(tel.sa);
  const gap = hasSa ? adiff(t.cmd_sail, tel.sa) : null;
  $('#figCmd').textContent = deg(t.cmd_sail);
  $('#figAct').textContent = hasSa ? deg(tel.sa) : '—';
  const gp = $('#gapPill');
  if (!hasSa) { gp.className = 'pill'; gp.textContent = 'No report yet'; }
  else if (Math.abs(gap) > GAP_WARN) { gp.className = 'pill warn'; gp.textContent = `Gap ${sdeg(gap)}`; }
  else { gp.className = 'pill pos'; gp.innerHTML = '<span class="dot"></span>In step'; }

  const dead = S.config ? S.config.sail_deadband : 2, rate = S.config ? S.config.sail_rate_dps : 72;
  const input = st ? st.sail : null;
  let motor;
  if (!st) motor = 'No controller status yet';
  else if (Math.abs(input) >= dead) motor = `Stick at ${sint(input)}: command turning ${input > 0 ? 'clockwise' : 'anticlockwise'} at ${rate}°/s`;
  else if (hasSa && Math.abs(gap) > GAP_WARN) motor = `Stick centred: the boat reports ${Math.abs(r0(gap))}° off your command`;
  else motor = 'Stick centred: command holding';
  $('#motorTxt').textContent = motor;
  $('#alignBtn').hidden = !(hasSa && Math.abs(gap) > GAP_WARN && st && Math.abs(input) < dead);

  $('#kvRud').textContent = sdeg(t.cmd_rudder);
  $('#kvTra').textContent = sdeg(tel.tra);
  $('#kvCb').textContent = hdg(tel.cb);
  $('#kvTb').textContent = hdg(tel.tb);
  const tl = targetLabel();
  $('#kvTbW').textContent = tl ? `to WP ${tl.n}` : 'target';
  $('#tgtT').textContent = tl ? `WP ${tl.n}` : 'Target';
  $('#kvWa').textContent = deg(tel.wa);
  $('#kvWaS').textContent = windSide(tel.wa);
  const pos = $('#kvPos');
  if (t.position) pos.textContent = coord(t.position[0], t.position[1]);
  else if (t.position_truncated) pos.innerHTML = `<span class="warnv">${esc(coord(tel.clat, tel.clon, 0))} · truncated by the firmware</span>`;
  else pos.textContent = isNum(tel.clat) ? 'No GPS fix' : '—';
  $('#stS').textContent = st ? sint(st.sail) : '—';
  $('#stR').textContent = st ? sint(st.rudder) : '—';
  barFill('#stSf', st ? st.sail : null); barFill('#stRf', st ? st.rudder : null);
  $('#kt-cb').textContent = hdg(tel.cb); $('#kt-wa').textContent = deg(tel.wa);
  $('#kt-sa').textContent = deg(tel.sa); $('#kt-tra').textContent = sdeg(tel.tra);
  $('#finding').innerHTML = finding(t, gap, tl);
}

function finding(t, gap, tl) {
  const c = S.conn;
  if (!c || c.state !== 'connected') return 'No controller connected, so nothing below is live.';
  const age = t.telemetry_age_s, stale = S.config ? S.config.stale_s : 2;
  if (age == null) return `Connected to ${esc(c.label || c.device)}. Waiting for the first XBee packet.`;
  if (age > stale) return `No telemetry for <b>${age < 60 ? age.toFixed(0) + ' s' : mmss(age)}</b>. Check the XBee link.`;
  if (gap !== null && Math.abs(gap) > GAP_FINDING) return `The sail is <b>${Math.abs(r0(gap))}° off</b> your command.`;
  if (isNum(t.target_m)) {
    const where = tl ? esc(wpLabel(tl.n, tl.name)) : 'the boat’s target, which isn’t in this route';
    const eta = isNum(t.speed_mps) && t.speed_mps > 0.2 ? `, about ${mmss(t.target_m / t.speed_mps)} at ${t.speed_mps.toFixed(1)} m/s` : '';
    return `Sail and rudder match your commands. <b>${km(t.target_m)}</b> to ${where}${eta}.`;
  }
  return 'Sail and rudder match your commands.';
}

/* ---------------- link card ---------------- */
function updateLink() {
  const t = S.tick, c = S.conn; if (!t || !c) return;
  const st = t.status;
  $('#ctrlDev').textContent = c.device === S.config?.simulator_url ? `Simulator · ${c.device}` : `${c.device} · ${c.baud} baud`;
  $('#lineRate').textContent = t.line_rate.toFixed(1);
  $('#dropTxt').textContent = st ? `${nf(st.dropped)} dropped · ${nf(st.overruns)} overruns` : 'No status lines yet';
  $('#dropTxt').classList.toggle('warnv', !!(st && (st.dropped || st.overruns)));
  $('#pktRate').textContent = t.packet_rate.toFixed(1);
  $('#pktCount').textContent = `#${nf(t.packets)}`;
  const age = t.telemetry_age_s, stale = age != null && age > (S.config ? S.config.stale_s : 2);
  $('#ageTxt').textContent = age == null ? 'No packets' : age < 60 ? `${age.toFixed(1)} s` : mmss(age);
  $('#agePill').className = 'pill ' + (age == null ? '' : stale ? 'warn' : 'pos');
  const fresh = $('#fresh');
  fresh.hidden = age == null;
  if (age != null) {
    $('#freshText').textContent = stale ? `Telemetry stale · ${age < 60 ? age.toFixed(1) + ' s' : mmss(age)} since the last packet` : `Telemetry ${age.toFixed(1)} s ago`;
    fresh.classList.toggle('stale', stale);
    $('#freshDot').className = 'dot ' + (stale ? 'warn' : 'live');
    $('#freshPkt').textContent = `packet ${nf(t.packets)}`;
  }
  $('#autoConn').checked = c.autoconnect;
}

function drawStrip() {
  const svg = $('#strip'); const W = svg.clientWidth || 300, H = 22;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const t1 = now(), t0 = t1 - 60;
  const times = S.packetTimes;
  let h = '', gaps = [], prev = null;
  times.forEach(t => { if (prev !== null && t - prev > 1.5) gaps.push([prev, t]); prev = t; });
  if (prev !== null && t1 - prev > 1.5) gaps.push([prev, t1]);
  gaps.forEach(([a, b]) => { const x0 = Math.max(0, (a - t0) / 60 * W), x1 = (b - t0) / 60 * W; h += `<rect class="gp" x="${x0.toFixed(1)}" y="0" width="${Math.max(1, x1 - x0).toFixed(1)}" height="${H}" rx="2"/>`; });
  times.forEach(t => { const x = ((t - t0) / 60 * W).toFixed(1); h += `<line class="pk${t > t1 - 2.6 ? ' new' : ''}" x1="${x}" y1="4" x2="${x}" y2="${H - 4}"/>`; });
  svg.innerHTML = h;
  const longest = gaps.reduce((m, [a, b]) => Math.max(m, b - a), 0);
  $('#gapTxt').textContent = !times.length ? '' : gaps.length ? `${gaps.length} gap${gaps.length > 1 ? 's' : ''} · longest ${longest.toFixed(1)} s` : 'No gaps';
}

/* ---------------- trend chart ---------------- */
let series = 'cb', hoverX = null;
$$('.ktab').forEach(b => b.addEventListener('click', () => {
  series = b.dataset.series;
  $$('.ktab').forEach(x => x.setAttribute('aria-selected', String(x === b)));
  drawChart();
}));
function niceStep(span) {
  const raw = span / 4, p = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / p;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p;
}
function drawChart() {
  const el = $('#chart'); if (currentPage() !== 'live' || $('#liveGrid').hidden) return;
  const W = el.clientWidth, H = el.clientHeight; if (!W) return;
  const key = series, isAng = key !== 'tra';
  const pts = S.history.filter(p => isNum(p[key]));
  if (pts.length < 2) {
    el.innerHTML = `<div class="empty-row">${S.history.length ? 'This value isn’t in the telemetry.' : 'The last two minutes of telemetry will draw here.'}</div>`;
    return;
  }
  const t1 = now(), t0 = t1 - 120;
  // unwrap angles so a series crossing 0/360 stays continuous
  const vals = [];
  pts.forEach((p, i) => vals.push(i && isAng ? vals[i - 1] + adiff(p[key], pts[i - 1][key]) : p[key]));
  const refs = key === 'cb' ? pts.map((p, i) => isNum(p.tb) ? vals[i] + adiff(p.tb, p.cb) : null) : null;
  const all = vals.concat((refs || []).filter(isNum));
  let lo = Math.min(...all), hi = Math.max(...all);
  if (hi - lo < 20) { const m = (hi + lo) / 2; lo = m - 10; hi = m + 10; }
  const stp = niceStep(hi - lo);
  lo = Math.floor(lo / stp) * stp; hi = Math.ceil(hi / stp) * stp;
  if (hi === Math.max(...vals)) hi += stp;
  const P = { l: 44, r: 12, t: 14, b: 26 }, iw = W - P.l - P.r, ih = H - P.t - P.b;
  const X = t => P.l + (t - t0) / 120 * iw, Y = v => P.t + (1 - (v - lo) / (hi - lo)) * ih;
  const fmt = v => isAng ? (key === 'cb' ? hdg(v) : deg(v)) : sdeg(v);
  let h = '';
  for (let i = 1; i < pts.length; i++) if (pts[i].t - pts[i - 1].t > 1.5) {
    const x0 = Math.max(P.l, X(pts[i - 1].t)), x1 = X(pts[i].t);
    h += `<rect class="gapband" x="${x0.toFixed(1)}" y="${P.t}" width="${Math.max(0, x1 - x0).toFixed(1)}" height="${ih}"/>`;
  }
  for (let v = lo; v <= hi + 1e-6; v += stp) {
    const y = Y(v).toFixed(1);
    h += `<line class="${v === lo ? 'base' : 'grid'}" x1="${P.l}" x2="${W - P.r}" y1="${y}" y2="${y}"/><text class="yl" x="${P.l - 8}" y="${(+y + 4).toFixed(1)}" text-anchor="end">${fmt(v)}</text>`;
  }
  [[0, '2 min ago', 'start'], [.25, '90 s', 'middle'], [.5, '60 s', 'middle'], [.75, '30 s', 'middle'], [1, 'now', 'end']].forEach(([f, t, an]) => {
    h += `<text class="xl" x="${(P.l + f * iw).toFixed(1)}" y="${H - 6}" text-anchor="${an}">${t}</text>`;
  });
  let d = '', breaks = false;
  pts.forEach((p, i) => {
    const brk = i && p.t - pts[i - 1].t > 1.5; breaks ||= brk;
    d += `${i === 0 || brk ? 'M' : 'L'}${Math.max(P.l, X(p.t)).toFixed(1)} ${Y(vals[i]).toFixed(1)}`;
  });
  if (refs && refs.some(isNum)) {
    let rd = '', pen = false;
    pts.forEach((p, i) => { if (!isNum(refs[i])) { pen = false; return; } rd += `${pen ? 'L' : 'M'}${Math.max(P.l, X(p.t)).toFixed(1)} ${Y(refs[i]).toFixed(1)}`; pen = true; });
    h += `<path class="ref" d="${rd}"/>`;
    const lastRef = [...refs].reverse().find(isNum), lastTb = [...pts].reverse().find(p => isNum(p.tb)).tb;
    h += `<text class="ref-t" x="${W - P.r}" y="${(Y(lastRef) - 7).toFixed(1)}" text-anchor="end">Target ${hdg(lastTb)}</text>`;
  }
  const lx = X(pts[pts.length - 1].t), ly = Y(vals[vals.length - 1]);
  if (!breaks) h += `<path class="area" d="${d}L${lx.toFixed(1)} ${(P.t + ih).toFixed(1)}L${Math.max(P.l, X(pts[0].t)).toFixed(1)} ${(P.t + ih).toFixed(1)}Z"/>`;
  h += `<path class="line" d="${d}"/>`;
  const fresh = S.tick && S.tick.telemetry_age_s != null && S.tick.telemetry_age_s < 2;
  if (fresh) h += `<circle class="endp" cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="4"/>`;
  h += `<circle class="end" cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="3.5"/>`;
  let tip = '';
  if (hoverX !== null && hoverX >= P.l && hoverX <= W - P.r) {
    const tt = t0 + (hoverX - P.l) / iw * 120;
    let bi = 0, bd = Infinity;
    pts.forEach((p, i) => { const dd = Math.abs(p.t - tt); if (dd < bd) { bd = dd; bi = i; } });
    const hx = X(pts[bi].t);
    h += `<line class="hov" x1="${hx.toFixed(1)}" x2="${hx.toFixed(1)}" y1="${P.t}" y2="${P.t + ih}"/><circle class="end" cx="${hx.toFixed(1)}" cy="${Y(vals[bi]).toFixed(1)}" r="3.5"/>`;
    tip = `<div class="chart-tip" style="left:${clamp(hx, 70, W - 70)}px">${hms(at(pts[bi].t))} · ${fmt(pts[bi][key])}</div>`;
  }
  const name = $('.ktab[aria-selected=true] .kl').textContent;
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${name} over the last two minutes">${h}</svg>${tip}`;
}
$('#chart').addEventListener('pointermove', e => { hoverX = e.clientX - e.currentTarget.getBoundingClientRect().left; drawChart(); });
$('#chart').addEventListener('pointerleave', () => { hoverX = null; drawChart(); });

/* ---------------- route card ---------------- */
function renderRouteCard() {
  const r = S.route; if (!r) return;
  const total = r.legs.reduce((a, l) => a + l.length_m, 0);
  $('#rmSub').textContent = r.items.length ? `${r.items.length} waypoint${r.items.length === 1 ? '' : 's'} · ${km(total)}` : 'No waypoints yet';
  syncPill($('#rmPill'), r);
  const tc = $('#tabRouteCount'); tc.textContent = r.changes; tc.hidden = !r.changes;
  const u = underDraft(r);
  $('#rmWarn').hidden = !u;
  if (u) $('#rmWarnT').textContent = `Leg ${u.i + 1} → ${u.i + 2} runs ${r0(u.m)} m under the ${S.config.boat_draft_m.toFixed(1)} m draft.`;
  const { index } = routeTarget();
  const list = $('#rmList');
  list.hidden = !r.items.length;
  $('#rmEmpty').hidden = !!r.items.length;
  if (r.items.length) {
    list.innerHTML = r.items.map((w, i) => {
      const cls = index < 0 ? '' : i < index ? 'reached' : i === index ? 'active' : '';
      const meta = i === index && isNum(S.tick?.target_m) ? `${km(S.tick.target_m)} to go`
        : cls === 'reached' ? 'Passed' : i ? km(r.legs[i - 1].length_m) : 'Start';
      return `<li><button class="rm-row ${cls}" data-wp="${w.id}"><span class="wpn ${cls}">${i + 1}</span><span class="nmw"><span class="nm">${esc(w.name)}</span>${i ? profileBar(r.legs[i - 1]) : ''}</span><span class="meta${cls === 'reached' ? ' passed' : ''}">${meta}</span></button></li>`;
    }).join('');
    $$('.rm-row', list).forEach(b => b.addEventListener('click', () => { go('route'); }));
  }
  $('#rmKey').hidden = r.legs.length === 0 || !r.lake;
  $('#rmSend').disabled = !r.items.length || !r.changes && r.sent > 0;
}
$('#rmSend').addEventListener('click', sendRoute);

/* ---------------- connected or not ---------------- */
function renderConn() {
  const c = S.conn; if (!c) return;
  const live = c.state === 'connected';
  $('#liveGrid').hidden = !live;
  $('#offline').hidden = live;
  if (!live) {
    $('#fresh').hidden = true;
    $('#finding').textContent = 'No controller connected, so nothing below is live.';
    $('#offH').textContent = c.error ? 'Couldn’t open the port' : 'Waiting for the controller';
    $('#offP').textContent = c.error ? c.error
      : c.autoconnect ? 'Plug the ST-Link into this laptop. With auto-connect on, Windbound opens its COM port as soon as it appears.'
        : 'Choose the controller’s port below. Auto-connect is off.';
    $('#scanning').hidden = !c.autoconnect;
    const box = $('#ports');
    box.innerHTML = c.ports.map(p => `<div class="p">${ic(p.sim ? 'cube' : 'plug')}<div class="grow"><div>${esc(p.sim ? 'Simulator' : p.device)}</div><div class="s">${esc(p.sim ? `${p.device} · start windbound-firmware/sim first` : p.description)}${p.stlink ? ' · ST-Link' : ''}</div></div><button class="btn sm${p.stlink ? ' primary' : ''} ica" data-port="${esc(p.device)}">Connect</button></div>`).join('');
    $$('[data-port]', box).forEach(b => b.addEventListener('click', async () => {
      b.classList.add('busy'); b.innerHTML = '<span class="spinner"></span>Connecting';
      await connectTo(b.dataset.port, c.baud);
      b.classList.remove('busy'); b.textContent = 'Connect';
    }));
  } else {
    requestAnimationFrame(() => { drawChart(); drawStrip(); });
  }
}
$('#urlForm').addEventListener('submit', e => {
  e.preventDefault();
  const v = $('#urlIn').value.trim(); if (v) connectTo(v, S.conn ? S.conn.baud : 115200);
});
$('#discBtn').addEventListener('click', disconnect);
$('#autoConn').addEventListener('change', async e => {
  const res = await post('connection', { action: 'autoconnect', on: e.target.checked });
  answer(res, res.message, e.target.checked ? 'An ST-Link that is plugged in is opened at once.' : 'Choose the port yourself from the connection menu.', 'refresh');
});
$('#alignBtn').addEventListener('click', async () => { answer(await post('sail', { action: 'align' })); });

/* ---------------- wiring ---------------- */
let lastFrame = performance.now(), lastStrip = 0;
function frame(nowMs) {
  const dt = Math.min(nowMs - lastFrame, 250); lastFrame = nowMs;
  if (currentPage() === 'live' && S.conn && S.conn.state === 'connected') {
    drawBoat(dt);
    if (nowMs - lastStrip > 500) { lastStrip = nowMs; drawStrip(); }
  }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

on('hello', () => { renderConn(); renderRouteCard(); updateFigures(); updateLink(); drawBoat(1000); drawChart(); drawStrip(); });
on('conn', () => { renderConn(); updateLink(); });
on('route', renderRouteCard);
on('tick', () => { if (currentPage() === 'live') { updateFigures(); updateLink(); } });
on('packets', () => {
  const r = $('#radioIc'); if (r) { r.classList.remove('ping'); void r.getBoundingClientRect(); r.classList.add('ping'); }
  if (currentPage() === 'live') { drawChart(); renderRouteCard(); }
});
onPage('live', () => { updateFigures(); updateLink(); renderRouteCard(); drawChart(); drawStrip(); });
addEventListener('resize', () => { drawChart(); drawStrip(); });
