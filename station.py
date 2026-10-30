"""The ground station's core for the web UI, with no UI toolkit.

Station owns everything the operator watches and does: the serial link (a COM
port, or any pyserial URL such as the boat simulator's socket://127.0.0.1:5760),
the controller's parsed lines, the commanded-sail model, telemetry history, the
GPS track, CSV recording and past sessions, and the waypoint route with its depth
along each leg. web_server.py streams its state to the
page and calls its commands; every command returns (ok, message).

One background thread drains the serial reader, advances the commanded sail,
watches for hot-plugged ST-Links and publishes a tick ten times a second.
Commands arrive on the web server's threads, so all state sits behind one lock.
"""

import os
import queue
import sqlite3
import threading
import time
from collections import deque

import serial
import serial.tools.list_ports

from lake_depth import LAKE_DEPTH_DIR, discover_lake_depth_layers
from parsing import parse_sail_rudder, parse_xbee
from recording import Recorder, export_gpx_from_csv, summarize_csv
from route import (
    WaypointStore, BOAT_DRAFT_M, SAFE_DEPTH_M, distance_m, leg_depth_profile,
)
from serial_io import SerialReader, is_stlink
from tile_cache import (
    MAP_CACHE_DB, OPENSEAMAP_TILE_SERVER, TILE_SERVERS, TileCache,
    prepare_tile_cache_database,
)


APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(APP_DIR, "logs")

BAUD_RATES  = ["9600", "19200", "38400", "57600", "115200", "230400", "460800"]
DEFAULT_BAUD = 115200
PORT_POLL_MS = 1000  # how often to scan for COM-port hot-plug / unplug

# The sail is a continuous-rotation drive, not a positional servo: a negative
# command spins it anti-clockwise (viewed from above), a positive command spins
# it clockwise, both at a constant slew rate. The COMMANDED boat integrates this
# over real time instead of treating the stick value as an angle.
SAIL_ROTATION_RATE_DPS = 72.0  # degrees per second
SAIL_CMD_DEADBAND      = 2.0   # |command| below this is treated as "stop"

# The boat simulator (windbound-firmware/sim) serves the controller's serial
# output on this TCP port.
SIMULATOR_URL = "socket://127.0.0.1:5760"

TICK_S = 0.05             # station loop period
PUBLISH_S = 0.1           # how often a tick goes to the page
HISTORY_S = 120.0         # telemetry kept for the trend chart
PACKET_WINDOW_S = 60.0    # packet arrival times kept for the link strip
RATE_WINDOW_S = 5.0       # window for lines/s and packets/s
SPEED_WINDOW_S = 10.0     # GPS fixes used for speed over ground
LOG_LINES = 5000          # lines kept for the log view
TARGET_MATCH_M = 5.0      # telemetry target within this of a waypoint is it
STALE_S = 2.0             # telemetry older than this is stale (as the Tk app)


def _position_ok(lat, lon):
    """A GPS fix worth drawing: not 0,0 / no-fix and not whole degrees.

    Today's firmware formats clat/clon with %d, so the position arrives
    truncated to whole degrees (about 88 km out at Lake Micmac). Such a fix
    is reported as truncated instead of being drawn in the wrong place.
    """
    if lat is None or lon is None:
        return False
    if abs(lat) <= 1e-4 and abs(lon) <= 1e-4:
        return False
    return not (float(lat).is_integer() and float(lon).is_integer())


def _position_truncated(lat, lon):
    return (lat is not None and lon is not None
            and (abs(lat) > 1e-4 or abs(lon) > 1e-4)
            and float(lat).is_integer() and float(lon).is_integer())


def _line_type(line):
    if parse_sail_rudder(line) is not None:
        return "ctrl"
    if "XBee RX" in line:
        return "xbee"
    return "other"


def _validate_image(data):
    """Return tile bytes unchanged if they decode as an image, else raise."""
    import io
    from PIL import Image
    with Image.open(io.BytesIO(data)) as img:
        img.verify()
    return data


class Station:
    def __init__(self, log_dir=LOG_DIR, lake_dir=LAKE_DEPTH_DIR,
                 tile_db=MAP_CACHE_DB):
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._subscribers = set()
        self._thread = None

        # serial link
        self.ser = None
        self.reader = None
        self.reader_stop = None
        self.rx = queue.Queue()
        self.device = None
        self.baud = DEFAULT_BAUD
        self.conn_error = None
        self.autoconnect = True
        self.known_devices = None    # set of COM device names; None until 1st scan
        self.auto_target = None      # ST-Link device we want to auto-connect to
        self._auto_fail_logged = set()
        self.ports = []

        # what the controller and the boat report
        self.status = None           # latest sail/rudder/dropped/overruns
        self.telemetry = {}          # latest value of every telemetry field
        self.telemetry_at = None     # wall time of the last packet
        self.packets = 0
        self.packet_times = deque()  # wall times, last PACKET_WINDOW_S
        self.line_times = deque()    # wall times, last RATE_WINDOW_S
        self.history = deque()       # (wall time, fields), last HISTORY_S
        self.gps_track = []          # [(lat, lon, wall time)] since start
        self.position_truncated = False

        # commanded-sail model
        self.cmd_sail_input = 0.0
        self.cmd_sail_angle = 0.0
        self.cmd_rudder = 0.0
        self._last_sail_tick = None
        # The commanded boat starts where the boat reports its sail, so the
        # two drawings agree until the stick moves (the Tk app starts at 0).
        self._seed_cmd_sail = True

        # log
        self.lines = deque(maxlen=LOG_LINES)
        self._line_seq = 0
        self._pending_lines = []
        self._pending_packets = []

        # recording and sessions
        self.log_dir = log_dir
        self.recorder = Recorder(log_dir=log_dir)
        self.recording_started = None
        self._session_cache = {}

        # route and lake depth
        self.waypoints = WaypointStore()
        self.lakes = discover_lake_depth_layers(lake_dir)
        self.lake_name = ("Lake Micmac" if "Lake Micmac" in self.lakes
                          else next(iter(self.lakes), None))
        self._leg_cache = {}
        self._lake_info = {name: self._describe_lake(name, layer)
                           for name, layer in self.lakes.items()}
        self.waypoints.add_listener(self._route_changed)

        # map tiles
        self.tile_db = tile_db
        self.tiles = TileCache()
        self._tile_local = threading.local()
        self._depth_tile_cache = {}
        prepare_tile_cache_database(tile_db)

        self.refresh_ports()

    # ===================================================================== #
    # lifecycle and publishing
    # ===================================================================== #

    def start(self):
        self._thread = threading.Thread(target=self._run, name="station",
                                        daemon=True)
        self._thread.start()

    def close(self):
        """Stop the loop, finish any recording and close the port."""
        self._stop.set()
        with self._lock:
            if self.recorder.active:
                self.log(self.recorder.stop())
            self._close_port()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def subscribe(self):
        """Return (queue, hello) for a new page; events follow on the queue."""
        q = queue.Queue(maxsize=2000)
        with self._lock:
            hello = self._hello()
            self._subscribers.add(q)
        return q, hello

    def unsubscribe(self, q):
        with self._lock:
            self._subscribers.discard(q)

    def _publish(self, event, data):
        dead = []
        for q in list(self._subscribers):
            try:
                q.put_nowait((event, data))
            except queue.Full:
                dead.append(q)      # a page that stopped reading
        for q in dead:
            self._subscribers.discard(q)
            try:
                q.put_nowait(("bye", None))
            except queue.Full:
                pass

    def note(self, title, desc="", tone="info"):
        """Show a toast on every open page."""
        self._publish("note", {"title": title, "desc": desc, "tone": tone})

    def log(self, text, kind="app"):
        """Add an app or transmit line to the log, like the Tk app's '** **' lines."""
        with self._lock:
            self._add_line(text, kind)

    def _add_line(self, text, kind):
        self._line_seq += 1
        entry = {"id": self._line_seq, "t": time.time(), "type": kind, "text": text}
        self.lines.append(entry)
        self._pending_lines.append(entry)

    def _run(self):
        last_watch = 0.0
        last_publish = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            with self._lock:
                self._drain_reader()
                self._advance_commanded_sail(now)
                if now - last_watch >= PORT_POLL_MS / 1000.0:
                    last_watch = now
                    self._watch_ports()
                if now - last_publish >= PUBLISH_S:
                    last_publish = now
                    self._publish_tick()
            self._stop.wait(TICK_S)

    # ===================================================================== #
    # snapshots sent to the page
    # ===================================================================== #

    def _hello(self):
        return {
            "now": time.time(),
            "config": {
                "baud_rates": [int(b) for b in BAUD_RATES],
                "simulator_url": SIMULATOR_URL,
                "sail_rate_dps": SAIL_ROTATION_RATE_DPS,
                "sail_deadband": SAIL_CMD_DEADBAND,
                "boat_draft_m": BOAT_DRAFT_M,
                "safe_depth_m": SAFE_DEPTH_M,
                "stale_s": STALE_S,
                "tile_servers": list(TILE_SERVERS.keys()),
                "log_dir": os.path.relpath(self.log_dir, APP_DIR),
            },
            "lakes": list(self._lake_info.values()),
            "conn": self._conn_state(),
            "route": self._route_state(),
            "rec": self._rec_state(),
            "sessions": self._sessions_state(),
            "tiles": self._tile_stats(),
            "history": [[t, fields] for t, fields in self.history],
            "packet_times": list(self.packet_times),
            "track": [[lat, lon, t] for lat, lon, t in self.gps_track[-1500:]],
            "lines": list(self.lines),
            "tick": self._tick_state(),
        }

    def _tick_state(self):
        now = time.time()
        self._trim(now)
        tel = dict(self.telemetry)
        lat, lon = tel.get("clat"), tel.get("clon")
        tlat, tlon = tel.get("tlat"), tel.get("tlon")
        here = (lat, lon) if _position_ok(lat, lon) else None
        target = (tlat, tlon) if _position_ok(tlat, tlon) else None
        return {
            "now": now,
            "connected": self.ser is not None,
            "status": self.status,
            "telemetry": tel,
            "telemetry_age_s": (now - self.telemetry_at) if self.telemetry_at else None,
            "packets": self.packets,
            "packet_rate": self._rate(self.packet_times, now),
            "line_rate": self._rate(self.line_times, now),
            "cmd_sail": self.cmd_sail_angle,
            "cmd_sail_input": self.cmd_sail_input,
            "cmd_rudder": self.cmd_rudder,
            "position": list(here) if here else None,
            "position_truncated": self.position_truncated,
            "speed_mps": self._speed(now),
            "target": list(target) if target else None,
            "target_id": self._target_id(target),
            "target_m": distance_m(here, target) if here and target else None,
            "rec_rows": self.recorder.rows if self.recorder.active else None,
        }

    def _publish_tick(self):
        tick = self._tick_state()
        tick["lines"] = self._pending_lines[-400:]
        tick["lines_dropped"] = max(0, len(self._pending_lines) - 400)
        tick["new_packets"] = self._pending_packets
        self._pending_lines = []
        self._pending_packets = []
        self._publish("tick", tick)

    @staticmethod
    def _rate(times, now):
        n = sum(1 for t in times if t > now - RATE_WINDOW_S)
        return n / RATE_WINDOW_S

    def _trim(self, now):
        while self.packet_times and self.packet_times[0] < now - PACKET_WINDOW_S:
            self.packet_times.popleft()
        while self.line_times and self.line_times[0] < now - RATE_WINDOW_S:
            self.line_times.popleft()
        while self.history and self.history[0][0] < now - HISTORY_S:
            self.history.popleft()

    def _speed(self, now):
        """Speed over ground from the GPS fixes in the last few seconds."""
        recent = [p for p in self.gps_track[-60:] if p[2] > now - SPEED_WINDOW_S]
        if len(recent) < 2 or recent[-1][2] - recent[0][2] < 1.0:
            return None
        metres = sum(distance_m(a[:2], b[:2]) for a, b in zip(recent, recent[1:]))
        return metres / (recent[-1][2] - recent[0][2])

    def _target_id(self, target):
        if target is None:
            return None
        best, best_m = None, TARGET_MATCH_M
        for w in self.waypoints.items():
            d = distance_m((w["lat"], w["lon"]), target)
            if d <= best_m:
                best, best_m = w["id"], d
        return best

    # ===================================================================== #
    # serial connection
    # ===================================================================== #

    def _conn_state(self):
        return {
            "state": "connected" if self.ser is not None else "disconnected",
            "device": self.device,
            "label": self._device_label(self.device),
            "baud": self.baud,
            "autoconnect": self.autoconnect,
            "ports": self.ports,
            "error": self.conn_error,
        }

    def _device_label(self, device):
        if device is None:
            return None
        if device == SIMULATOR_URL:
            return "Simulator"
        return device

    def _publish_conn(self):
        self._publish("conn", self._conn_state())

    def refresh_ports(self):
        with self._lock:
            ports = []
            for p in serial.tools.list_ports.comports():
                desc = p.description or "Unknown device"
                ports.append({"device": p.device, "description": desc,
                              "stlink": is_stlink(p), "sim": False})
            ports.append({"device": SIMULATOR_URL,
                          "description": "Boat simulator (windbound-firmware/sim)",
                          "stlink": False, "sim": True})
            self.ports = ports
            self._publish_conn()
            return True, f"Found {len(ports) - 1} COM port{'s' if len(ports) != 2 else ''}"

    def set_autoconnect(self, on):
        with self._lock:
            self.autoconnect = bool(on)
            self._publish_conn()
            return True, "Auto-connect on" if on else "Auto-connect off"

    def set_baud(self, baud):
        with self._lock:
            if str(baud) not in BAUD_RATES:
                return False, f"{baud} isn't a supported baud rate"
            self.baud = int(baud)
            self._publish_conn()
            return True, f"Baud set to {self.baud}"

    def connect(self, device=None, baud=None, quiet=False):
        """Open a COM port or pyserial URL. Returns (ok, message)."""
        with self._lock:
            if self.ser is not None:
                self._close_port()
            device = (device or "").strip() or self.device
            if not device:
                return False, "Choose a port first."
            if baud is not None:
                ok, msg = self.set_baud(baud)
                if not ok:
                    return ok, msg
            try:
                # serial_for_url opens COM ports and socket:// URLs alike.
                self.ser = serial.serial_for_url(device, self.baud, timeout=0.2)
            except (serial.SerialException, OSError, ValueError) as e:
                self.ser = None
                self.conn_error = f"Could not open {device}: {e}"
                if not quiet:
                    self.log(self.conn_error)
                self._publish_conn()
                return False, self.conn_error
            self.device = device
            self.conn_error = None
            self._seed_cmd_sail = True
            self.rx = queue.Queue()
            self.reader_stop = threading.Event()
            self.reader = SerialReader(self.ser, self.rx, self.reader_stop)
            self.reader.start()
            self.log(f"Connected to {device} at {self.baud} baud")
            self._publish_conn()
            return True, f"Connected to {self._device_label(device)}"

    def disconnect(self):
        with self._lock:
            if self.ser is None:
                return False, "Not connected"
            device = self.device
            self._close_port()
            # A manual disconnect (or a cable pull) clears the auto-connect
            # target, so the watcher won't immediately grab the same port
            # again. It only auto-connects after an unplug and re-plug.
            self.auto_target = None
            self.log(f"Disconnected from {device}")
            self._publish_conn()
            return True, f"Disconnected from {self._device_label(device)}"

    def _close_port(self):
        if self.reader_stop:
            self.reader_stop.set()
        if self.reader and self.reader.is_alive():
            self.reader.join(timeout=1.0)
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
        self.reader = None
        self.reader_stop = None
        # Stop the commanded sail from spinning once data stops arriving.
        self.cmd_sail_input = 0.0

    def _watch_ports(self):
        """Scan COM ports; auto-connect to a newly plugged-in ST-Link.

        The first pass only records a baseline of the ports already present,
        so a board that was plugged in before the program started is not
        grabbed; only devices that appear after launch trigger auto-connect.
        """
        try:
            found = {p.device: p for p in serial.tools.list_ports.comports()}
        except Exception:
            return
        devices = set(found)
        if self.known_devices is None:
            self.known_devices = devices
            return
        added = devices - self.known_devices
        removed = self.known_devices - devices
        self.known_devices = devices
        if added or removed:
            self.refresh_ports()
        for dev in sorted(added):
            if is_stlink(found[dev]):
                self.auto_target = dev
                self._auto_fail_logged.discard(dev)
                self.log(f"ST-Link detected on {dev}")
        if self.auto_target and self.auto_target not in devices:
            self.auto_target = None
        if self.autoconnect and self.ser is None and self.auto_target:
            ok, _ = self.connect(device=self.auto_target, quiet=True)
            if ok:
                self.note(f"Connected to {self.auto_target}",
                          "An ST-Link was plugged in.", "pos")
            elif self.auto_target not in self._auto_fail_logged:
                self.log(f"Could not open {self.auto_target} yet; retrying...")
                self._auto_fail_logged.add(self.auto_target)

    # ===================================================================== #
    # incoming lines
    # ===================================================================== #

    def _drain_reader(self):
        while True:
            try:
                kind, payload = self.rx.get_nowait()
            except queue.Empty:
                return
            if kind == "line":
                self._handle_line(payload)
            elif kind == "error":
                device = self.device
                self.log(payload)
                self._close_port()
                self.auto_target = None
                self.conn_error = payload
                self._publish_conn()
                self.note(f"Lost {self._device_label(device)}", payload, "warn")

    def _handle_line(self, line):
        now = time.time()
        self._add_line(line, _line_type(line))
        self.line_times.append(now)

        sr = parse_sail_rudder(line)
        if sr is not None:
            self.status = sr
            # The sail command sets a rotation direction, not an angle; the
            # rudder is positional and applied directly.
            self.cmd_sail_input = float(sr["sail"])
            self.cmd_rudder = float(sr["rudder"])
            return

        tel = parse_xbee(line)
        if tel is None:
            return
        self.telemetry.update(tel)
        self.telemetry_at = now
        if self._seed_cmd_sail and "sa" in tel and abs(self.cmd_sail_input) < SAIL_CMD_DEADBAND:
            self.cmd_sail_angle = float(tel["sa"]) % 360.0
            self._seed_cmd_sail = False
        self.packets += 1
        self.packet_times.append(now)
        self.history.append((now, tel))
        self._pending_packets.append([now, tel])

        lat, lon = tel.get("clat"), tel.get("clon")
        self.position_truncated = _position_truncated(lat, lon)
        if _position_ok(lat, lon):
            if not self.gps_track or self.gps_track[-1][:2] != (lat, lon):
                self.gps_track.append((lat, lon, now))

        if self.recorder.active:
            self.recorder.write_row(tel)

    def _advance_commanded_sail(self, now):
        """Advance the commanded sail at a constant slew rate.

        Same continuous-rotation model as the Tk app: the sail spins at
        SAIL_ROTATION_RATE_DPS while the stick is out of the dead band and
        holds otherwise.
        """
        if self._last_sail_tick is None:
            self._last_sail_tick = now
        dt = now - self._last_sail_tick
        self._last_sail_tick = now
        if abs(self.cmd_sail_input) >= SAIL_CMD_DEADBAND:
            direction = 1.0 if self.cmd_sail_input > 0 else -1.0
            self.cmd_sail_angle = (self.cmd_sail_angle
                                   + direction * SAIL_ROTATION_RATE_DPS * dt) % 360.0

    def align_commanded_sail(self):
        """Line the commanded sail up with the sail angle the boat reports."""
        with self._lock:
            sa = self.telemetry.get("sa")
            if sa is None:
                return False, "No sail angle from the boat yet"
            self.cmd_sail_angle = float(sa) % 360.0
            return True, f"Commanded sail set to {self.cmd_sail_angle:.0f}°"

    def clear_log(self):
        with self._lock:
            self.lines.clear()
            self._pending_lines = []
            self._publish("log_cleared", None)
            return True, "Cleared the log view"

    # ===================================================================== #
    # recording and sessions
    # ===================================================================== #

    def _rec_state(self):
        return {
            "active": self.recorder.active,
            "path": (os.path.relpath(self.recorder.path, APP_DIR)
                     if self.recorder.path else None),
            "file": os.path.basename(self.recorder.path) if self.recorder.path else None,
            "rows": self.recorder.rows,
            "started": self.recording_started,
        }

    def set_recording(self, on):
        with self._lock:
            if on and not self.recorder.active:
                ok, msg = self.recorder.start()
                self.log(msg)
                if ok:
                    self.recording_started = time.time()
            elif not on and self.recorder.active:
                msg = self.recorder.stop()
                self.log(msg)
                ok = True
            else:
                return True, "Already recording" if on else "Not recording"
            self._publish("rec", self._rec_state())
            self._publish("sessions", self._sessions_state())
            return ok, msg

    def _sessions_state(self):
        """Every telemetry CSV in the log folder, newest first."""
        try:
            names = [n for n in os.listdir(self.log_dir)
                     if n.startswith("telemetry_") and n.endswith(".csv")]
        except OSError:
            names = []
        out = []
        live = os.path.basename(self.recorder.path) if self.recorder.active else None
        for name in sorted(names, reverse=True):
            path = os.path.join(self.log_dir, name)
            if name == live:
                out.append({"file": name, "path": os.path.relpath(path, APP_DIR),
                            "live": True, "started": self.recording_started})
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue
            key = (st.st_mtime_ns, st.st_size)
            cached = self._session_cache.get(name)
            if cached is None or cached[0] != key:
                summary = summarize_csv(path)
                summary["lake"] = self._lake_for_box(summary.get("box"))
                self._session_cache[name] = (key, summary)
            summary = self._session_cache[name][1]
            out.append(dict(summary, file=name, path=os.path.relpath(path, APP_DIR),
                            live=False))
        return out

    def export_session_gpx(self, file_name):
        with self._lock:
            name = os.path.basename(str(file_name))
            path = os.path.join(self.log_dir, name)
            if not (name.endswith(".csv") and os.path.isfile(path)):
                return False, f"No session called {name}"
            ok, msg, gpx = export_gpx_from_csv(path)
            if ok:
                self.log(msg)
                msg = msg.replace(gpx, os.path.relpath(gpx, APP_DIR))
            return ok, msg

    # ===================================================================== #
    # route
    # ===================================================================== #

    def _lake(self):
        return self.lakes.get(self.lake_name) if self.lake_name else None

    def _depth_at(self, lat, lon):
        lake = self._lake()
        return lake.depth_at(lat, lon) if lake is not None else None

    def depth_probe(self, lat, lon):
        with self._lock:
            lake = self._lake()
            if lake is None:
                return {"ok": False, "reason": "Choose a lake with a depth grid first."}
            est = lake.depth_at(lat, lon)
            if est is None:
                reason = getattr(lake, "_depth_grid_error", None) or \
                    "outside the depth grid, or over land"
                return {"ok": False, "lake": lake.name, "reason": reason}
            return {"ok": True, "lake": lake.name, "depth_m": est["depth_m"],
                    "uncertainty_m": est["uncertainty_m"]}

    def _leg(self, a, b):
        lake = self._lake()
        key = (round(a["lat"], 8), round(a["lon"], 8), round(b["lat"], 8),
               round(b["lon"], 8), lake.cache_id if lake is not None else None)
        prof = self._leg_cache.get(key)
        if prof is None:
            prof = leg_depth_profile(a, b, self._depth_at if lake is not None else None)
            if len(self._leg_cache) > 400:
                self._leg_cache.clear()
            self._leg_cache[key] = prof
        return prof

    def _route_state(self):
        items = self.waypoints.items()
        changed, removed, reordered = self.waypoints.changes()
        state, detail = self.waypoints.sync_state()
        out_items = []
        for w in items:
            est = self._depth_at(w["lat"], w["lon"])
            out_items.append({"id": w["id"], "name": w["name"], "lat": w["lat"],
                              "lon": w["lon"],
                              "depth_m": est["depth_m"] if est else None,
                              "change": changed.get(w["id"])})
        legs = []
        for a, b in zip(items, items[1:]):
            p = self._leg(a, b)
            legs.append({"from": a["id"], "to": b["id"], "length_m": p["length_m"],
                         "segments": p["segments"], "min_depth_m": p["min_depth_m"],
                         "metres": p["metres"]})
        n_changes = len(changed) + removed + (1 if reordered else 0)
        return {"items": out_items, "legs": legs, "lake": self.lake_name,
                "sync": state, "sync_detail": detail, "changes": n_changes,
                "sent": self.waypoints.sent_count()}

    def _route_changed(self, structural=True):
        self._publish("route", self._route_state())

    def route_command(self, action, body):
        with self._lock:
            store = self.waypoints
            if action == "add":
                wp = store.add(float(body["lat"]), float(body["lon"]), body.get("name"))
                self.log(f"Added waypoint {wp['name']} at {wp['lat']:.5f}, {wp['lon']:.5f}")
                return True, f"Added {wp['name']}"
            wp_id = body.get("id")
            if action == "remove":
                store.remove(int(wp_id))
                return True, "Removed the waypoint"
            if action == "rename":
                name = str(body.get("name", "")).strip()[:60]
                if not name:
                    return False, "A waypoint needs a name"
                store.rename(int(wp_id), name)
                return True, f"Renamed to {name}"
            if action == "update":
                store.update_position(int(wp_id), float(body["lat"]), float(body["lon"]))
                return True, "Moved the waypoint"
            if action == "move":
                store.move(int(body["from"]), int(body["to"]))
                return True, "Reordered the route"
            if action == "clear":
                n = store.count()
                store.clear()
                self.log("Cleared all waypoints.")
                return True, f"Cleared {n} waypoints"
            if action == "restore":
                store.restore_synced()
                self.log("Restored last synced waypoint list.")
                return True, "Restored the last route marked as sent"
            if action == "send":
                if store.count() == 0:
                    return False, "No waypoints to send"
                payload = store.to_json()
                # Two-way comms isn't wired yet (same as the Tk app's
                # _send_waypoints_to_boat): log the payload for the radio link.
                self.log(f"TX waypoints: {payload}", kind="tx")
                store.mark_synced()
                return True, f"Marked {store.count()} waypoints as sent"
            return False, f"Unknown route action {action}"

    def set_lake(self, name):
        with self._lock:
            if name is not None and name not in self.lakes:
                return False, f"No depth grid for {name}"
            self.lake_name = name
            self._route_changed()
            return True, f"Depth from {name}" if name else "Lake depth off"

    def _describe_lake(self, name, layer):
        info = {"name": name, "north": layer.north, "south": layer.south,
                "west": layer.west, "east": layer.east,
                "center": list(layer.center), "zoom": layer.preferred_zoom,
                "has_grid": layer.depth_grid is not None,
                "spacing_m": layer.depth_grid_spacing_m,
                "uncertainty_m": layer.depth_uncertainty_m,
                "source_url": layer.source_url,
                "max_depth_m": None, "cells": None}
        if layer.depth_grid is not None:
            import numpy as np
            info["max_depth_m"] = float(np.nanmax(layer.depth_grid))
            info["cells"] = list(layer.depth_grid.shape)
        return info

    def _lake_for_box(self, box):
        if not box:
            return None
        lat = (box[0] + box[1]) / 2.0
        lon = (box[2] + box[3]) / 2.0
        for name, info in self._lake_info.items():
            if info["south"] <= lat <= info["north"] and info["west"] <= lon <= info["east"]:
                return name
        return None

    # ===================================================================== #
    # map tiles
    # ===================================================================== #

    def _tile_cursor(self):
        conn = getattr(self._tile_local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.tile_db, timeout=10, check_same_thread=False)
            self._tile_local.conn = conn
        return conn.cursor()

    def base_tile(self, server_name, z, x, y):
        url = TILE_SERVERS.get(server_name)
        if url is None:
            return None
        return self.tiles.get(url, z, x, y, self._tile_cursor(), decode=_validate_image)

    def seamark_tile(self, z, x, y):
        if z > 18:
            return None
        return self.tiles.get(OPENSEAMAP_TILE_SERVER, z, x, y, self._tile_cursor(),
                              decode=_validate_image, max_zoom=18)

    def depth_tile(self, lake_name, z, x, y):
        """The lake's calibrated overlay rendered into one XYZ tile, as PNG."""
        layer = self.lakes.get(lake_name)
        if layer is None:
            return None
        key = (lake_name, z, x, y)
        cached = self._depth_tile_cache.get(key)
        if cached is not None:
            return cached
        img = layer.render_tile(z, x, y, 256, opacity=1.0)
        if img is None:
            data = b""
        else:
            import io
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            data = buf.getvalue()
        if len(self._depth_tile_cache) > 800:
            self._depth_tile_cache.clear()
        self._depth_tile_cache[key] = data
        return data

    def _tile_stats(self):
        try:
            conn = sqlite3.connect(self.tile_db, timeout=5)
            try:
                count, size = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(LENGTH(tile_image)), 0) FROM tiles").fetchone()
            finally:
                conn.close()
            return {"count": count, "bytes": size,
                    "path": os.path.relpath(self.tile_db, APP_DIR)}
        except sqlite3.Error:
            return {"count": None, "bytes": None,
                    "path": os.path.relpath(self.tile_db, APP_DIR)}


