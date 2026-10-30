"""The waypoint route: the list sent to the boat and its depth along each leg.

WaypointStore is the single source of truth for both ground-station UIs. The
desktop panel and map (waypoints.py) and the web UI (station.py) subscribe to
changes via add_listener(). Nothing here imports a UI toolkit.
"""

import itertools
import json
import math


# Status of a single waypoint.
STATUS_IDLE    = "idle"     # white  - just in the list
STATUS_NEXT    = "next"     # yellow - next in line
STATUS_ACTIVE  = "active"   # green  - currently active
STATUS_SKIPPED = "skipped"  # red    - skipped by the boat


# Sync state of the whole list (drives the banner).
SYNC_SYNCED      = "synced"
SYNC_UNSYNCED    = "unsynced"
SYNC_EMPTY       = "empty"
SYNC_SKIPPED_HIT = "skipped_hit"


# Route-depth safety thresholds.  These are deliberately module-level so the
# map probe and the route renderer use exactly the same values.
BOAT_DRAFT_M = 2.0
SAFE_DEPTH_M = 2.5
ROUTE_DEPTH_SAMPLE_M = 1.0          # route is depth-tested about every 1 m

# Depth class of one stretch of route.
DEPTH_DEEP    = "deep"      # estimated depth >= SAFE_DEPTH_M
DEPTH_SHALLOW = "shallow"   # BOAT_DRAFT_M .. SAFE_DEPTH_M
DEPTH_UNDER   = "under"     # estimated depth < BOAT_DRAFT_M
DEPTH_UNKNOWN = "unknown"   # depth layer active but no estimate here


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #

class WaypointStore:
    """Holds the waypoint list and notifies listeners on every change.

    Keeps a snapshot of the last list sent to the boat so it can tell when
    the user has unsent changes.
    """

    def __init__(self):
        self._items = []        # list of dicts: id, name, lat, lon, status
        self._synced_snapshot = []  # last list sent to the boat
        self._listeners = []
        self._id_counter = itertools.count(1)
        self._last_skipped_name = None

    # ----- listeners --------------------------------------------------- #
    def add_listener(self, fn):
        """Register a callback fn() to be called on every change."""
        self._listeners.append(fn)

    def _notify(self, structural=True):
        for fn in self._listeners:
            try:
                fn(structural)
            except TypeError:
                # Listener accepts no args
                try:
                    fn()
                except Exception:
                    pass
            except Exception:
                pass

    # ----- queries ----------------------------------------------------- #
    def items(self):
        """Return a copy of the list (callers shouldn't mutate it directly)."""
        return list(self._items)

    def count(self):
        return len(self._items)

    def sync_state(self):
        """Return (sync_state, detail) for the banner."""
        if self._last_skipped_name is not None:
            return SYNC_SKIPPED_HIT, self._last_skipped_name
        if not self._items:
            return SYNC_EMPTY, None
        if self._content_equals_snapshot():
            # In sync. Surface active waypoint if any.
            for w in self._items:
                if w["status"] == STATUS_ACTIVE:
                    return SYNC_SYNCED, w["name"]
            return SYNC_SYNCED, None
        return SYNC_UNSYNCED, None

    def _content_equals_snapshot(self):
        """Compare current list to last-sent snapshot, ignoring status."""
        if len(self._items) != len(self._synced_snapshot):
            return False
        for a, b in zip(self._items, self._synced_snapshot):
            for k in ("id", "name", "lat", "lon"):
                if a[k] != b[k]:
                    return False
        return True

    def sent_count(self):
        """How many waypoints the last list marked as sent held."""
        return len(self._synced_snapshot)

    def changes(self):
        """What changed since the last list sent, ignoring status.

        Returns ({wp_id: "new" | "renamed" | "moved"}, removed_count,
        reordered) where reordered means the waypoints both lists share are
        in a different order.
        """
        sent = {w["id"]: w for w in self._synced_snapshot}
        changed = {}
        for w in self._items:
            old = sent.get(w["id"])
            if old is None:
                changed[w["id"]] = "new"
            elif old["name"] != w["name"]:
                changed[w["id"]] = "renamed"
            elif old["lat"] != w["lat"] or old["lon"] != w["lon"]:
                changed[w["id"]] = "moved"
        current_ids = [w["id"] for w in self._items if w["id"] in sent]
        removed = sum(1 for wid in sent if wid not in current_ids)
        sent_order = [w["id"] for w in self._synced_snapshot if w["id"] in current_ids]
        return changed, removed, current_ids != sent_order

    # ----- mutations --------------------------------------------------- #
    def add(self, lat, lon, name=None):
        wp = {
            "id":     next(self._id_counter),
            "name":   name or f"WP {len(self._items) + 1}",
            "lat":    float(lat),
            "lon":    float(lon),
            "status": STATUS_IDLE,
        }
        self._items.append(wp)
        self._notify(structural=True)
        return wp

    def remove(self, wp_id):
        self._items = [w for w in self._items if w["id"] != wp_id]
        self._notify(structural=True)

    def rename(self, wp_id, new_name):
        for w in self._items:
            if w["id"] == wp_id:
                if w["name"] == new_name:
                    return
                w["name"] = new_name
                self._notify(structural=True)
                return

    def update_position(self, wp_id, lat, lon):
        """Move a waypoint to new coordinates."""
        for w in self._items:
            if w["id"] == wp_id:
                w["lat"] = float(lat)
                w["lon"] = float(lon)
                self._notify(structural=True)
                return

    def move(self, from_index, to_index):
        """Move item at from_index to to_index. Clamps to valid range."""
        if from_index == to_index:
            return
        n = len(self._items)
        if not (0 <= from_index < n):
            return
        to_index = max(0, min(n - 1, to_index))
        item = self._items.pop(from_index)
        self._items.insert(to_index, item)
        self._notify(structural=True)

    def clear(self):
        self._items = []
        self._last_skipped_name = None
        self._notify(structural=True)

    def restore_synced(self):
        """Replace current list with the last-sent snapshot."""
        self._items = [dict(w) for w in self._synced_snapshot]
        self._last_skipped_name = None
        self._notify(structural=True)

    def mark_synced(self):
        """Snapshot the current list as the version sent to the boat."""
        self._synced_snapshot = [dict(w) for w in self._items]
        self._last_skipped_name = None
        self._notify(structural=False)

    def set_status(self, wp_id, status):
        for w in self._items:
            if w["id"] == wp_id:
                w["status"] = status
                if status == STATUS_SKIPPED:
                    self._last_skipped_name = w["name"]
                self._notify(structural=False)
                return

    def clear_skipped_alert(self):
        self._last_skipped_name = None
        self._notify(structural=False)

    # ----- export ------------------------------------------------------ #
    def to_json(self):
        """Build the JSON payload to send to the boat."""
        payload = {
            "waypoints": [
                {"id": w["id"], "name": w["name"],
                 "lat": w["lat"], "lon": w["lon"]}
                for w in self._items
            ]
        }
        return json.dumps(payload)


# --------------------------------------------------------------------------- #
# Depth along the route
# --------------------------------------------------------------------------- #

def distance_m(a, b):
    """Great-circle distance between two (lat, lon) points in metres."""
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = (math.sin(dp / 2.0) ** 2 +
         math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2)
    return 2.0 * r * math.asin(min(1.0, math.sqrt(h)))


def lerp_position(a, b, t):
    """Linear lat/lon interpolation, sufficient over individual lake legs."""
    return (a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t)


def depth_class(depth_m):
    if depth_m < BOAT_DRAFT_M:
        return DEPTH_UNDER
    if depth_m < SAFE_DEPTH_M:
        return DEPTH_SHALLOW
    return DEPTH_DEEP


def _leg_samples(a, b, depth_at):
    """Yield (p0, p1, depth_class, depth_m or None) about every metre of a leg.

    ``depth_at(lat, lon)`` returns a LakeDepthRaster.depth_at() dict or None.
    With no depth layer (``depth_at`` is None) every stretch counts as deep,
    as the route is then drawn in a single colour.
    """
    leg_m = distance_m(a, b)
    pieces = max(1, int(math.ceil(leg_m / ROUTE_DEPTH_SAMPLE_M)))
    for i in range(pieces):
        p0 = lerp_position(a, b, i / pieces)
        p1 = lerp_position(a, b, (i + 1) / pieces)
        if depth_at is None:
            yield p0, p1, DEPTH_DEEP, None
            continue
        try:
            estimate = depth_at(*lerp_position(p0, p1, 0.5))
        except Exception:
            estimate = None
        if estimate is None:
            yield p0, p1, DEPTH_UNKNOWN, None
        else:
            depth = float(estimate["depth_m"])
            yield p0, p1, depth_class(depth), depth


def _leg_ends(wp_a, wp_b):
    return ((float(wp_a["lat"]), float(wp_a["lon"])),
            (float(wp_b["lat"]), float(wp_b["lon"])))


def route_depth_runs(items, depth_at=None):
    """Return [(depth_class, [coords...]), ...] for the complete route.

    Neighbouring stretches of the same class are merged into one run, across
    legs too, so a map can draw each run as one path.
    """
    runs = []
    current_class = None
    current_coords = []

    def append_segment(p0, p1, cls):
        nonlocal current_class, current_coords
        if current_class == cls and current_coords:
            if current_coords[-1] != p0:
                current_coords.append(p0)
            current_coords.append(p1)
            return
        if current_coords and len(current_coords) >= 2:
            runs.append((current_class, current_coords))
        current_class = cls
        current_coords = [p0, p1]

    for wp_a, wp_b in zip(items, items[1:]):
        a, b = _leg_ends(wp_a, wp_b)
        for p0, p1, cls, _ in _leg_samples(a, b, depth_at):
            append_segment(p0, p1, cls)

    if current_coords and len(current_coords) >= 2:
        runs.append((current_class, current_coords))
    return runs


def leg_depth_profile(wp_a, wp_b, depth_at=None):
    """Summarise one leg for a profile bar.

    Returns {"length_m", "segments": [(depth_class, fraction), ...],
    "min_depth_m" (or None), "metres": {depth_class: metres}} with the
    segments in order from wp_a to wp_b and their fractions summing to 1.
    """
    a, b = _leg_ends(wp_a, wp_b)
    length = distance_m(a, b)
    samples = list(_leg_samples(a, b, depth_at))
    step = 1.0 / len(samples)
    segments = []
    metres = {}
    shallowest = None
    for _, _, cls, depth in samples:
        if segments and segments[-1][0] == cls:
            segments[-1][1] += step
        else:
            segments.append([cls, step])
        metres[cls] = metres.get(cls, 0.0) + length * step
        if depth is not None and (shallowest is None or depth < shallowest):
            shallowest = depth
    return {
        "length_m": length,
        "segments": [(c, f) for c, f in segments],
        "min_depth_m": shallowest,
        "metres": metres,
    }
