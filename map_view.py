"""3D boat + GPS map window and its handlers.

Mixin class: methods live here but run as App methods via multiple
inheritance, so they still see self.append_log, self.waypoints, etc.

The map uses a small TkinterMapView subclass that fixes two important cache
limitations in tkintermapview 1.29:

1. Tiles fetched during normal interactive use are written to the SQLite
   cache, instead of only being cached in RAM.
2. Base-map and OpenSeaMap overlay tiles are cached separately. Toggling the
   nautical layer therefore recomposites cached tiles instead of downloading
   the base map again.

It also disables TkinterMapView's aggressive radius-8 background pre-cache.
Only tiles actually needed by the visible map are requested, which cuts a
large amount of unnecessary network traffic when panning/zooming.
"""

import io
import math
import os
import sqlite3
import threading
import time
import tkinter as tk

import customtkinter as ctk

from boat3d import Boat3DView
from waypoints import (
    WaypointMapLayer,
    BOAT_DRAFT_M, SAFE_DEPTH_M,
    ROUTE_SAFE_COLOR, ROUTE_CAUTION_COLOR, ROUTE_DANGER_COLOR,
    ROUTE_UNKNOWN_COLOR,
)
from lake_depth import (  # noqa: F401
    LAKE_DEPTH_DIR, LAKE_DEPTH_OFF, LAKE_DEPTH_DEFAULT_OPACITY,
    NS_LAKE_DEPTH_ATTRIBUTION, LAKE_DEPTH_BOUND_OVERRIDES,
    LakeDepthRaster, discover_lake_depth_layers,
)
from tile_cache import (  # noqa: F401
    MAP_CACHE_DIR, MAP_CACHE_DB, OPENSEAMAP_TILE_SERVER, OPENSEAMAP_MAX_ZOOM,
    VIEW_TILE_FALLBACK_TTL_S, MAP_USER_AGENT, TILE_SERVERS,
    TileCache, prepare_tile_cache_database,
)


# Map defaults
DEFAULT_MAP_LAT   = 44.6488     # Halifax, NS
DEFAULT_MAP_LON   = -63.5752
DEFAULT_MAP_ZOOM  = 12

# Common OpenSeaMap symbols the operator is most likely to encounter while
# setting waypoints on lakes or near shore. This is a compact, operator-focused
# legend rather than a complete IALA/ENC symbol catalogue.
NAUTICAL_LEGEND_ITEMS = [
    ("port", "Port lateral mark",
     "Usually red. Keep to port when entering from seaward / heading upstream."),
    ("starboard", "Starboard lateral mark",
     "Usually green. Keep to starboard when entering from seaward / heading upstream."),
    ("safe", "Safe water mark",
     "Mid-channel / fairway marker. Safe water all around."),
    ("isolated", "Isolated danger",
     "Danger with safe water all around; do not pass too close."),
    ("north", "North cardinal",
     "Pass north of the mark."),
    ("south", "South cardinal",
     "Pass south of the mark."),
    ("east", "East cardinal",
     "Pass east of the mark."),
    ("west", "West cardinal",
     "Pass west of the mark."),
    ("light", "Light / beacon",
     "Fixed or flashing light, beacon, or lighthouse aid to navigation."),
    ("marina", "Marina / harbour",
     "Marina, dock, harbour or water-access facility."),
    ("anchor", "Anchorage / mooring",
     "Anchorage area or mooring-related feature."),
    ("warn", "Restricted / caution",
     "Cable areas, restrictions, local cautionary or special-use marks."),
]


def _make_cached_mapview_class(tkintermapview):
    """Return a TkinterMapView subclass with persistent base/overlay caching."""
    from PIL import Image, ImageTk

    # Pillow 10+ removed Image.ANTIALIAS, while tkintermapview 1.29 still
    # references it in its stock overlay path. Keep the alias for any library
    # code that still reaches that path.
    if not hasattr(Image, "ANTIALIAS") and hasattr(Image, "Resampling"):
        Image.ANTIALIAS = Image.Resampling.LANCZOS

    class CachedOverlayMapView(tkintermapview.TkinterMapView):
        """TkinterMapView with a real disk cache for base and overlay tiles."""

        _MEMORY_TILE_LIMIT = 2000

        def __init__(self, *args, overlay_max_zoom=OPENSEAMAP_MAX_ZOOM, **kwargs):
            database_path = kwargs.get("database_path")
            if database_path:
                prepare_tile_cache_database(database_path)

            self.overlay_max_zoom = int(overlay_max_zoom)
            self.depth_layer = None
            self.depth_opacity = LAKE_DEPTH_DEFAULT_OPACITY
            self._tile_source = TileCache()
            self._memory_cache_lock = threading.Lock()
            # TkinterMapView's constructor briefly points at Berlin and queues
            # tiles before the caller can set the real initial position. Suppress
            # those startup network requests; the Halifax redraw below will queue
            # the tiles that are actually needed.
            self._initializing_map = True
            super().__init__(*args, **kwargs)
            self._initializing_map = False
            self.image_load_queue_tasks = []
            self.image_load_queue_results = []

        # ------------------------------------------------------------------ #
        # Cache identity / redraw
        # ------------------------------------------------------------------ #

        def _overlay_for_zoom(self, zoom):
            if self.overlay_tile_server is None:
                return None
            if int(zoom) > self.overlay_max_zoom:
                return None
            return self.overlay_tile_server

        def _image_cache_key(self, zoom, x, y, tile_server=None,
                             overlay_server="__CURRENT__",
                             depth_layer="__CURRENT__",
                             depth_opacity=None):
            base = self.tile_server if tile_server is None else tile_server
            if overlay_server == "__CURRENT__":
                overlay = self._overlay_for_zoom(zoom)
            else:
                overlay = overlay_server

            if depth_layer == "__CURRENT__":
                depth = self.depth_layer
            else:
                depth = depth_layer
            depth_id = depth.cache_id if depth is not None else None
            if depth_opacity is None:
                depth_opacity = self.depth_opacity

            return (base, overlay, depth_id, round(float(depth_opacity), 3),
                    int(zoom), int(x), int(y))

        def get_tile_image_from_cache(self, zoom, x, y):
            key = self._image_cache_key(zoom, x, y)
            with self._memory_cache_lock:
                return self.tile_image_cache.get(key, False)

        def _put_memory_tile(self, key, image_tk):
            with self._memory_cache_lock:
                self.tile_image_cache[key] = image_tk
                excess = len(self.tile_image_cache) - self._MEMORY_TILE_LIMIT
                if excess > 0:
                    # dicts preserve insertion order; discard the oldest images.
                    for old_key in list(self.tile_image_cache.keys())[:excess]:
                        self.tile_image_cache.pop(old_key, None)

        def _redraw_tiles_for_source_change(self):
            """Redraw visible tiles without throwing away other cache variants."""
            self.image_load_queue_tasks = []
            self.image_load_queue_results = []
            self.canvas.delete("tile")
            self.draw_initial_array()
            self.manage_z_order()

        def set_tile_server(self, tile_server: str, tile_size: int = 256,
                            max_zoom: int = 19):
            """Change base source but retain cached tiles from all sources."""
            changed = (tile_server != self.tile_server or
                       tile_size != self.tile_size or
                       max_zoom != self.max_zoom)
            self.max_zoom = max_zoom
            self.tile_size = tile_size
            self.min_zoom = math.ceil(
                math.log2(math.ceil(self.width / self.tile_size)))
            self.tile_server = tile_server
            if changed:
                self._redraw_tiles_for_source_change()

        def set_overlay_tile_server(self, overlay_server):
            """Toggle/change overlay without flushing base or overlay caches."""
            if overlay_server == self.overlay_tile_server:
                return
            self.overlay_tile_server = overlay_server
            self._redraw_tiles_for_source_change()

        def set_depth_layer(self, layer):
            """Enable/disable a local lake-depth raster without flushing caches."""
            if layer is self.depth_layer:
                return
            self.depth_layer = layer
            if layer is not None:
                self.depth_opacity = layer.default_opacity
            self._redraw_tiles_for_source_change()

        def set_depth_opacity(self, opacity):
            """Change lake-depth transparency and redraw from cached components."""
            opacity = max(0.05, min(1.0, float(opacity)))
            if abs(opacity - self.depth_opacity) < 0.001:
                return
            self.depth_opacity = opacity
            self._redraw_tiles_for_source_change()

        def depth_estimate_at(self, lat, lon):
            """Return the active lake raster's approximate numeric depth."""
            if self.depth_layer is None:
                return None
            return self.depth_layer.depth_at(lat, lon)

        # ------------------------------------------------------------------ #
        # Tile sources (network + SQLite live in tile_cache.TileCache)
        # ------------------------------------------------------------------ #

        @staticmethod
        def _decode_image(image_bytes):
            if image_bytes is None:
                return None
            with Image.open(io.BytesIO(image_bytes)) as img:
                img.load()
                return img.copy()

        def _load_source_image(self, server, zoom, x, y, db_cursor):
            """Return one source tile, preferring disk and refreshing if stale."""
            return self._tile_source.get(
                server, zoom, x, y, db_cursor, decode=self._decode_image,
                max_zoom=self.max_zoom)

        # ------------------------------------------------------------------ #
        # TkinterMapView image request override
        # ------------------------------------------------------------------ #

        def request_image(self, zoom: int, x: int, y: int, db_cursor=None):
            if getattr(self, "_initializing_map", False):
                return self.empty_tile_image

            # Capture sources at the start so a switch toggle halfway through a
            # network request cannot store the composite under the wrong key.
            base_server = self.tile_server
            overlay_server = self._overlay_for_zoom(zoom)
            depth_layer = self.depth_layer
            depth_opacity = self.depth_opacity
            cache_key = self._image_cache_key(
                zoom, x, y, tile_server=base_server,
                overlay_server=overlay_server, depth_layer=depth_layer,
                depth_opacity=depth_opacity)

            with self._memory_cache_lock:
                cached_photo = self.tile_image_cache.get(cache_key)
            if cached_photo is not None:
                return cached_photo

            base = self._load_source_image(
                base_server, zoom, x, y, db_cursor)
            if base is None:
                if self.use_database_only:
                    return self.empty_tile_image
                return self.empty_tile_image

            if overlay_server is not None:
                overlay = self._load_source_image(
                    overlay_server, zoom, x, y, db_cursor)
                if overlay is not None:
                    base = base.convert("RGBA")
                    overlay = overlay.convert("RGBA")
                    if overlay.size != (self.tile_size, self.tile_size):
                        if hasattr(Image, "Resampling"):
                            resample = Image.Resampling.LANCZOS
                        else:
                            resample = Image.LANCZOS
                        overlay = overlay.resize(
                            (self.tile_size, self.tile_size), resample)
                    base.alpha_composite(overlay, (0, 0))

            # Nova Scotia lake bathymetry is local and therefore never touches
            # the network. It is rendered directly into the current XYZ tile
            # after the base/seamarks and before Tkinter converts to PhotoImage.
            if depth_layer is not None:
                depth = depth_layer.render_tile(
                    zoom, x, y, self.tile_size, opacity=depth_opacity)
                if depth is not None:
                    base = base.convert("RGBA")
                    base.alpha_composite(depth, (0, 0))

            if not self.running:
                return self.empty_tile_image

            image_tk = ImageTk.PhotoImage(base)
            self._put_memory_tile(cache_key, image_tk)
            return image_tk

        def pre_cache(self):
            """Disable tkintermapview's radius-8 speculative downloader.

            The stock implementation can request hundreds of tiles around every
            new centre point. Visible tiles already load in the normal worker
            pool, and every viewed tile is now persisted to SQLite, so that
            aggressive look-ahead is unnecessary and substantially slows the
            nautical overlay.
            """
            while self.running:
                time.sleep(0.25)

    return CachedOverlayMapView


class MapViewMixin:
        # ----- 3D view + GPS map window --------------------------------------- #
        def open_3d_window(self):
            """Open (or focus) the pseudo-3D boat + GPS map window."""
            if self.win3d is not None and self.win3d.winfo_exists():
                self.win3d.focus()
                return

            try:
                import tkintermapview
                CachedOverlayMapView = _make_cached_mapview_class(tkintermapview)
            except ImportError:
                self.append_log("** 'tkintermapview' is not installed. Run: "
                                "pip install tkintermapview **")
                tkintermapview = None
                CachedOverlayMapView = None

            self.win3d = ctk.CTkToplevel(self)
            self.win3d.title("3D View & GPS Map")
            self.win3d.geometry("1100x600")
            self.win3d.grid_columnconfigure(0, weight=1)
            self.win3d.grid_rowconfigure(0, weight=1)
            self.win3d.protocol("WM_DELETE_WINDOW", self._close_3d_window)

            # PanedWindow gives a draggable sash between the two panels.
            # sashwidth=6 makes it easy to grab; sashcursor shows a resize cursor.
            paned = tk.PanedWindow(
                self.win3d, orient=tk.HORIZONTAL,
                sashwidth=6, sashpad=2, sashcursor="sb_h_double_arrow",
                sashrelief="flat",
                bg="#1c1c24" if ctk.get_appearance_mode() == "Dark" else "#dbdbdb",
                bd=0)
            paned.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

            # Left: pseudo-3D boat
            left = ctk.CTkFrame(paned, corner_radius=10)
            left.grid_columnconfigure(0, weight=1)
            left.grid_rowconfigure(1, weight=1)
            ctk.CTkLabel(left, text="Boat (3D)",
                         font=ctk.CTkFont(size=15, weight="bold")).grid(
                row=0, column=0, sticky="w", padx=14, pady=(12, 4))
            self.view3d = Boat3DView(left)
            self.view3d.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 12))
            paned.add(left, minsize=280, stretch="always")

            # Right: GPS map
            right = ctk.CTkFrame(paned, corner_radius=10)
            right.grid_columnconfigure(0, weight=1)
            right.grid_rowconfigure(2, weight=1)

            map_head = ctk.CTkFrame(right, fg_color="transparent")
            map_head.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
            map_head.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(map_head, text="Position (GPS)",
                         font=ctk.CTkFont(size=15, weight="bold")).grid(
                row=0, column=0, sticky="w")

            # Tile server picker — lighter servers load faster and use less bandwidth.
            self.tile_combo = ctk.CTkComboBox(
                map_head, values=list(TILE_SERVERS.keys()), width=160,
                command=self._change_tile_server)
            self.tile_combo.set("OpenStreetMap")
            self.tile_combo.grid(row=0, column=1, sticky="e", padx=(0, 8))

            # OpenSeaMap seamarks are a transparent XYZ overlay. Keep this as a
            # separate switch so the base map can still be changed independently.
            self.nautical_switch = ctk.CTkSwitch(
                map_head, text="Nautical", width=92,
                command=self._toggle_nautical_overlay)
            self.nautical_switch.select()
            self.nautical_switch.grid(row=0, column=2, sticky="e", padx=(0, 8))

            self.nautical_legend_btn = ctk.CTkButton(
                map_head, text="Legend", width=72, height=28,
                font=ctk.CTkFont(size=12),
                command=self._toggle_nautical_legend)
            self.nautical_legend_btn.grid(row=0, column=3, sticky="e", padx=(0, 8))

            # Refresh button — re-draws waypoint markers and lines. Useful if
            # the map state gets out of sync after rapid waypoint edits.
            self.map_refresh_btn = ctk.CTkButton(
                map_head, text="\u21bb", width=32,
                font=ctk.CTkFont(size=14, weight="bold"),
                command=self._refresh_map)
            self.map_refresh_btn.grid(row=0, column=4, sticky="e", padx=(0, 6))

            # Download tiles — grabs the visible area at current zoom ± 3
            # levels into the on-disk database so it loads offline next time.
            self.map_download_btn = ctk.CTkButton(
                map_head, text="\u2b07 Save Offline", width=110,
                font=ctk.CTkFont(size=12),
                command=self._download_visible_tiles)
            self.map_download_btn.grid(row=0, column=5, sticky="e")

            # Second row is reserved for environmental layers. Lake-depth
            # packages are local/offline and discovered each time this window
            # opens, so adding a new prepared lake does not require code edits.
            try:
                os.makedirs(LAKE_DEPTH_DIR, exist_ok=True)
            except OSError:
                pass
            self._lake_depth_layers = discover_lake_depth_layers()
            layer_bar = ctk.CTkFrame(right, fg_color="transparent")
            layer_bar.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 6))
            layer_bar.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(
                layer_bar, text="Lake depth:",
                font=ctk.CTkFont(size=11)).grid(
                    row=0, column=0, sticky="w", padx=(0, 6))

            depth_values = [LAKE_DEPTH_OFF] + list(self._lake_depth_layers.keys())
            self.depth_combo = ctk.CTkComboBox(
                layer_bar, values=depth_values, width=210,
                command=self._change_lake_depth_layer)
            self.depth_combo.set(LAKE_DEPTH_OFF)
            self.depth_combo.grid(row=0, column=1, sticky="w", padx=(0, 10))

            ctk.CTkLabel(
                layer_bar, text="Opacity",
                font=ctk.CTkFont(size=11)).grid(
                    row=0, column=2, sticky="e", padx=(0, 4))
            self.depth_opacity_slider = ctk.CTkSlider(
                layer_bar, from_=0.20, to=1.0, width=110,
                command=self._change_lake_depth_opacity)
            self.depth_opacity_slider.set(LAKE_DEPTH_DEFAULT_OPACITY)
            self.depth_opacity_slider.grid(
                row=0, column=3, sticky="e", padx=(0, 6))

            self.depth_reload_btn = ctk.CTkButton(
                layer_bar, text="Reload", width=62, height=26,
                font=ctk.CTkFont(size=11),
                command=self._reload_lake_depth_layers)
            self.depth_reload_btn.grid(row=0, column=4, sticky="e")

            if tkintermapview is not None:
                default_server = TILE_SERVERS[self.tile_combo.get()]
                try:
                    prepare_tile_cache_database(MAP_CACHE_DB)
                except OSError as e:
                    self.append_log(f"** Could not create map cache dir: {e} **")
                except sqlite3.Error as e:
                    self.append_log(f"** Could not initialise map cache DB: {e} **")

                # CachedOverlayMapView keeps base and seamark tiles as separate
                # rows in tiles.db, so toggling Nautical never has to redownload
                # the underlying map.
                self.mapview = CachedOverlayMapView(
                    right, corner_radius=8, database_path=MAP_CACHE_DB,
                    overlay_max_zoom=OPENSEAMAP_MAX_ZOOM)
                self.mapview.set_tile_server(default_server, max_zoom=19)
                self.mapview.set_overlay_tile_server(OPENSEAMAP_TILE_SERVER)
                self.mapview.grid(row=2, column=0, sticky="nsew", padx=10,
                                  pady=(0, 4))

                # Start on Halifax by default. Boat GPS will move the view once
                # a valid fix comes in.
                self.mapview.set_position(DEFAULT_MAP_LAT, DEFAULT_MAP_LON)
                self.mapview.set_zoom(DEFAULT_MAP_ZOOM)

                # Right-click map actions.  The depth probe remains visible in
                # the menu even with the layer off so it is obvious how to query
                # bathymetry; it reports that a lake-depth layer must be enabled.
                try:
                    self.mapview.add_right_click_menu_command(
                        label="Add waypoint here",
                        command=self._add_waypoint_from_map,
                        pass_coords=True)
                    self.mapview.add_right_click_menu_command(
                        label="Approx. water depth here (±)",
                        command=self._probe_depth_from_map,
                        pass_coords=True)
                except Exception as e:
                    self.append_log(
                        f"** Right-click menu unavailable ({e}); "
                        f"update tkintermapview to add waypoints/depth probes. **")

                # Attach the waypoint layer; it handles markers, connecting lines,
                # and the "Adjust nearest waypoint" right-click option.
                self.wp_map_layer = WaypointMapLayer(
                    self.mapview, self.waypoints, on_log=self.append_log)

                self.nautical_attribution = ctk.CTkLabel(
                    right,
                    text="",
                    font=ctk.CTkFont(size=9),
                    text_color=("gray45", "gray60"),
                    anchor="e")
                self.nautical_attribution.grid(
                    row=3, column=0, sticky="e", padx=12, pady=(0, 6))
                self._update_map_attribution()
            else:
                ctk.CTkLabel(right, text="Map unavailable.\nInstall it with:\n"
                             "pip install tkintermapview",
                             justify="center").grid(row=2, column=0, padx=20,
                                                    pady=20)
                self.mapview = None
            paned.add(right, minsize=280, stretch="always")

            # Default sash position: 40% left, 60% right — set after the window
            # is mapped so the geometry is finalised.
            self.win3d.after(
                50,
                lambda: paned.sash_place(
                    0, int(paned.winfo_width() * 0.4), 0)
                if paned.winfo_width() > 1 else None)

            # Seed with the latest known state
            self._refresh_3d_from_latest()

            # CTkToplevel applies its title bar / icon on a short timer, which can
            # re-stack the new window behind its parent. Raise it just after that.
            self.win3d.after(250, self.win3d.lift)
            self.win3d.after(260, self.win3d.focus)

        def _close_3d_window(self):
            if self.win3d is not None:
                self.win3d.destroy()
            self.win3d = None
            self.view3d = None
            if getattr(self, "nautical_legend_win", None) is not None:
                try:
                    self.nautical_legend_win.destroy()
                except Exception:
                    pass
            self.nautical_legend_win = None
            self.mapview = None
            self.boat_marker = None
            self.wp_marker = None
            self.breadcrumb_path = None
            self.wp_map_layer = None
            self._depth_probe_marker = None

        def _add_waypoint_from_map(self, coords):
            """Right-click handler on the map: append a new waypoint here."""
            lat, lon = coords
            wp = self.waypoints.add(lat, lon)
            self.append_log(
                f"** Added waypoint {wp['name']} at "
                f"{lat:.5f}, {lon:.5f} **")

        def _probe_depth_from_map(self, coords):
            """Right-click depth query using the active local bathymetry raster."""
            if self.mapview is None:
                return

            lat, lon = coords
            layer = getattr(self.mapview, "depth_layer", None)
            if layer is None:
                self.append_log(
                    "** Depth unavailable: enable a Lake depth layer first. **")
                return

            estimate = self.mapview.depth_estimate_at(lat, lon)
            if estimate is None:
                reason = getattr(layer, "_depth_grid_error", None)
                if reason:
                    detail = f" ({reason})"
                else:
                    detail = " (outside the static depth grid or over land/unmapped water)"
                self.append_log(
                    f"** Approx. depth unavailable at {lat:.5f}, {lon:.5f}{detail}. **")
                return

            depth = float(estimate["depth_m"])
            error = float(estimate["uncertainty_m"])
            if depth < BOAT_DRAFT_M:
                status = f"DANGER - below {BOAT_DRAFT_M:.1f} m draft"
                colour = ROUTE_DANGER_COLOR
            elif depth < SAFE_DEPTH_M:
                status = f"CAUTION - below {SAFE_DEPTH_M:.1f} m safe depth"
                colour = ROUTE_CAUTION_COLOR
            else:
                status = f"SAFE - >= {SAFE_DEPTH_M:.1f} m"
                colour = ROUTE_SAFE_COLOR

            text = f"Depth ≈ {depth:.1f} ± {error:.1f} m\n{status}"
            old_marker = getattr(self, "_depth_probe_marker", None)
            if old_marker is not None:
                try:
                    old_marker.delete()
                except Exception:
                    pass
            try:
                self._depth_probe_marker = self.mapview.set_marker(
                    lat, lon, text=text,
                    marker_color_circle=colour,
                    marker_color_outside=colour)
            except Exception:
                self._depth_probe_marker = None

            self.append_log(
                f"** {layer.name}: depth ≈ {depth:.1f} ± {error:.1f} m "
                f"at {lat:.5f}, {lon:.5f} - {status}. **")

        def _change_tile_server(self, choice):
            """Switch base map while retaining all previously cached sources."""
            if self.mapview is None:
                return
            url = TILE_SERVERS.get(choice)
            if url:
                try:
                    self.mapview.set_tile_server(url, max_zoom=19)
                except Exception as e:
                    self.append_log(f"** Could not change map source: {e} **")

        def _toggle_nautical_overlay(self):
            """Enable/disable OpenSeaMap without flushing the base-map cache."""
            if self.mapview is None:
                return

            enabled = bool(self.nautical_switch.get())
            overlay = OPENSEAMAP_TILE_SERVER if enabled else None
            try:
                # CachedOverlayMapView redraws against a different in-memory
                # cache namespace and keeps both raw sources in SQLite. No call
                # to set_tile_server() is needed here.
                self.mapview.set_overlay_tile_server(overlay)

                self._update_map_attribution()

                self.append_log(
                    f"** OpenSeaMap nautical overlay "
                    f"{'enabled' if enabled else 'disabled'}. **")
            except Exception as e:
                self.append_log(f"** Could not change nautical overlay: {e} **")

        def _update_map_attribution(self):
            """Refresh the small attribution/warning line under the map."""
            label = getattr(self, "nautical_attribution", None)
            if label is None:
                return

            parts = ["© OpenStreetMap contributors"]
            try:
                if bool(self.nautical_switch.get()):
                    parts.append("Nautical: OpenSeaMap")
            except Exception:
                pass

            depth_layer = getattr(getattr(self, "mapview", None),
                                  "depth_layer", None)
            if depth_layer is not None:
                parts.append(
                    "Depth: static PDF-derived grid - approximate only")

            label.configure(text="   |   ".join(parts))

        def _change_lake_depth_layer(self, choice):
            """Enable one locally prepared Nova Scotia lake-depth overlay."""
            if self.mapview is None:
                return

            if choice == LAKE_DEPTH_OFF:
                self.mapview.set_depth_layer(None)
                if self.wp_map_layer is not None:
                    self.wp_map_layer.refresh(structural=True)
                self._update_map_attribution()
                self.append_log("** Lake-depth overlay disabled. **")
                return

            layer = getattr(self, "_lake_depth_layers", {}).get(choice)
            if layer is None:
                return

            try:
                self.mapview.set_depth_layer(layer)
                self.depth_opacity_slider.set(layer.default_opacity)
                self.mapview.set_position(*layer.center)
                self.mapview.set_zoom(layer.preferred_zoom)
                if self.wp_map_layer is not None:
                    self.wp_map_layer.refresh(structural=True)
                self._update_map_attribution()
                if layer._depth_grid_ready:
                    spacing = layer.depth_grid_spacing_m
                    spacing_note = (
                        f"{spacing:g} m grid" if spacing is not None
                        else "static grid")
                    depth_note = (
                        f"Numeric depth loaded from {spacing_note}; "
                        f"nominal uncertainty ±{layer.depth_uncertainty_m:.2g} m.")
                else:
                    depth_note = (
                        "Numeric depth unavailable: "
                        f"{layer._depth_grid_error or 'unknown grid error'}." )
                self.append_log(
                    f"** Lake depth enabled: {layer.name}. {depth_note} "
                    "Bathymetry may not be accurate. **")
            except Exception as e:
                self.append_log(f"** Could not enable lake-depth layer: {e} **")

        def _change_lake_depth_opacity(self, value):
            """Adjust bathymetry transparency without invalidating source caches."""
            if self.mapview is None or self.mapview.depth_layer is None:
                return
            try:
                self.mapview.set_depth_opacity(float(value))
            except Exception:
                pass

        def _reload_lake_depth_layers(self):
            """Rescan lake_depth_maps so new/updated packages appear immediately."""
            previous = LAKE_DEPTH_OFF
            try:
                previous = self.depth_combo.get()
            except Exception:
                pass

            try:
                os.makedirs(LAKE_DEPTH_DIR, exist_ok=True)
            except OSError:
                pass
            self._lake_depth_layers = discover_lake_depth_layers()
            values = [LAKE_DEPTH_OFF] + list(self._lake_depth_layers.keys())
            self.depth_combo.configure(values=values)

            if previous in self._lake_depth_layers:
                self.depth_combo.set(previous)
                self._change_lake_depth_layer(previous)
            else:
                self.depth_combo.set(LAKE_DEPTH_OFF)
                if self.mapview is not None:
                    self.mapview.set_depth_layer(None)
                self._update_map_attribution()

            self.append_log(
                f"** Found {len(self._lake_depth_layers)} prepared "
                "lake-depth map(s). **")

        def _make_legend_icon(self, parent, kind):
            """Return a small Canvas approximating one common OpenSeaMap symbol."""
            bg = parent.cget("fg_color") if hasattr(parent, "cget") else "transparent"
            c = tk.Canvas(parent, width=28, height=20, highlightthickness=0,
                          bd=0, bg="#21232d" if ctk.get_appearance_mode() == "Dark" else "#f4f4f7")
            def line(*args, **kwargs):
                c.create_line(*args, width=2, capstyle="round", **kwargs)
            if kind == "port":
                c.create_rectangle(10, 5, 18, 15, fill="#d14b4b", outline="#d14b4b")
            elif kind == "starboard":
                c.create_polygon(14, 4, 20, 15, 8, 15, fill="#3ea85b", outline="#3ea85b")
            elif kind == "safe":
                c.create_oval(8, 2, 20, 18, fill="#ffffff", outline="#cc4c4c", width=2)
                line(10, 6, 18, 14, fill="#cc4c4c")
                line(18, 6, 10, 14, fill="#cc4c4c")
            elif kind == "isolated":
                c.create_oval(8, 4, 20, 16, fill="#111111", outline="#111111")
                line(8, 10, 20, 10, fill="#d14b4b")
            elif kind == "north":
                c.create_polygon(14, 2, 21, 12, 7, 12, fill="#111111", outline="#111111")
                c.create_polygon(14, 8, 19, 18, 9, 18, fill="#f0d34a", outline="#f0d34a")
            elif kind == "south":
                c.create_polygon(14, 2, 19, 12, 9, 12, fill="#f0d34a", outline="#f0d34a")
                c.create_polygon(14, 8, 21, 18, 7, 18, fill="#111111", outline="#111111")
            elif kind == "east":
                c.create_polygon(8, 3, 14, 10, 8, 17, fill="#111111", outline="#111111")
                c.create_polygon(14, 3, 20, 10, 14, 17, fill="#f0d34a", outline="#f0d34a")
            elif kind == "west":
                c.create_polygon(8, 3, 14, 10, 8, 17, fill="#f0d34a", outline="#f0d34a")
                c.create_polygon(14, 3, 20, 10, 14, 17, fill="#111111", outline="#111111")
            elif kind == "light":
                line(14, 16, 14, 5, fill="#e6e6e6")
                c.create_oval(10, 2, 18, 10, fill="#ffd85a", outline="#ffd85a")
            elif kind == "marina":
                line(6, 15, 22, 15, fill="#5f9df0")
                line(10, 15, 10, 5, fill="#5f9df0")
                line(18, 15, 18, 8, fill="#5f9df0")
            elif kind == "anchor":
                line(14, 4, 14, 13, fill="#d5d8df")
                c.create_arc(8, 8, 20, 18, start=200, extent=140, style='arc', outline="#d5d8df", width=2)
                line(10, 15, 14, 18, 18, 15, fill="#d5d8df")
            else:  # warn
                c.create_polygon(14, 3, 24, 18, 4, 18, fill="#f0d34a", outline="#caa400")
                line(14, 7, 14, 12, fill="#111111")
                c.create_oval(13, 14, 15, 16, fill="#111111", outline="#111111")
            return c

        def _toggle_nautical_legend(self):
            """Open or focus a small operator legend for common seamark symbols."""
            win = getattr(self, "nautical_legend_win", None)
            if win is not None and win.winfo_exists():
                if str(win.state()) == "withdrawn":
                    win.deiconify()
                win.lift()
                win.focus()
                return

            win = ctk.CTkToplevel(self)
            self.nautical_legend_win = win
            win.title("OpenSeaMap Legend")
            win.geometry("520x500")
            win.minsize(460, 360)
            win.grid_columnconfigure(0, weight=1)
            win.grid_rowconfigure(2, weight=1)
            win.protocol("WM_DELETE_WINDOW", self._close_nautical_legend)

            title = ctk.CTkLabel(
                win,
                text="Common OpenSeaMap seamarks",
                font=ctk.CTkFont(size=16, weight="bold"))
            title.grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))

            note = ctk.CTkLabel(
                win,
                text=("This is a practical quick-reference for the symbols most useful "
                      "when planning waypoints. Zooming to 16–18 usually reveals the "
                      "most nautical detail."),
                justify="left", wraplength=480, anchor="w")
            note.grid(row=1, column=0, sticky="new", padx=14, pady=(0, 8))

            frame = ctk.CTkScrollableFrame(win, corner_radius=10)
            frame.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 12))
            frame.grid_columnconfigure(1, weight=1)

            for row, (kind, title_txt, desc_txt) in enumerate(NAUTICAL_LEGEND_ITEMS):
                icon = self._make_legend_icon(frame, kind)
                icon.grid(row=row, column=0, sticky="nw", padx=(8, 10), pady=(8, 2))
                ctk.CTkLabel(
                    frame, text=title_txt, anchor="w",
                    font=ctk.CTkFont(size=13, weight="bold")).grid(
                    row=row, column=1, sticky="nw", padx=(0, 6), pady=(7, 0))
                ctk.CTkLabel(
                    frame, text=desc_txt, anchor="w", justify="left",
                    wraplength=390).grid(
                    row=row, column=2, sticky="nw", padx=(0, 10), pady=(8, 2))

        def _close_nautical_legend(self):
            win = getattr(self, "nautical_legend_win", None)
            if win is not None:
                try:
                    win.destroy()
                except Exception:
                    pass
            self.nautical_legend_win = None

        def _refresh_map(self):
            """Re-draw waypoint markers and connecting lines from the store."""
            if self.wp_map_layer is not None:
                self.wp_map_layer.refresh(structural=True)
                self.append_log("** Map waypoints refreshed. **")

        def _download_visible_tiles(self):
            """Save the visible area to the offline tile database.

            Normal interactive viewing is already cached automatically by
            CachedOverlayMapView. This button remains for explicit pre-loading
            with providers that allow it.
            """
            if self.mapview is None:
                return
            try:
                import tkintermapview
            except ImportError:
                return

            server_name = self.tile_combo.get()
            server_url = TILE_SERVERS.get(server_name)

            # Public OSM tiles require local caching of viewed tiles, but prohibit
            # bulk/offline prefetching. Interactive views are already cached by
            # this file, so do not run OfflineLoader against tile.openstreetmap.org.
            if server_name == "OpenStreetMap":
                self.append_log(
                    "** OpenStreetMap public tiles are cached automatically while "
                    "you view them. Bulk 'Save Offline' downloads are disabled for "
                    "this provider. Select a provider that permits offline "
                    "prefetching for this button. **")
                return

            # Bounding box of the currently visible map area.
            try:
                top_left = self.mapview.convert_canvas_coords_to_decimal_coords(0, 0)
                bottom_right = self.mapview.convert_canvas_coords_to_decimal_coords(
                    self.mapview.winfo_width(), self.mapview.winfo_height())
            except Exception as e:
                self.append_log(f"** Could not read map bounds: {e} **")
                return

            current_zoom = int(self.mapview.zoom)
            zoom_min = max(0, current_zoom - 3)
            zoom_max = min(19, current_zoom + 3)

            # Rough estimate of tile count so the user knows what they're asking for.
            lat_span = abs(top_left[0] - bottom_right[0])
            lon_span = abs(top_left[1] - bottom_right[1])
            est_tiles = 0
            for z in range(zoom_min, zoom_max + 1):
                n = 2 ** z
                est_tiles += max(1, int(n * lon_span / 360.0)) * \
                             max(1, int(n * lat_span / 180.0))

            self.append_log(
                f"** Downloading tiles for zoom {zoom_min}–{zoom_max} "
                f"(~{est_tiles} tiles) from {server_name}... **")
            self.map_download_btn.configure(state="disabled",
                                            text="Downloading...")

            def _worker():
                try:
                    prepare_tile_cache_database(MAP_CACHE_DB)
                    loader = tkintermapview.OfflineLoader(
                        path=MAP_CACHE_DB, tile_server=server_url)
                    loader.save_offline_tiles(top_left, bottom_right,
                                              zoom_min, zoom_max)
                    self.queue.put(("log", "** Tile download complete. **"))
                except Exception as ex:
                    self.queue.put(("log", f"** Tile download failed: {ex} **"))
                finally:
                    self.queue.put(("map_dl_done", None))

            threading.Thread(target=_worker, daemon=True).start()

        def _refresh_3d_from_latest(self):
            """Push the most recent telemetry into the 3D view."""
            if self.view3d is None:
                return
            self.view3d.set_state(getattr(self, "_last_heading", 0.0),
                                  self.boat_act.sail_angle,
                                  self.boat_act.rudder_angle)

        def update_3d_and_map(self, d):
            """Feed the latest state into the 3D boat and the GPS map (if open)."""
            self._last_heading = d.get("cb", getattr(self, "_last_heading", 0.0))
            if self.view3d is not None:
                self.view3d.set_state(self._last_heading,
                                      self.boat_act.sail_angle,
                                      self.boat_act.rudder_angle)
            if self.mapview is None:
                return
            try:
                if self.gps_track:
                    lat, lon, _ = self.gps_track[-1]
                    if self.boat_marker is None:
                        self.boat_marker = self.mapview.set_marker(lat, lon,
                                                                   text="Boat")
                        self.mapview.set_position(lat, lon)
                    else:
                        self.boat_marker.set_position(lat, lon)
                    coords = [(p[0], p[1]) for p in self.gps_track[-500:]]
                    if len(coords) > 1:
                        # Only delete OUR breadcrumb path, not the waypoint path.
                        if self.breadcrumb_path is not None:
                            try:
                                self.breadcrumb_path.delete()
                            except Exception:
                                pass
                        self.breadcrumb_path = self.mapview.set_path(coords)
                tlat = d.get("tlat")
                tlon = d.get("tlon")
                if (tlat is not None and tlon is not None
                        and (abs(tlat) > 1e-4 or abs(tlon) > 1e-4)):
                    if self.wp_marker is None:
                        self.wp_marker = self.mapview.set_marker(tlat, tlon,
                                                                 text="Waypoint")
                    else:
                        self.wp_marker.set_position(tlat, tlon)
            except Exception:
                pass  # window may be tearing down as a packet lands
