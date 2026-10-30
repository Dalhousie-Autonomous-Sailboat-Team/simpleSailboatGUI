// The link to the local station: one Server-Sent Events stream for state, and
// JSON POSTs for commands. EventSource reconnects on its own if Windbound restarts.

import { S, emit, addPackets, addLines, addTrackPoint } from './state.js';

const SIMPLE = ['conn', 'route', 'rec', 'sessions', 'tiles'];

export function startStream() {
  const es = new EventSource('/api/stream');
  es.addEventListener('hello', e => {
    const d = JSON.parse(e.data);
    S.clockOffset = d.now - Date.now() / 1000;
    S.config = d.config; S.lakes = d.lakes;
    for (const k of SIMPLE) S[k] = d[k];
    S.tick = d.tick;
    S.history = []; S.packetTimes = []; S.lines = []; S.track = d.track || [];
    addPackets(d.history || []);
    S.packetTimes = (d.packet_times || []).slice();
    addLines(d.lines || []);
    S.ready = true;
    emit('online');
    emit('hello', d);
  });
  es.addEventListener('tick', e => {
    const d = JSON.parse(e.data);
    S.tick = d;
    if (d.new_packets && d.new_packets.length) { addPackets(d.new_packets); emit('packets', d.new_packets); }
    addTrackPoint(d.position, d.now);
    if (d.lines && d.lines.length) { addLines(d.lines); emit('lines', d.lines); }
    emit('tick', d);
  });
  for (const k of SIMPLE) {
    es.addEventListener(k, e => { S[k] = JSON.parse(e.data); emit(k, S[k]); });
  }
  es.addEventListener('note', e => emit('note', JSON.parse(e.data)));
  es.addEventListener('log_cleared', () => { S.lines = []; emit('log_cleared'); });
  es.onerror = () => { if (es.readyState !== EventSource.OPEN) emit('offline'); };
  return es;
}

export async function post(area, body = {}) {
  try {
    const r = await fetch(`/api/${area}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    try { return await r.json(); } catch (e) { return { ok: false, message: `Windbound answered ${r.status}` }; }
  } catch (e) {
    return { ok: false, message: 'Windbound isn’t running. Start it again with python windbound_web.py.' };
  }
}

export async function getJSON(url) {
  try { const r = await fetch(url); return await r.json(); } catch (e) { return null; }
}
