// Sessions: the telemetry recordings in logs/, with a live row while recording.

import { $, $$, ic, hydrate, play } from './icons.js';
import { S, on, now } from './state.js';
import { post } from './api.js';
import { onPage, currentPage, setRecording, toast, copyText, addCommands } from './shell.js';
import { nf, dur, km, relDay, esc, distM } from './format.js';

// A session's GPS track drawn into a 64 x 44 thumbnail, north up.
function thumb(points, live) {
  if (!points || points.length < 2) return '<svg class="thumb" viewBox="0 0 64 44" aria-hidden="true"></svg>';
  const lat0 = points[0][0], k = Math.cos(lat0 * Math.PI / 180);
  const xs = points.map(p => p[1] * k), ys = points.map(p => -p[0]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const s = Math.min(52 / Math.max(x1 - x0, 1e-9), 32 / Math.max(y1 - y0, 1e-9));
  const ox = (64 - (x1 - x0) * s) / 2, oy = (44 - (y1 - y0) * s) / 2;
  const P = points.map((p, i) => [ox + (xs[i] - x0) * s, oy + (ys[i] - y0) * s]);
  const d = 'M' + P.map(p => `${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' L');
  const end = P[P.length - 1];
  return `<svg class="thumb" viewBox="0 0 64 44" aria-hidden="true"><path d="${d}"/>${live ? `<circle cx="${end[0].toFixed(1)}" cy="${end[1].toFixed(1)}" r="2.5" fill="var(--accent)"/>` : ''}</svg>`;
}

// The current recording's track: the GPS breadcrumb since it started.
function liveTrack() {
  const since = S.rec && S.rec.started ? S.rec.started : 0;
  return S.track.filter(p => p[2] >= since);
}
function liveLength() {
  const t = liveTrack(); let m = 0;
  for (let i = 1; i < t.length; i++) m += distM(t[i - 1], t[i]);
  return m;
}
const liveRows = () => S.tick && S.tick.rec_rows != null ? S.tick.rec_rows : (S.rec ? S.rec.rows : 0);

function render() {
  const list = S.sessions || [];
  const rec = S.rec;
  $('#sessRecT').textContent = rec && rec.active ? 'Stop recording' : 'Start recording';
  $('#sessSub').innerHTML = `Telemetry recordings saved to <code>${esc(S.config ? S.config.log_dir : 'logs')}/</code> as CSV, one row per packet.`;
  if (!list.length) {
    $('#slist').innerHTML = '<li class="empty-row">No recordings yet. Press Record in the top bar, or R, to start one.</li>';
    $('#sTotal').textContent = 'No sessions';
    return;
  }
  $('#slist').innerHTML = list.map(s => {
    if (s.live) {
      const secs = s.started ? now() - s.started : null;
      return `<li class="srow on">${thumb(liveTrack(), true)}<div class="sname"><div class="f"><span>${esc(s.file)}</span><span class="pill neg"><span class="dot"></span>Recording</span></div><div class="m">${esc(S.route && S.route.lake || '')}${S.route && S.route.lake ? ' · ' : ''}one row per packet</div></div><span class="r num c-dur" id="sLiveDur">${dur(secs)}</span><span class="r num c-rows" id="sLiveRows">${nf(liveRows())}</span><span class="r num c-track" id="sLiveTrack">—</span><span class="r c-start g">${s.started ? relDay(s.started) : ''}</span><span class="sacts"><button class="btn sm ica" disabled><i data-ic="download"></i><span class="bl">GPX</span></button><button class="btn sm ghost icon ica" aria-label="Copy the file's path" data-copy="${esc(s.path)}">${ic('copy')}</button></span></li>`;
    }
    const started = s.first ? Date.parse(s.first) / 1000 : null;
    const meta = s.error ? `Couldn’t read: ${esc(s.error)}` : [s.lake, s.fixes ? `${nf(s.fixes)} GPS fixes` : 'no GPS fixes'].filter(Boolean).join(' · ');
    return `<li class="srow">${thumb(s.thumb, false)}<div class="sname"><div class="f"><span>${esc(s.file)}</span></div><div class="m">${meta}</div></div><span class="r num c-dur">${dur(s.duration_s)}</span><span class="r num c-rows">${nf(s.rows)}</span><span class="r num c-track">${s.fixes ? km(s.track_m) : '—'}</span><span class="r c-start g">${started ? relDay(started) : '—'}</span><span class="sacts"><button class="btn sm ica" data-gpx="${esc(s.file)}"${s.fixes ? '' : ' disabled title="No GPS positions in this recording"'}><i data-ic="download"></i><span class="bl">GPX</span></button><button class="btn sm ghost icon ica" aria-label="Copy the file's path" data-copy="${esc(s.path)}">${ic('copy')}</button></span></li>`;
  }).join('');
  hydrate($('#slist'));
  const rows = list.reduce((a, s) => a + (s.live ? liveRows() : (s.rows || 0)), 0);
  $('#sTotal').textContent = `${list.length} session${list.length === 1 ? '' : 's'} · ${nf(rows)} rows`;
  $$('[data-gpx]').forEach(b => b.addEventListener('click', async () => {
    play(b);
    const res = await post('sessions', { action: 'gpx', file: b.dataset.gpx });
    if (res.ok) toast({ icon: 'download', title: 'Exported GPX', desc: esc(res.message) });
    else toast({ tone: 'warn', title: 'Couldn’t export GPX', desc: esc(res.message) });
  }));
  $$('[data-copy]').forEach(b => b.addEventListener('click', () => copyText(b.dataset.copy, b)));
  tickLive();
}
function tickLive() {
  const rec = S.rec, d = $('#sLiveDur');
  if (!rec || !rec.active || !d) return;
  d.textContent = dur(now() - rec.started);
  $('#sLiveRows').textContent = nf(liveRows());
  $('#sLiveTrack').textContent = liveTrack().length > 1 ? km(liveLength()) : '—';
  const list = S.sessions || [];
  const rows = list.reduce((a, s) => a + (s.live ? liveRows() : (s.rows || 0)), 0);
  $('#sTotal').textContent = `${list.length} session${list.length === 1 ? '' : 's'} · ${nf(rows)} rows`;
}

$('#sessRec').addEventListener('click', () => setRecording(!(S.rec && S.rec.active)));
on('sessions', () => { if (currentPage() === 'sessions') render(); });
on('rec', () => { if (currentPage() === 'sessions') render(); });
on('hello', () => { if (currentPage() === 'sessions') render(); });
let lastTick = 0;
on('tick', () => { const t = performance.now(); if (currentPage() === 'sessions' && t - lastTick > 900) { lastTick = t; tickLive(); } });
onPage('sessions', render);

addCommands([
  { g: 'Boat', l: 'Export GPX of the last session', ic: 'download', when: () => (S.sessions || []).some(s => !s.live && s.fixes), run: async () => {
    const s = S.sessions.find(x => !x.live && x.fixes);
    const res = await post('sessions', { action: 'gpx', file: s.file });
    toast(res.ok ? { icon: 'download', title: 'Exported GPX', desc: esc(res.message) } : { tone: 'warn', title: 'Couldn’t export GPX', desc: esc(res.message) });
  } },
]);
