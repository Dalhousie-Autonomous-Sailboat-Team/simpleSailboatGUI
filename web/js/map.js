// A small slippy map: Web Mercator XYZ tiles served by the station (through the
// same SQLite cache as the desktop map), with fractional zoom, drag to pan and
// wheel zoom about the pointer. Layers are tile sets; everything else (route,
// markers) is drawn by the caller in screen space with project().

const TILE = 256;
export const MIN_ZOOM = 3, MAX_ZOOM = 19;

export const mercX = lon => (lon + 180) / 360;
export const mercY = lat => {
  const r = Math.max(-85.05112878, Math.min(85.05112878, lat)) * Math.PI / 180;
  return (1 - Math.asinh(Math.tan(r)) / Math.PI) / 2;
};
export const lonOf = x => x * 360 - 180;
export const latOf = y => Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180 / Math.PI;

export class TileLayer {
  // url(z, x, y) -> string; mask: draw the tile as a CSS mask over a theme colour;
  // bounds: [north, south, west, east] limits which tiles are requested.
  constructor(el, { url, maxZoom = MAX_ZOOM, mask = false, bounds = null }) {
    this.el = el; this.url = url; this.maxZoom = maxZoom; this.mask = mask; this.bounds = bounds;
    this.tiles = new Map();   // key -> {el, z, x, y, ready}
    this.visible = true;
    this.onLoad = null;       // set by SlippyMap: re-render to drop the backdrop
  }
  setUrl(url) { this.url = url; this.clear(); }
  setBounds(b) { this.bounds = b; this.clear(); }
  clear() { this.tiles.forEach(t => t.el.remove()); this.tiles.clear(); }
  setVisible(v) { this.visible = v; this.el.style.display = v ? '' : 'none'; }

  update(view) {
    if (!this.visible || !this.url) return;
    const z = Math.max(0, Math.min(this.maxZoom, Math.round(view.z)));
    const n = 2 ** z, scale = 2 ** (view.z - z), size = TILE * scale;
    const world = TILE * 2 ** view.z;
    const left = view.cx * world - view.w / 2, top = view.cy * world - view.h / 2;
    let x0 = Math.floor(left / size), x1 = Math.floor((left + view.w) / size);
    let y0 = Math.max(0, Math.floor(top / size)), y1 = Math.min(n - 1, Math.floor((top + view.h) / size));
    if (this.bounds) {
      const [nn, ss, ww, ee] = this.bounds;
      x0 = Math.max(x0, Math.floor(mercX(ww) * n)); x1 = Math.min(x1, Math.floor(mercX(ee) * n));
      y0 = Math.max(y0, Math.floor(mercY(nn) * n)); y1 = Math.min(y1, Math.floor(mercY(ss) * n));
    }
    const want = new Set();
    let pending = 0;
    for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) {
      const xx = ((x % n) + n) % n, key = `${z}/${xx}/${y}/${x}`;
      want.add(key);
      let t = this.tiles.get(key);
      if (!t) { t = this.makeTile(z, xx, y, x); this.tiles.set(key, t); }
      if (!t.ready) pending++;
    }
    // Tiles of other zoom levels stay as a backdrop until this level has loaded.
    this.tiles.forEach((t, key) => {
      if (want.has(key)) return;
      if (t.z === z || !pending) { t.el.remove(); this.tiles.delete(key); }
    });
    this.tiles.forEach(t => {
      const s = TILE * 2 ** (view.z - t.z);
      t.el.style.transform = `translate(${(t.wx * s - left).toFixed(1)}px, ${(t.y * s - top).toFixed(1)}px) scale(${(s / TILE).toFixed(5)})`;
      t.el.style.zIndex = t.z === z ? 2 : 1;
    });
  }

  loaded() {
    // once every tile of the current zoom is in, the backdrop can go
    if (this.onLoad && ![...this.tiles.values()].some(t => !t.ready)) this.onLoad();
  }

  makeTile(z, x, y, wx) {
    const src = this.url(z, x, y);
    const t = { z, x, y, wx, ready: false };
    if (this.mask) {
      const el = document.createElement('div');
      el.className = 'tile';
      const img = new Image();
      img.onload = () => { el.style.webkitMaskImage = el.style.maskImage = `url("${src}")`; el.classList.add('ready'); t.ready = true; this.loaded(); };
      img.onerror = () => { t.ready = true; this.loaded(); };
      img.src = src;
      t.el = el;
    } else {
      const el = document.createElement('img');
      el.className = 'tile'; el.alt = ''; el.draggable = false; el.decoding = 'async';
      el.onload = () => { el.classList.add('ready'); t.ready = true; this.loaded(); };
      el.onerror = () => { t.ready = true; el.style.visibility = 'hidden'; this.loaded(); };
      el.src = src;
      t.el = el;
    }
    this.el.appendChild(t.el);
    return t;
  }
}

export class SlippyMap {
  constructor(el, { onView, onClick, onContext, onMove } = {}) {
    this.el = el; this.layers = [];
    this.view = { cx: mercX(-63.5752), cy: mercY(44.6488), z: 12, w: 0, h: 0 };
    this.onView = onView || (() => {}); this.onClick = onClick; this.onContext = onContext; this.onMove = onMove;
    this.raf = 0;
    this.resize();
    el.addEventListener('wheel', e => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      this.zoomAt(this.view.z - e.deltaY * 0.0022, e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    let drag = null;
    el.addEventListener('pointerdown', e => {
      if (e.button !== 0 || e.target.closest('.mtools, .wpm, .ctx, .mread, .mscale')) return;
      drag = { x: e.clientX, y: e.clientY, cx: this.view.cx, cy: this.view.cy, moved: false, id: e.pointerId };
      el.setPointerCapture(e.pointerId);
    });
    el.addEventListener('pointermove', e => {
      const r = el.getBoundingClientRect();
      if (this.onMove) this.onMove(this.unproject(e.clientX - r.left, e.clientY - r.top));
      if (!drag || drag.id !== e.pointerId) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) { drag.moved = true; el.classList.add('dragging'); }
      const world = TILE * 2 ** this.view.z;
      this.view.cx = drag.cx - dx / world; this.view.cy = Math.max(0, Math.min(1, drag.cy - dy / world));
      this.schedule();
    });
    const end = e => {
      if (drag && !drag.moved && this.onClick && e.type === 'pointerup') {
        const r = el.getBoundingClientRect();
        this.onClick(this.unproject(e.clientX - r.left, e.clientY - r.top), e);
      }
      drag = null; el.classList.remove('dragging');
    };
    el.addEventListener('pointerup', end); el.addEventListener('pointercancel', end);
    el.addEventListener('dblclick', e => {
      if (e.target.closest('.mtools, .wpm, .ctx')) return;
      const r = el.getBoundingClientRect(); this.zoomAt(Math.round(this.view.z) + 1, e.clientX - r.left, e.clientY - r.top);
    });
    el.addEventListener('contextmenu', e => {
      if (e.target.closest('.mtools, .ctx')) return;
      e.preventDefault();
      const r = el.getBoundingClientRect();
      if (this.onContext) this.onContext(this.unproject(e.clientX - r.left, e.clientY - r.top), e.clientX - r.left, e.clientY - r.top, e);
    });
    new ResizeObserver(() => { this.resize(); this.render(); }).observe(el);
  }

  addLayer(layer) { layer.onLoad = () => this.schedule(); this.layers.push(layer); return layer; }
  resize() { this.view.w = this.el.clientWidth; this.view.h = this.el.clientHeight; }
  schedule() { if (!this.raf) this.raf = requestAnimationFrame(() => { this.raf = 0; this.render(); }); }
  render() {
    if (!this.view.w) this.resize();
    if (!this.view.w) return;
    this.layers.forEach(l => l.update(this.view));
    this.onView(this.view);
  }

  project(lat, lon) {
    const world = TILE * 2 ** this.view.z;
    return { x: (mercX(lon) - this.view.cx) * world + this.view.w / 2, y: (mercY(lat) - this.view.cy) * world + this.view.h / 2 };
  }
  unproject(px, py) {
    const world = TILE * 2 ** this.view.z;
    return { lat: latOf(this.view.cy + (py - this.view.h / 2) / world), lon: lonOf(this.view.cx + (px - this.view.w / 2) / world) };
  }
  metresPerPixel() {
    const lat = latOf(this.view.cy);
    return 156543.03392 * Math.cos(lat * Math.PI / 180) / 2 ** this.view.z;
  }
  bounds() {
    const a = this.unproject(0, 0), b = this.unproject(this.view.w, this.view.h);
    return { north: a.lat, west: a.lon, south: b.lat, east: b.lon };
  }

  zoomAt(z, px, py) {
    z = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, z));
    const before = this.unproject(px, py);
    this.view.z = z;
    const world = TILE * 2 ** z;
    this.view.cx = mercX(before.lon) - (px - this.view.w / 2) / world;
    this.view.cy = mercY(before.lat) - (py - this.view.h / 2) / world;
    this.schedule();
  }
  zoomBy(d) { this.zoomAt(this.view.z + d, this.view.w / 2, this.view.h / 2); }
  setView(lat, lon, z) { this.view.cx = mercX(lon); this.view.cy = mercY(lat); if (z != null) this.view.z = z; this.schedule(); }

  // Fit [[lat, lon], ...] with a margin in pixels.
  fit(points, pad = 90, maxZ = 17) {
    if (!points.length) return false;
    this.resize();
    const xs = points.map(p => mercX(p[1])), ys = points.map(p => mercY(p[0]));
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const w = Math.max(this.view.w - pad * 2, 40), h = Math.max(this.view.h - pad * 2, 40);
    const span = Math.max((x1 - x0) / w, (y1 - y0) / h, 1e-12);
    this.view.z = Math.max(MIN_ZOOM, Math.min(maxZ, Math.log2(1 / (span * TILE))));
    this.view.cx = (x0 + x1) / 2; this.view.cy = (y0 + y1) / 2;
    this.schedule();
    return true;
  }
}
