// Boot: draw the icons, set the theme, wire every screen, then open the stream.

import { hydrate } from './icons.js';
import { applyTheme, theme, go, PAGES } from './shell.js';
import './live.js';
import './route.js';
import './logs.js';
import './sessions.js';
import './settings.js';
import './sim.js';
import { startStream } from './api.js';

hydrate();
applyTheme(theme());
const start = (location.hash || '#live').slice(1);
go(PAGES.includes(start) ? start : 'live', false);
startStream();
