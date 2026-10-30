// Route helpers shared by Live's route card and the Route screen.

import { S } from './state.js';
import { post } from './api.js';
import { toast } from './shell.js';
import { esc, km } from './format.js';

export function profileBar(leg) {
  if (!leg) return '';
  return `<span class="prof" aria-hidden="true">${leg.segments.map(([c, f]) => `<i class="${c}" style="flex:${f}"></i>`).join('')}</span>`;
}

export function syncPill(el, r) {
  if (!r.items.length) { el.className = 'pill'; el.textContent = 'No route'; }
  else if (r.changes) { el.className = 'pill warn'; el.innerHTML = `<span class="dot"></span>${r.changes} change${r.changes > 1 ? 's' : ''} not sent`; }
  else if (r.sent) { el.className = 'pill pos'; el.innerHTML = '<span class="dot"></span>Sent'; }
  else { el.className = 'pill'; el.textContent = 'Not sent'; }
}

// The first leg that runs under the boat's draft, with how many metres.
export function underDraft(r) {
  for (let i = 0; i < r.legs.length; i++) {
    const m = r.legs[i].metres.under || 0;
    if (m >= 1) return { i, m };
  }
  return null;
}

export async function routeCmd(action, body = {}) {
  const res = await post('route', { action, ...body });
  if (!res.ok) toast({ tone: 'neg', title: esc(res.message) });
  return res;
}

export async function sendRoute() {
  const r = S.route;
  if (!r || !r.items.length) { toast({ tone: 'info', icon: 'send', title: 'No waypoints to send' }); return; }
  if (!r.changes && r.sent) { toast({ tone: 'info', icon: 'send', title: 'Nothing new to send', desc: 'This route is the one last sent.' }); return; }
  const res = await routeCmd('send');
  if (res.ok) {
    const total = r.legs.reduce((a, l) => a + l.length_m, 0);
    toast({ icon: 'send', title: `Route of ${r.items.length} waypoints logged for the boat`, desc: `${km(total)}. Sending over the XBee link isn’t wired up yet, so the payload is in the log as a TX line.` });
  }
}
