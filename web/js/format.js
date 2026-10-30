// Number, angle, time and coordinate formatting shared by every screen.
// Signs use a real minus (U+2212) so columns of figures line up.

export const MINUS = '−';
export const wrap = a => ((a % 360) + 360) % 360;
export const adiff = (a, b) => ((a - b + 540) % 360) - 180;   // a - b in -180..180
export const r0 = v => Math.round(v);
export const isNum = v => typeof v === 'number' && Number.isFinite(v);
export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

export const deg = v => isNum(v) ? `${r0(wrap(v)) % 360}°` : '—';
export const hdg = v => isNum(v) ? `${String(r0(wrap(v)) % 360).padStart(3, '0')}°` : '—';
export const sdeg = v => { if (!isNum(v)) return '—'; const n = r0(v); return n === 0 ? '0°' : (n > 0 ? '+' : MINUS) + Math.abs(n) + '°'; };
export const sint = v => { if (!isNum(v)) return '—'; const n = r0(v); return n === 0 ? '0' : (n > 0 ? '+' : MINUS) + Math.abs(n); };
export const neg = s => String(s).replace(/-/g, MINUS);
export const nf = n => isNum(n) ? n.toLocaleString('en-US') : '—';

export const pad2 = n => String(n).padStart(2, '0');
export const hms = d => `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
export const hmsms = d => `${hms(d)}.${String(d.getMilliseconds()).padStart(3, '0')}`;
export const at = t => new Date(t * 1000);   // server times are seconds since the epoch
export const dur = s => { if (!isNum(s)) return '—'; s = Math.max(0, Math.floor(s)); const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60; return h ? `${h}h ${pad2(m)}m` : `${m}m ${pad2(s % 60)}s`; };
export const mmss = s => { s = Math.max(0, Math.floor(s)); const h = Math.floor(s / 3600); const m = Math.floor(s / 60) % 60; return h ? `${h}:${pad2(m)}:${pad2(s % 60)}` : `${pad2(m)}:${pad2(s % 60)}`; };
export const km = m => !isNum(m) ? '—' : m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${r0(m)} m`;
export const metres = m => isNum(m) ? `${m.toFixed(1)} m` : '—';
export const bytes = b => !isNum(b) ? '—' : b >= 1e9 ? `${(b / 1e9).toFixed(2)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.round(b / 1e3)} kB`;

export const coord = (lat, lon, d = 6) => neg(`${lat.toFixed(d)}, ${lon.toFixed(d)}`);
export const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export function relDay(t) {
  const d = at(t), today = new Date();
  const diff = Math.round((new Date(today.toDateString()) - new Date(d.toDateString())) / 86400000);
  const hm = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  if (diff === 0) return `Today ${hm}`;
  if (diff === 1) return `Yesterday ${hm}`;
  return `${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} ${hm}`;
}

// Great-circle distance in metres, the same formula as route.py.
export function distM(a, b) {
  const R = 6371000, p1 = a[0] * Math.PI / 180, p2 = b[0] * Math.PI / 180;
  const dp = p2 - p1, dl = (b[1] - a[1]) * Math.PI / 180;
  const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

// "WP 3 West cove", or just "WP 3" when the waypoint still has its default name.
export function wpLabel(n, name) {
  const base = `WP ${n}`;
  return !name || name.trim().toUpperCase() === base ? base : `${base} ${name}`;
}

// Where the apparent wind comes from, in words.
export function windSide(wa) {
  if (!isNum(wa)) return '';
  const r = adiff(wa, 0), s = Math.abs(r), side = r < 0 ? 'port' : 'starboard';
  if (s < 10) return 'dead ahead';
  if (s > 170) return 'from astern';
  return `${r0(s)}° off ${side} ${s < 90 ? 'bow' : 'quarter'}`;
}
