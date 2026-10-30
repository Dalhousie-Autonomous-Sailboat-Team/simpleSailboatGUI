"""Waypoint list shared between the main window panel and the map view.

WaypointStore is the single source of truth. The panel and the map both
subscribe to changes via add_listener() and rebuild their views when
notified.
"""


import tkinter as tk

import customtkinter as ctk

# The store, its status constants and the route-depth rules live in route.py
# so the web UI can share them; they are re-exported here for existing callers.
from route import (  # noqa: F401
    STATUS_IDLE, STATUS_NEXT, STATUS_ACTIVE, STATUS_SKIPPED,
    SYNC_SYNCED, SYNC_UNSYNCED, SYNC_EMPTY, SYNC_SKIPPED_HIT,
    WaypointStore,
    BOAT_DRAFT_M, SAFE_DEPTH_M, ROUTE_DEPTH_SAMPLE_M,
    DEPTH_DEEP, DEPTH_SHALLOW, DEPTH_UNDER, DEPTH_UNKNOWN,
    route_depth_runs,
)


# --------------------------------------------------------------------------- #
# Panel
# --------------------------------------------------------------------------- #

# Status colors for the indicator dot.
_DOT_COLORS = {
    STATUS_IDLE:    "#ffffff",
    STATUS_NEXT:    "#e8c43d",
    STATUS_ACTIVE:  "#4caf72",
    STATUS_SKIPPED: "#d05b5b",
}


class WaypointPanel(ctk.CTkFrame):
    """Side panel with status banner, scrollable list, and action buttons."""

    def __init__(self, master, store, on_send, on_log, **kwargs):
        super().__init__(master, corner_radius=10, **kwargs)
        self.store    = store
        self.on_send  = on_send   # callback when "Send to Boat" is pressed
        self.on_log   = on_log    # callback for log messages

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_banner()
        self._build_list()
        self._build_buttons()

        # Drag state
        self._drag_from = None
        self._drag_widget = None

        store.add_listener(self.refresh)
        self.refresh()

    # ----- layout ------------------------------------------------------ #
    def _build_header(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Waypoints",
                     font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, sticky="w")
        self.count_label = ctk.CTkLabel(head, text="0",
                                        text_color=("gray45", "gray60"),
                                        font=ctk.CTkFont(size=11))
        self.count_label.grid(row=0, column=1, sticky="e")

    def _build_banner(self):
        self.banner = ctk.CTkLabel(
            self, text="No Waypoints", anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#e0e4ec", "#2a2e3a"), corner_radius=6,
            text_color=("#283040", "#c8ccdc"))
        self.banner.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8),
                         ipady=6)

    def _build_list(self):
        self.list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=0)
        self.list_frame.grid_columnconfigure(0, weight=1)
        self._row_widgets = []   # parallel to store.items()

    def _build_buttons(self):
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=3, column=0, sticky="ew", padx=12, pady=10)
        btns.grid_columnconfigure((0, 1, 2), weight=1)
        self.send_btn = ctk.CTkButton(
            btns, text="Send to Boat", command=self._on_send,
            fg_color="#3b8ed0", hover_color="#2f7ab5")
        self.send_btn.grid(row=0, column=0, padx=2, sticky="ew")
        ctk.CTkButton(
            btns, text="Restore", command=self._on_restore,
            fg_color="transparent", border_width=1,
            text_color=("gray20", "gray80")).grid(
            row=0, column=1, padx=2, sticky="ew")
        ctk.CTkButton(
            btns, text="Clear All", command=self._on_clear,
            fg_color="#c0552e", hover_color="#a8482a").grid(
            row=0, column=2, padx=2, sticky="ew")

    # ----- refresh ----------------------------------------------------- #
    def refresh(self, structural=True):
        """Rebuild from the store. structural=True rebuilds the list rows;
        structural=False only updates dots and banner so in-progress edits
        aren't disturbed.
        """
        self._refresh_banner()
        if structural:
            self._refresh_list()
        else:
            self._refresh_status_only()

    def _refresh_banner(self):
        state, detail = self.store.sync_state()
        n = self.store.count()
        self.count_label.configure(text=f"{n}" if n else "0")

        if state == SYNC_SKIPPED_HIT:
            text  = f"Waypoint {detail} Skipped!"
            color = ("#d05b5b", "#d05b5b")
        elif state == SYNC_UNSYNCED:
            text  = "ALERT: New Changes not Transmitted"
            color = ("#d0a23b", "#d0a23b")
        elif state == SYNC_EMPTY:
            text  = "No Waypoints"
            color = ("#283040", "#c8ccdc")
        elif state == SYNC_SYNCED and detail:
            text  = f"Active Waypoint is {detail}"
            color = ("#4caf72", "#4caf72")
        else:
            text  = "Synced to Boat"
            color = ("#4caf72", "#4caf72")

        self.banner.configure(text=text, text_color=color)

    def _refresh_list(self):
        # Tear down old rows
        for w in self._row_widgets:
            w.destroy()
        self._row_widgets = []

        for i, wp in enumerate(self.store.items()):
            row = self._make_row(i, wp)
            row.grid(row=i, column=0, sticky="ew", padx=4, pady=2)
            self._row_widgets.append(row)

    def _refresh_status_only(self):
        """Update dot colors in place without rebuilding rows."""
        items = self.store.items()
        # If lengths mismatch (e.g. listener fired out of order), fall back.
        if len(items) != len(self._row_widgets):
            self._refresh_list()
            return
        for wp, row in zip(items, self._row_widgets):
            try:
                row._dot.itemconfigure(
                    "dot", fill=_DOT_COLORS.get(wp["status"], "#ffffff"))
            except Exception:
                pass

    # ----- row construction ------------------------------------------- #
    def _make_row(self, index, wp):
        row = ctk.CTkFrame(self.list_frame, corner_radius=6,
                           fg_color=("#e6e6ec", "#26262e"))
        row.grid_columnconfigure(2, weight=1)
        row._wp_id = wp["id"]
        row._index = index

        # Indicator dot (canvas circle)
        dot = tk.Canvas(row, width=14, height=14, highlightthickness=0,
                        bd=0, bg=self._row_bg())
        dot.create_oval(2, 2, 12, 12,
                        fill=_DOT_COLORS.get(wp["status"], "#ffffff"),
                        outline="#555", tags="dot")
        dot.grid(row=0, column=0, padx=(8, 4), pady=8)
        row._dot = dot

        # Drag handle
        handle = ctk.CTkLabel(row, text="\u2630", width=18,
                              font=ctk.CTkFont(size=14),
                              text_color=("gray45", "gray60"))
        handle.grid(row=0, column=1, padx=(0, 6))
        handle.configure(cursor="fleur")
        handle.bind("<ButtonPress-1>",   lambda e, r=row: self._drag_start(e, r))
        handle.bind("<B1-Motion>",       lambda e, r=row: self._drag_motion(e, r))
        handle.bind("<ButtonRelease-1>", lambda e, r=row: self._drag_end(e, r))

        # Editable name + coords
        info = ctk.CTkFrame(row, fg_color="transparent")
        info.grid(row=0, column=2, sticky="ew", padx=2, pady=4)
        info.grid_columnconfigure(0, weight=1)

        name_var = tk.StringVar(value=wp["name"])
        name_entry = ctk.CTkEntry(info, textvariable=name_var,
                                  font=ctk.CTkFont(size=12, weight="bold"),
                                  height=24, border_width=0,
                                  fg_color="transparent")
        name_entry.grid(row=0, column=0, sticky="ew")
        name_entry.bind("<FocusOut>",
                        lambda e, wid=wp["id"], v=name_var:
                            self.store.rename(wid, v.get().strip() or "WP"))
        name_entry.bind("<Return>", lambda e: self.focus())

        coords = ctk.CTkLabel(
            info, text=f"{wp['lat']:.5f}, {wp['lon']:.5f}",
            font=ctk.CTkFont(size=10),
            text_color=("gray45", "gray60"), anchor="w")
        coords.grid(row=1, column=0, sticky="w")

        # Delete button
        ctk.CTkButton(row, text="\u2715", width=24, height=24,
                      fg_color="transparent",
                      hover_color=("gray75", "gray30"),
                      text_color=("gray45", "gray60"),
                      command=lambda wid=wp["id"]: self.store.remove(wid)).grid(
            row=0, column=3, padx=(0, 6))
        return row

    def _row_bg(self):
        return "#26262e" if ctk.get_appearance_mode() == "Dark" else "#e6e6ec"

    # ----- drag-to-reorder -------------------------------------------- #
    def _drag_start(self, event, row):
        self._drag_from = row._index

    def _drag_motion(self, event, row):
        if self._drag_from is None:
            return
        # Convert event y to a y in the scrollable frame's coordinate space.
        y_root = event.y_root
        # Find which row currently sits under the cursor.
        for r in self._row_widgets:
            top = r.winfo_rooty()
            bot = top + r.winfo_height()
            if top <= y_root <= bot:
                target = r._index
                if target != self._drag_from:
                    self.store.move(self._drag_from, target)
                    self._drag_from = target
                return

    def _drag_end(self, event, row):
        self._drag_from = None

    # ----- button handlers -------------------------------------------- #
    def _on_send(self):
        if self.store.count() == 0:
            self.on_log("** No waypoints to send. **")
            return
        payload = self.store.to_json()
        self.on_send(payload)
        self.store.mark_synced()
        self.on_log(f"** Sent {self.store.count()} waypoints to boat. **")

    def _on_restore(self):
        self.store.restore_synced()
        self.on_log("** Restored last synced waypoint list. **")

    def _on_clear(self):
        self.store.clear()
        self.on_log("** Cleared all waypoints. **")


# --------------------------------------------------------------------------- #
# Map integration
# --------------------------------------------------------------------------- #

ROUTE_SAFE_COLOR = "#3b8ed0"       # blue: estimated depth >= SAFE_DEPTH_M
ROUTE_CAUTION_COLOR = "#e8c43d"    # yellow: BOAT_DRAFT_M .. SAFE_DEPTH_M
ROUTE_DANGER_COLOR = "#d05b5b"     # red: estimated depth < BOAT_DRAFT_M
ROUTE_UNKNOWN_COLOR = "#7f8c8d"    # grey: bathymetry enabled, no estimate
ROUTE_LINE_WIDTH = 5

_ROUTE_COLOURS = {
    DEPTH_DEEP:    ROUTE_SAFE_COLOR,
    DEPTH_SHALLOW: ROUTE_CAUTION_COLOR,
    DEPTH_UNDER:   ROUTE_DANGER_COLOR,
    DEPTH_UNKNOWN: ROUTE_UNKNOWN_COLOR,
}


class WaypointMapLayer:
    """Draw waypoints and a depth-aware route on a TkinterMapView map.

    When a lake-depth layer is active, each waypoint leg is sampled at roughly
    ``ROUTE_DEPTH_SAMPLE_M`` spacing.  Only the local part of the route that is
    shallow changes colour:

        blue   >= SAFE_DEPTH_M
        yellow BOAT_DRAFT_M .. SAFE_DEPTH_M
        red    < BOAT_DRAFT_M
        grey   no usable depth estimate

    This means one route can naturally transition blue -> yellow -> red ->
    yellow -> blue as it crosses a shoal.  The bathymetry layer supplies the
    approximate numeric depth; this class only handles route sampling and
    safety colouring.

    Right-click "Adjust nearest waypoint" enters move mode.  The next
    right-click "Place waypoint here" repositions the selected waypoint.
    """

    _SNAP_THRESHOLD_DEG = 0.01  # ~1 km at mid-latitudes

    def __init__(self, mapview, store, on_log=None):
        self.mapview = mapview
        self.store = store
        self.on_log = on_log or (lambda s: None)
        self._markers = []
        self._paths = []

        # Re-use expensive depth sampling when only waypoint status colours
        # changed.  Structural waypoint edits or a different bathymetry layer
        # naturally produce a different signature.
        self._route_cache_signature = None
        self._route_cache_runs = []

        # Adjust mode state
        self._adjusting_id = None
        self._adjusting_name = None
        self._adjust_label = None

        try:
            self.mapview.add_right_click_menu_command(
                label="Adjust nearest waypoint",
                command=self._start_adjust,
                pass_coords=True)
            self.mapview.add_right_click_menu_command(
                label="Place waypoint here",
                command=self._place_adjust,
                pass_coords=True)
        except Exception:
            pass

        store.add_listener(self.refresh)
        self.refresh()

    # ----- drawing ----------------------------------------------------- #
    def refresh(self, structural=True):
        """Rebuild waypoint markers and the depth-coloured route."""
        for marker in self._markers:
            try:
                marker.delete()
            except Exception:
                pass
        self._markers = []

        for path in self._paths:
            try:
                path.delete()
            except Exception:
                pass
        self._paths = []

        items = self.store.items()
        if not items:
            return

        for i, wp in enumerate(items, start=1):
            try:
                marker = self.mapview.set_marker(
                    wp["lat"], wp["lon"], text=f"{i}. {wp['name']}",
                    marker_color_circle=_DOT_COLORS.get(
                        wp["status"], "#ffffff"),
                    marker_color_outside="#444")
                self._markers.append(marker)
            except Exception:
                pass

        if len(items) >= 2:
            self._draw_depth_route(items)

    def _depth_estimate(self, lat, lon):
        provider = getattr(self.mapview, "depth_estimate_at", None)
        if provider is None:
            return None
        try:
            return provider(lat, lon)
        except Exception:
            return None

    def _route_signature(self, items):
        coords = tuple((round(w["lat"], 8), round(w["lon"], 8)) for w in items)
        layer = getattr(self.mapview, "depth_layer", None)
        layer_id = getattr(layer, "cache_id", None) if layer is not None else None
        return coords, layer_id

    def _build_route_runs(self, items):
        """Return [(colour, [coords...]), ...] for the complete route."""
        depth_active = getattr(self.mapview, "depth_layer", None) is not None
        depth_at = self._depth_estimate if depth_active else None
        return [(_ROUTE_COLOURS[cls], coords)
                for cls, coords in route_depth_runs(items, depth_at)]

    def _draw_depth_route(self, items):
        signature = self._route_signature(items)
        if signature == self._route_cache_signature:
            runs = self._route_cache_runs
        else:
            runs = self._build_route_runs(items)
            self._route_cache_signature = signature
            self._route_cache_runs = runs

        for colour, coords in runs:
            try:
                path = self.mapview.set_path(
                    coords, color=colour, width=ROUTE_LINE_WIDTH)
                self._paths.append(path)
            except Exception:
                pass

    # ----- adjust mode ------------------------------------------------- #
    def _find_nearest(self, lat, lon):
        """Return the store item nearest to (lat, lon), or None."""
        best, best_dist = None, float("inf")
        for wp in self.store.items():
            d = (wp["lat"] - lat) ** 2 + (wp["lon"] - lon) ** 2
            if d < best_dist:
                best, best_dist = wp, d
        if best is not None and best_dist ** 0.5 < self._SNAP_THRESHOLD_DEG:
            return best
        return None

    def _start_adjust(self, coords):
        """Right-click -> 'Adjust nearest waypoint': select the closest one."""
        if self._adjusting_id is not None:
            self._exit_adjust()

        lat, lon = coords
        nearest = self._find_nearest(lat, lon)
        if nearest is None:
            self.on_log("** No waypoint close enough to adjust. "
                        "Right-click nearer to a marker. **")
            return

        self._adjusting_id = nearest["id"]
        self._adjusting_name = nearest["name"]

        try:
            import customtkinter as ctk
            self._adjust_label = ctk.CTkLabel(
                self.mapview,
                text=f"  Adjusting {self._adjusting_name}  -  "
                     f"right-click -> 'Place waypoint here'  ",
                fg_color="#d0a23b", text_color="#1a1a1a", corner_radius=6,
                font=ctk.CTkFont(size=13, weight="bold"))
            self._adjust_label.place(relx=0.5, y=10, anchor="n")
        except Exception:
            pass

        self.on_log(f"** Selected {self._adjusting_name} for adjustment. "
                    f"Right-click the new position -> 'Place waypoint here'. **")

    def _place_adjust(self, coords):
        """Right-click -> 'Place waypoint here': move the selected waypoint."""
        if self._adjusting_id is None:
            self.on_log("** No waypoint selected for adjustment. "
                        "Use 'Adjust nearest waypoint' first. **")
            return

        lat, lon = coords
        name = self._adjusting_name
        self.store.update_position(self._adjusting_id, lat, lon)
        self.on_log(f"** Moved {name} to {lat:.5f}, {lon:.5f} **")
        self._exit_adjust()

    def _exit_adjust(self):
        """Clear adjust mode state and remove the overlay prompt."""
        self._adjusting_id = None
        self._adjusting_name = None
        if self._adjust_label is not None:
            try:
                self._adjust_label.destroy()
            except Exception:
                pass
            self._adjust_label = None
