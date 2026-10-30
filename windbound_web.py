"""Windbound: the ground station's web UI (the second way to run it).

Starts the ground-station core (station.py) and a local web server
(web_server.py), then opens the page. It does the same job as
sailboat_monitor.py with a different interface; run either one.

Run:
    pip install -r requirements.txt
    python windbound_web.py

Options:
    --port N           first port to try (default 8765; the next free one is used)
    --connect PORT     open this port at start: COM5, /dev/tty.usbmodem1103,
                       or a pyserial URL such as socket://127.0.0.1:5760
                       (the boat simulator)
    --baud N           baud for --connect (default 115200)
    --browser          open a normal browser tab instead of an app window
    --no-open          don't open anything; just print the address
"""

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import webbrowser

from station import BAUD_RATES, DEFAULT_BAUD, Station
from web_server import DEFAULT_PORT, WebServer


def _app_browser():
    """A Chromium browser that can open the page as a chromeless app window."""
    candidates = []
    if sys.platform == "win32":
        for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"),
                     os.environ.get("LOCALAPPDATA")):
            if not base:
                continue
            candidates += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                           os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    elif sys.platform == "darwin":
        candidates += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                       "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                       "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    else:
        candidates += [shutil.which(n) for n in
                       ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")]
    return next((c for c in candidates if c and os.path.isfile(c)), None)


def open_page(url, app_window=True):
    browser = _app_browser() if app_window else None
    if browser:
        try:
            subprocess.Popen([browser, f"--app={url}", "--window-size=1440,900"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "app window"
        except OSError:
            pass
    webbrowser.open(url)
    return "browser"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Windbound ground station, web UI")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--connect", metavar="PORT")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD,
                        choices=[int(b) for b in BAUD_RATES])
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)

    print("Windbound: loading lake depth grids...", flush=True)
    station = Station()
    station.start()
    server = WebServer(station, port=args.port)

    if args.connect:
        ok, msg = station.connect(args.connect, args.baud)
        print(("" if ok else "Warning: ") + msg, flush=True)

    print(f"Windbound is running at {server.url}", flush=True)
    if not args.no_open:
        how = open_page(server.url, app_window=not args.browser)
        print(f"Opened it in a {how}. Press Ctrl+C here to stop.", flush=True)
    else:
        print("Press Ctrl+C here to stop.", flush=True)

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        while not stop.wait(0.5):
            pass
    finally:
        print("Stopping: closing the port and any recording...", flush=True)
        server.close()
        station.close()


if __name__ == "__main__":
    main()
