"""Local web server for the Windbound web UI.

Serves the page in web/ and lets it talk to the Station (station.py):

    GET  /api/stream          Server-Sent Events: "hello" (everything), then
                              "tick" ten times a second, and "conn", "route",
                              "rec", "sessions", "tiles", "note" and
                              "log_cleared" when they change
    GET  /api/depth?lat=&lon= approximate depth from the selected lake's grid
    GET  /tiles/base/<style>/<z>/<x>/<y>     map tiles via the shared cache
    GET  /tiles/seamarks/<z>/<x>/<y>         OpenSeaMap seamarks
    GET  /tiles/depth/<lake>/<z>/<x>/<y>     the lake's depth contours
    POST /api/<area>          {"action": ..., ...} for connection, record,
                              route, lake, log, sail and sessions

It listens on 127.0.0.1 only, answers only requests addressed to it by that
name (so a site that rebinds its own name to 127.0.0.1 can't read it), and
takes commands only as JSON from its own page, so another website can't open
ports or edit the route.
"""

import json
import mimetypes
import queue
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

WEB_DIR = Path(__file__).resolve().parent / "web"
DEFAULT_PORT = 8765
MAX_BODY = 64 * 1024
KEEPALIVE_S = 10.0
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
                 ".png": "image/png", ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8"}


def _empty_png():
    """A 1x1 transparent PNG, for depth tiles with no contours in them."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (1, 1), (0, 0, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


EMPTY_PNG = _empty_png()


class WebServer:
    def __init__(self, station, host="127.0.0.1", port=DEFAULT_PORT):
        self.station = station
        server = self

        class Handler(_Handler):
            owner = server

        self.httpd = None
        last_error = None
        # The first free port from the one asked for.
        for candidate in range(port, port + 20):
            try:
                self.httpd = ThreadingHTTPServer((host, candidate), Handler)
                break
            except OSError as e:
                last_error = e
        if self.httpd is None:
            raise OSError(f"No free port from {port} to {port + 19}: {last_error}")
        self.httpd.daemon_threads = True
        self.host, self.port = self.httpd.server_address[:2]

    @property
    def url(self):
        return f"http://{self.host}:{self.port}/"

    @property
    def own_hosts(self):
        return {f"{self.host}:{self.port}", f"localhost:{self.port}"}

    def serve_forever(self):
        self.httpd.serve_forever(poll_interval=0.25)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    # ----- commands from the page ------------------------------------------ #

    def command(self, area, body):
        st = self.station
        action = body.get("action")
        if area == "connection":
            if action == "connect":
                return st.connect(body.get("device"), body.get("baud"))
            if action == "disconnect":
                return st.disconnect()
            if action == "rescan":
                return st.refresh_ports()
            if action == "autoconnect":
                return st.set_autoconnect(bool(body.get("on")))
            if action == "baud":
                return st.set_baud(body.get("baud"))
        elif area == "sail" and action == "align":
            return st.align_commanded_sail()
        elif area == "record":
            return st.set_recording(bool(body.get("on")))
        elif area == "route":
            return st.route_command(action, body)
        elif area == "lake":
            return st.set_lake(body.get("name") or None)
        elif area == "log" and action == "clear":
            return st.clear_log()
        elif area == "sessions" and action == "gpx":
            return st.export_session_gpx(body.get("file", ""))
        raise ValueError(f"Unknown command {area}/{action}")


class _Handler(BaseHTTPRequestHandler):
    owner: WebServer
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path
        if self.headers.get("Host") not in self.owner.own_hosts:
            self._send(421, "text/plain", b"Open Windbound at its own address.")
        elif path == "/api/stream":
            self._stream()
        elif path == "/api/depth":
            q = parse_qs(url.query)
            try:
                lat, lon = float(q["lat"][0]), float(q["lon"][0])
            except (KeyError, ValueError, IndexError):
                self._json({"ok": False, "reason": "lat and lon are required"}, 400)
                return
            self._json(self.owner.station.depth_probe(lat, lon))
        elif path.startswith("/tiles/"):
            self._tile(path)
        else:
            self._static(path)

    def do_POST(self):
        path = urlsplit(self.path).path
        if self.headers.get("Host") not in self.owner.own_hosts:
            self._send(421, "text/plain", b"Open Windbound at its own address.")
            return
        if not path.startswith("/api/") or not self._from_own_page():
            self._send(403, "text/plain", b"Commands are accepted from the Windbound page only.")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._send(413, "text/plain", b"Too large")
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
            ok, message = self.owner.command(path[len("/api/"):], body)
        except (ValueError, KeyError, TypeError) as exc:
            self._json({"ok": False, "message": str(exc)}, 400)
            return
        self._json({"ok": bool(ok), "message": message}, 200 if ok else 409)

    # ----- helpers ---------------------------------------------------------- #

    def _from_own_page(self):
        """JSON only (a cross-site form can't send it without a CORS preflight,
        which this server never approves), and from this server's own origin."""
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://{h}" for h in self.owner.own_hosts}

    def _static(self, path):
        name = "index.html" if path == "/" else unquote(path).lstrip("/")
        file = (WEB_DIR / name).resolve()
        # Only files inside web/ with a known type are served.
        if WEB_DIR not in file.parents or not file.is_file():
            self._send(404, "text/plain", b"Not found")
            return
        ctype = CONTENT_TYPES.get(file.suffix) or mimetypes.guess_type(file.name)[0]
        if ctype is None:
            self._send(404, "text/plain", b"Not found")
            return
        cache = file.suffix in (".woff2", ".png")
        self._send(200, ctype, file.read_bytes(), cache=cache)

    def _tile(self, path):
        parts = [unquote(p) for p in path.split("/")[2:]]
        st = self.owner.station
        try:
            kind = parts[0]
            z, x, y = (int(p.split(".")[0]) for p in parts[-3:])
            if not (0 <= z <= 20 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
                raise ValueError
            if kind == "base" and len(parts) == 5:
                data = st.base_tile(parts[1], z, x, y)
            elif kind == "seamarks" and len(parts) == 4:
                data = st.seamark_tile(z, x, y)
            elif kind == "depth" and len(parts) == 5:
                data = st.depth_tile(parts[1], z, x, y)
                if data == b"":
                    data = EMPTY_PNG
            else:
                raise ValueError
        except (ValueError, IndexError):
            self._send(404, "text/plain", b"Not found")
            return
        if data is None:
            self._send(404, "text/plain", b"Tile unavailable")
            return
        self._send(200, "image/png", data, cache=True)

    def _json(self, data, status=200):
        self._send(status, "application/json", json.dumps(data).encode())

    def _send(self, status, content_type, body, cache=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _event(self, name, data):
        try:
            payload = json.dumps(data, separators=(",", ":"), allow_nan=False, default=str)
        except ValueError:
            # NaN or infinity isn't JSON; send it as null instead.
            clean = json.loads(json.dumps(data, default=str), parse_constant=lambda _: None)
            payload = json.dumps(clean, separators=(",", ":"))
        self.wfile.write(f"event: {name}\ndata: {payload}\n\n".encode())

    def _stream(self):
        station = self.owner.station
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        q, hello = station.subscribe()
        last_write = time.monotonic()
        try:
            self._event("hello", hello)
            self.wfile.flush()
            while True:
                try:
                    event, data = q.get(timeout=1.0)
                except queue.Empty:
                    if time.monotonic() - last_write > KEEPALIVE_S:
                        self.wfile.write(b": still here\n\n")
                        self.wfile.flush()
                        last_write = time.monotonic()
                    continue
                if event == "bye":
                    break
                self._event(event, data)
                # Send whatever else is already waiting in the same write.
                while True:
                    try:
                        event, data = q.get_nowait()
                    except queue.Empty:
                        break
                    if event == "bye":
                        return
                    self._event(event, data)
                self.wfile.flush()
                last_write = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            station.unsubscribe(q)
            self.close_connection = True

    def log_message(self, format, *args):
        pass
