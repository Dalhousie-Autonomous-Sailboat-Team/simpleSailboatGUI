"""Write telemetry to CSV and export the GPS track as GPX.

Recorder owns the file handles and row count. It does no UI work: callers
pass data in and use the returned status strings to update widgets.
"""

import csv
import math
import os
from datetime import datetime

from parsing import TELEMETRY_FIELDS


class Recorder:
    def __init__(self, log_dir="logs"):
        self.log_dir = log_dir
        self.file = None
        self.writer = None
        self.rows = 0
        self.path = None

    @property
    def active(self):
        return self.file is not None

    def start(self):
        """Open a new CSV. Return (ok, message)."""
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.path = os.path.join(self.log_dir, f"telemetry_{stamp}.csv")
            self.file = open(self.path, "w", newline="")
        except OSError as e:
            self.file = None
            return False, f"Could not start recording: {e}"
        self.writer = csv.writer(self.file)
        self.writer.writerow(["timestamp"] + [k for k, _, _ in TELEMETRY_FIELDS])
        self.rows = 0
        return True, f"Recording to {self.path}"

    def stop(self):
        """Close the CSV. Return a status message."""
        if self.file is not None:
            try:
                self.file.close()
            except Exception:
                pass
        msg = f"Recording stopped: {self.path} ({self.rows} rows)"
        self.file = None
        self.writer = None
        return msg

    def write_row(self, d):
        """Append one telemetry sample. No-op if not recording."""
        if self.writer is None:
            return
        row = [datetime.now().isoformat(timespec="milliseconds")]
        row += [d.get(k, "") for k, _, _ in TELEMETRY_FIELDS]
        try:
            self.writer.writerow(row)
            self.rows += 1
        except Exception:
            pass

    def status_text(self):
        """Short label for the recording state."""
        if self.active:
            return f"\u25cf REC  {self.rows} rows"
        return "not recording"

    def export_gpx(self, gps_track):
        """Write the GPS track to a GPX file. Return (ok, message)."""
        if not gps_track:
            return False, "No GPS points to export yet."
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(self.log_dir, f"track_{stamp}.gpx")
            _write_gpx(path, ((lat, lon, t.isoformat()) for lat, lon, t in gps_track))
            return True, f"Exported {len(gps_track)} points to {path}"
        except OSError as e:
            return False, f"GPX export failed: {e}"


def _write_gpx(path, points):
    """Write (lat, lon, ISO time) points to a GPX 1.1 track."""
    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<gpx version="1.1" creator="Sailboat Ground Station '
                'Monitor" xmlns="http://www.topografix.com/GPX/1/1">\n')
        f.write(' <trk><name>Sailboat track</name><trkseg>\n')
        for lat, lon, t in points:
            f.write(f'  <trkpt lat="{lat:.7f}" lon="{lon:.7f}">'
                    f'<time>{t}</time></trkpt>\n')
        f.write(' </trkseg></trk>\n</gpx>\n')


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_track(csv_path):
    """Return [(lat, lon, ISO time)] of the usable GPS fixes in a recording.

    Skips empty, 0,0 (no fix) and whole-degree positions (firmware that
    truncates clat/clon with %d), and consecutive duplicates.
    """
    track = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            lat, lon = _float(row.get("clat")), _float(row.get("clon"))
            if lat is None or lon is None:
                continue
            if abs(lat) <= 1e-4 and abs(lon) <= 1e-4:
                continue
            if lat.is_integer() and lon.is_integer():
                continue
            if track and track[-1][:2] == (lat, lon):
                continue
            track.append((lat, lon, row.get("timestamp", "")))
    return track


def _haversine_m(a, b):
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def summarize_csv(csv_path, max_points=40):
    """Rows, time span, track length and a thumbnail of one recording."""
    rows = 0
    first = last = None
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rows += 1
                stamp = row.get("timestamp")
                if stamp:
                    first = first or stamp
                    last = stamp
        track = read_track(csv_path)
    except (OSError, csv.Error, UnicodeDecodeError) as e:
        return {"rows": None, "error": str(e)}
    duration = None
    try:
        if first and last:
            duration = (datetime.fromisoformat(last)
                        - datetime.fromisoformat(first)).total_seconds()
    except ValueError:
        pass
    length = sum(_haversine_m(a, b) for a, b in zip(track, track[1:]))
    step = max(1, len(track) // max_points)
    thumb = [[lat, lon] for lat, lon, _ in track[::step]]
    if track and (not thumb or thumb[-1] != [track[-1][0], track[-1][1]]):
        thumb.append([track[-1][0], track[-1][1]])
    box = None
    if track:
        lats = [p[0] for p in track]
        lons = [p[1] for p in track]
        box = [min(lats), max(lats), min(lons), max(lons)]
    return {"rows": rows, "first": first, "last": last, "duration_s": duration,
            "fixes": len(track), "track_m": length, "thumb": thumb, "box": box}


def export_gpx_from_csv(csv_path):
    """Write a recording's GPS track to a .gpx beside it.

    Return (ok, message, gpx_path or None).
    """
    try:
        track = read_track(csv_path)
    except (OSError, csv.Error, UnicodeDecodeError) as e:
        return False, f"Could not read {csv_path}: {e}", None
    if not track:
        return False, "This recording has no usable GPS positions.", None
    gpx_path = os.path.splitext(csv_path)[0] + ".gpx"
    try:
        _write_gpx(gpx_path, track)
    except OSError as e:
        return False, f"GPX export failed: {e}", None
    return True, f"Exported {len(track)} points to {gpx_path}", gpx_path
