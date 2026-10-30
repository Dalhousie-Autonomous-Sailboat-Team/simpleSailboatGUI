// What the page knows about the station, filled from the event stream (api.js),
// and a tiny event bus the screens subscribe to.

export const HISTORY_S = 120;
export const PACKET_WINDOW_S = 60;
const MAX_LINES = 5000;
const MAX_TRACK = 3000;

export const S = {
  ready: false,
  config: null,
  lakes: [],
  conn: null,
  route: null,
  rec: null,
  sessions: [],
  tiles: null,
  tick: null,
  history: [],       // [{t, cb, wa, sa, tra, tb}] for the trend chart
  packetTimes: [],   // arrival times for the link strip
  track: [],         // [[lat, lon, t]] GPS breadcrumb
  lines: [],         // log entries {id, t, type, text}
  clockOffset: 0,    // server time minus this browser's time, in seconds
};

const subs = {};
export function on(event, fn) { (subs[event] ||= []).push(fn); }
export function emit(event, data) {
  (subs[event] || []).forEach(fn => { try { fn(data); } catch (e) { console.error(`${event} handler`, e); } });
}

export const now = () => Date.now() / 1000 + S.clockOffset;

export function addPackets(packets) {
  for (const [t, f] of packets) {
    S.history.push({ t, ...f });
    S.packetTimes.push(t);
  }
  const t = now();
  while (S.history.length && S.history[0].t < t - HISTORY_S) S.history.shift();
  while (S.packetTimes.length && S.packetTimes[0] < t - PACKET_WINDOW_S) S.packetTimes.shift();
}

export function addLines(lines) {
  if (!lines.length) return;
  S.lines.push(...lines);
  if (S.lines.length > MAX_LINES) S.lines.splice(0, S.lines.length - MAX_LINES);
}

export function addTrackPoint(p, t) {
  if (!p) return;
  const last = S.track[S.track.length - 1];
  if (last && last[0] === p[0] && last[1] === p[1]) return;
  S.track.push([p[0], p[1], t]);
  if (S.track.length > MAX_TRACK) S.track.shift();
}

// The waypoint the boat is steering to, its index, and whether waypoints before
// it count as passed (inferred from the order: the boat sails the list in turn).
export function routeTarget() {
  const r = S.route, t = S.tick;
  if (!r || !t || t.target_id == null) return { id: null, index: -1 };
  return { id: t.target_id, index: r.items.findIndex(w => w.id === t.target_id) };
}
