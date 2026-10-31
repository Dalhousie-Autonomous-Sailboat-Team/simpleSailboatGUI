"""The boat simulator (windbound-firmware/sim), from the ground station's side.

The simulator pretends to be the joystick controller on a TCP port
(socket://127.0.0.1:5760), which the station opens like any serial port. It
also serves its own page, whose Mission control and Firmware checks the web
UI shows as two more tabs. This module finds a simulator that is running,
finds a windbound-firmware checkout to start one from, and starts and stops
it for ``windbound_web.py --simulator``.

Everything here talks to 127.0.0.1 only.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PAGE_PORTS = range(8765, 8785)   # the simulator's start.py takes the first free port from 8765
LINK_PORT = 5760
FIRST_START_S = 900              # the first start builds the firmware and installs packages


def listening(port):
    """Something accepts connections on this local port."""
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def free_port(start):
    return next(port for port in range(start, start + 50) if not listening(port))


def probe(port, timeout=0.5):
    """The simulator's own description from this port, or None if no simulator answers there."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/info", timeout=timeout) as response:
            info = json.loads(response.read())
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) and info.get("app") == "windbound-sim" else None


def page_url(port):
    return f"http://127.0.0.1:{port}/"


def find_running(link_port=None, skip=()):
    """(page URL, link port) of a running simulator, the one serving
    ``link_port`` when that is given, or None."""
    for port in PAGE_PORTS:
        if port in skip or not listening(port):
            continue
        info = probe(port)
        if info and (link_port is None or info.get("link_port") == link_port):
            return page_url(port), info.get("link_port")
    return None


def link_port_of(device):
    """The port in a local socket:// URL, the way the simulator is reached; else None."""
    for prefix in ("socket://127.0.0.1:", "socket://localhost:"):
        if device and device.startswith(prefix) and device[len(prefix):].isdigit():
            return int(device[len(prefix):])
    return None


def find_checkout(path=None):
    """A windbound-firmware checkout with the simulator in it: ``path``,
    $WINDBOUND_FIRMWARE_DIR, or one next to this repository."""
    candidates = [Path(path).expanduser()] if path else []
    if os.environ.get("WINDBOUND_FIRMWARE_DIR"):
        candidates.append(Path(os.environ["WINDBOUND_FIRMWARE_DIR"]).expanduser())
    candidates.append(APP_DIR.parent / "windbound-firmware")
    return next((c.resolve() for c in candidates if (c / "sim" / "start.py").is_file()), None)


class SimulatorProcess:
    """The simulator's own launcher (sim/start.py) running in the background
    on the ports given, its output passed to ``on_line``, until stop()."""

    def __init__(self, checkout, port, link_port, on_line=print):
        self.checkout, self.port, self.link_port = Path(checkout), port, link_port
        self.on_line = on_line
        self.tail = deque(maxlen=12)       # its last lines, to explain a failed start
        self.proc = None

    @property
    def page(self):
        return page_url(self.port)

    @property
    def link_url(self):
        return f"socket://127.0.0.1:{self.link_port}"

    def start(self):
        env = dict(os.environ)
        # The lake comes from this repository's depth grids, wherever the checkout is.
        env.setdefault("WINDBOUND_LAKE_DIR", str(APP_DIR / "lake_depth_maps"))
        env["PYTHONUNBUFFERED"] = "1"
        command = [sys.executable, str(self.checkout / "sim" / "start.py"), "--no-open",
                   "--port", str(self.port), "--link-port", str(self.link_port)]
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.proc = subprocess.Popen(command, cwd=self.checkout, env=env, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace", creationflags=flags)
        threading.Thread(target=self._pump, name="simulator-output", daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            line = line.rstrip()
            if line:
                self.tail.append(line.strip())
                self.on_line(line)

    def wait_ready(self, timeout=FIRST_START_S):
        """True once its page answers; False if it exits or takes longer than ``timeout``."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                return False
            if probe(self.port):
                return True
            time.sleep(0.5)
        return False

    def why_not(self):
        """The simulator's own last words, for a start that failed."""
        if self.proc and self.proc.poll() is not None:
            return next((line for line in reversed(self.tail) if line), f"It exited with code {self.proc.returncode}.")
        return "It didn't answer in time."

    def stop(self, timeout=5.0):
        if not self.proc or self.proc.poll() is not None:
            return
        if os.name == "nt":
            # start.py runs the service as a child process on Windows: stop the whole tree.
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            self.proc.terminate()
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
