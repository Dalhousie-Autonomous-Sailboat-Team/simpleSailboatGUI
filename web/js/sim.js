// The boat simulator's own screens: Mission control and Firmware checks from
// windbound-firmware/sim, shown in a frame while a simulator is running (the
// station finds it, see simulator.py). The simulator keeps owning them, next
// to its API. They talk by message, only with the simulator's own origin:
// this page sends the theme and the screen to show, and hears back the screen
// the simulator moved to, its failing checks and ⌘K.

import { $, $$, ic } from './icons.js';
import { S, on } from './state.js';
import { onPage, currentPage, go, theme, openCmdk, connectTo } from './shell.js';

const SIM_PAGES = ['mission', 'checks'];
const TITLES = { mission: 'Mission control', checks: 'Firmware checks' };
const frame = $('#simFrame');
let shown = null;                       // the simulator page in the frame

const sim = () => (S.conn && S.conn.sim) || { state: 'off' };
const onSimPage = () => SIM_PAGES.includes(currentPage());
const tell = (type, data = {}) => {
  if (shown && frame.contentWindow) frame.contentWindow.postMessage({ type, ...data }, new URL(shown).origin);
};

function render() {
  const s = sim();
  const appearing = s.state !== 'off' && $('.tab-sim').hidden;
  $$('.tab-sim').forEach(t => { t.hidden = s.state === 'off'; });
  if (appearing && onSimPage()) $(`.tab[data-page="${currentPage()}"]`).scrollIntoView({ block: 'nearest', inline: 'nearest' });
  const page = s.state === 'ready' ? s.page : null;
  if (page && page !== shown) {
    shown = page;
    const at = onSimPage() ? currentPage() : 'mission';
    frame.src = `${page}?embed&parent=${encodeURIComponent(location.origin)}&theme=${theme()}#${at}`;
  } else if (!page && shown) {
    shown = null;
    frame.removeAttribute('src');
    $('#tabChecksCount').hidden = true;
  }
  frame.hidden = !page;
  $('#simWait').hidden = Boolean(page);
  if (page) return;
  // Not running yet: say why, and what to do.
  const connected = S.conn && S.conn.state === 'connected' && S.conn.device === S.config?.simulator_url;
  const [h, p] = s.state === 'starting'
    ? ['Starting the simulator', 'The first start sets up its Python environment and builds the firmware, which takes a minute or two. Its screens appear here when it’s ready.']
    : s.state === 'failed'
      ? ['The simulator didn’t start', `${s.message} Run <code>python3 sim/start.py</code> in windbound-firmware to see the whole story.`]
      : connected
        ? ['Looking for the simulator’s screens', 'Connected to the simulator’s link; its page should answer in a moment.']
        : ['The simulator isn’t running', 'Start the ground station with <code>python windbound_web.py --simulator</code> to run it too, or run <code>python3 sim/start.py</code> in windbound-firmware and connect to Simulator.'];
  $('#simWaitH').textContent = h;
  $('#simWaitP').innerHTML = p;
  $('#simIc').outerHTML = ic(s.state === 'failed' ? 'alert' : s.state === 'starting' ? 'refresh' : 'cube', s.state === 'starting' ? 'spinning' : '', 'simIc');
  $('#simConnect').hidden = s.state === 'starting' || connected;
  $('#simH').textContent = TITLES[onSimPage() ? currentPage() : 'mission'];
}

SIM_PAGES.forEach(name => onPage(name, () => {
  render();
  if (!shown) return;
  tell('windbound:section', { name });
  frame.focus();                         // its keys (Space, F, 1-3) work at once
}));
on('hello', render);
on('conn', render);
on('theme', () => tell('windbound:theme', { theme: theme() }));
$('#simConnect').addEventListener('click', () => connectTo(S.config.simulator_url, S.conn.baud));

window.addEventListener('message', e => {
  if (!shown || e.source !== frame.contentWindow || e.origin !== new URL(shown).origin) return;
  const m = e.data || {};
  if (m.type === 'windbound:section' && SIM_PAGES.includes(m.name)) {
    if (onSimPage() && currentPage() !== m.name) go(m.name);   // a link inside it moved screens
  } else if (m.type === 'windbound:checks') {
    const count = $('#tabChecksCount');
    count.textContent = m.failing ? `${m.failing} failing` : '';
    count.hidden = !m.failing;
  } else if (m.type === 'windbound:cmdk') {
    openCmdk();
  }
});
