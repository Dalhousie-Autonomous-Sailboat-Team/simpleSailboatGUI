"""Nova Scotia lake-inventory bathymetry: the calibrated overlay and its depth grid.

Shared by the desktop map (map_view.py) and the web UI (web_server.py). Each
prepared lake lives in lake_depth_maps/<Lake Name>/ with metadata.json, the
overlay PNG and an optional depth_grid.npz.
"""

import json
import math
import os
import threading


# Locally processed Nova Scotia lake-inventory bathymetry. Each lake lives in
# its own subdirectory and contains an RGBA PNG plus metadata.json. Keeping
# these overlays local makes them instant/offline once a lake has been
# prepared, and avoids repeatedly downloading the old inventory PDFs.
LAKE_DEPTH_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "lake_depth_maps")
LAKE_DEPTH_OFF = "Off"
LAKE_DEPTH_DEFAULT_OPACITY = 0.72

# Surface a concise accuracy warning whenever a bathymetry layer is on.
NS_LAKE_DEPTH_ATTRIBUTION = (
    "Lake depth: Nova Scotia Fisheries & Aquaculture Lake Inventory "
    "- Bathymetry may not be accurate")

# Temporary per-lake alignment overrides so the latest map_view.py can fix
# previously packaged lake layers without requiring the user to manually edit
# each metadata.json. If a packaged lake is updated later, these values can be
# removed or replaced by the corrected metadata.
LAKE_DEPTH_BOUND_OVERRIDES = {
    "lake micmac": {
        # Bounds after the first-point translation correction.
        "north": 44.6997751,
        "south": 44.6845151,
        "west": -63.5621433,
        "east": -63.5467633,
        # Similarity transform solved from the second control point.
        # Positive rotation is counter-clockwise on the map.
        "transform": {
            "anchor_lat": 44.6940442,
            "anchor_lon": -63.5602020,
            "scale": 1.0483028,
            "rotation_deg": -3.3474663,
        },
    },
    "lake charles": {
        # Bounds after the first-point translation correction.
        "north": 44.7373715,
        "south": 44.7084315,
        "west": -63.5563978,
        "east": -63.5450578,
        # Similarity transform solved from the second control point.
        "transform": {
            "anchor_lat": 44.7158269,
            "anchor_lon": -63.5537399,
            "scale": 1.0762620,
            "rotation_deg": 3.1613348,
        },
    },
}


def webmercator_y(lat):
    """Return normalized Web-Mercator Y (0..1) for latitude in degrees."""
    lat = max(-85.05112878, min(85.05112878, float(lat)))
    r = math.radians(lat)
    return (1.0 - math.asinh(math.tan(r)) / math.pi) / 2.0


def webmercator_x(lon):
    """Return normalized Web-Mercator X (0..1) for longitude in degrees."""
    return (float(lon) + 180.0) / 360.0


def webmercator_lon(x):
    """Convert normalized Web-Mercator X back to longitude."""
    return float(x) * 360.0 - 180.0


def webmercator_lat(y):
    """Convert normalized Web-Mercator Y back to latitude."""
    n = math.pi * (1.0 - 2.0 * float(y))
    return math.degrees(math.atan(math.sinh(n)))


class LakeDepthRaster:
    """Local bathymetry raster with translation, scale and rotation correction.

    The original Nova Scotia inventory map is first georeferenced by a simple
    north/south/east/west bounding box.  A per-lake similarity transform may
    then be applied around a known-good geographic anchor::

        transform = {
            "anchor_lat": 44.6940442,
            "anchor_lon": -63.5602020,
            "scale": 1.0483,
            "rotation_deg": -3.3475,
        }

    This is much more accurate than trying to represent a rotated scan using
    only axis-aligned bounds.  The transform is performed in Web-Mercator
    space, the same projection used by the XYZ base-map tiles, so the contours
    stay aligned while panning and zooming.
    """

    _TILE_CACHE_LIMIT = 600

    def __init__(self, metadata_path):
        from PIL import Image

        self.metadata_path = os.path.abspath(metadata_path)
        self.package_dir = os.path.dirname(self.metadata_path)
        with open(self.metadata_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        self.name = str(meta.get("name") or os.path.basename(self.package_dir))
        image_name = meta.get("image", "overlay.png")
        self.image_path = os.path.join(self.package_dir, image_name)
        if not os.path.isfile(self.image_path):
            raise FileNotFoundError(self.image_path)

        bounds = dict(meta.get("bounds") or {})
        transform = dict(meta.get("transform") or {})

        # Code-side overrides make it possible to calibrate the supplied lake
        # packages without requiring every existing metadata.json to be edited.
        override = LAKE_DEPTH_BOUND_OVERRIDES.get(self.name.strip().lower())
        if override:
            for k in ("north", "south", "west", "east"):
                if k in override:
                    bounds[k] = override[k]
            if override.get("transform"):
                transform.update(override["transform"])

        self.north = float(bounds["north"])
        self.south = float(bounds["south"])
        self.west = float(bounds["west"])
        self.east = float(bounds["east"])
        if not (self.north > self.south and self.east > self.west):
            raise ValueError("invalid depth-map bounds")

        self.preferred_zoom = int(meta.get("preferred_zoom", 15))
        self.source_url = str(meta.get("source_url", ""))
        self.notes = str(meta.get("notes", ""))
        self.default_opacity = float(
            meta.get("opacity", LAKE_DEPTH_DEFAULT_OPACITY))
        self.default_opacity = max(0.05, min(1.0, self.default_opacity))

        # Similarity transform for the visual PDF overlay. Numeric depth grids
        # are already stored north-up in geographic coordinates, but the image
        # overlay still uses this calibration when rendered into XYZ tiles.
        self.transform_scale = float(transform.get("scale", 1.0))
        if self.transform_scale <= 0:
            raise ValueError("lake-depth transform scale must be > 0")
        self.rotation_deg = float(transform.get("rotation_deg", 0.0))
        self.rotation_rad = math.radians(self.rotation_deg)
        self._rot_c = math.cos(self.rotation_rad)
        self._rot_s = math.sin(self.rotation_rad)

        default_anchor_lat = (self.north + self.south) / 2.0
        default_anchor_lon = (self.west + self.east) / 2.0
        self.anchor_lat = float(transform.get("anchor_lat", default_anchor_lat))
        self.anchor_lon = float(transform.get("anchor_lon", default_anchor_lon))
        self._anchor_wx = webmercator_x(self.anchor_lon)
        self._anchor_wy = webmercator_y(self.anchor_lat)

        with Image.open(self.image_path) as img:
            img.load()
            self.image = img.convert("RGBA")

        self._tile_cache = {}
        self._tile_cache_lock = threading.Lock()

        # Axis-aligned pre-transform raster bounds in Web-Mercator.
        self._wx0 = webmercator_x(self.west)
        self._wx1 = webmercator_x(self.east)
        self._wy0 = webmercator_y(self.north)
        self._wy1 = webmercator_y(self.south)
        self._layer_wx = self._wx1 - self._wx0
        self._layer_wy = self._wy1 - self._wy0

        # Numeric depth values come from a precomputed static grid stored next
        # to the visual overlay.  The GUI never tries to interpret contour ink.
        # This keeps runtime behaviour deterministic and makes the depth data
        # easy to inspect/replace without changing application code.
        self.depth_grid_name = str(meta.get("depth_grid", "depth_grid.npz"))
        self.depth_grid_path = os.path.join(self.package_dir, self.depth_grid_name)
        self.depth_grid = None
        self.depth_grid_spacing_m = None
        self.depth_uncertainty_m = max(
            0.1, float(meta.get("depth_uncertainty_m", 1.0)))
        self._depth_grid_ready = False
        self._depth_grid_error = None
        self._load_depth_grid()

        # A stable identity for the composite RAM cache.  Transform values are
        # included so changing calibration immediately creates a fresh variant.
        stat_img = os.stat(self.image_path)
        stat_meta = os.stat(self.metadata_path)
        if os.path.isfile(self.depth_grid_path):
            stat_grid = os.stat(self.depth_grid_path)
            grid_identity = (stat_grid.st_mtime_ns, stat_grid.st_size)
        else:
            grid_identity = None
        self.cache_id = (
            self.name, self.image_path, stat_img.st_mtime_ns, stat_img.st_size,
            stat_meta.st_mtime_ns, stat_meta.st_size, grid_identity,
            round(self.anchor_lat, 8), round(self.anchor_lon, 8),
            round(self.transform_scale, 8), round(self.rotation_deg, 8),
            round(self.north, 8), round(self.south, 8),
            round(self.west, 8), round(self.east, 8))

    # ------------------------------------------------------------------ #
    # Similarity transform helpers
    # ------------------------------------------------------------------ #

    def _forward_transform(self, wx, wy):
        """Map pre-transform Web-Mercator coordinate to calibrated position."""
        # Use north-positive local coordinates for intuitive rotation signs.
        dx = wx - self._anchor_wx
        dy_north = self._anchor_wy - wy

        x2 = self.transform_scale * (
            self._rot_c * dx - self._rot_s * dy_north)
        y2_north = self.transform_scale * (
            self._rot_s * dx + self._rot_c * dy_north)

        return (self._anchor_wx + x2,
                self._anchor_wy - y2_north)

    def _inverse_transform(self, wx, wy):
        """Map calibrated Web-Mercator coordinate back into source-raster space."""
        dx2 = wx - self._anchor_wx
        dy2_north = self._anchor_wy - wy
        inv = 1.0 / self.transform_scale

        # R(-theta) / scale.
        dx = inv * (self._rot_c * dx2 + self._rot_s * dy2_north)
        dy_north = inv * (-self._rot_s * dx2 + self._rot_c * dy2_north)

        return (self._anchor_wx + dx,
                self._anchor_wy - dy_north)

    def _source_pixel_for_tile_pixel(self, px, py, tx0, ty0,
                                     tile_span, tile_size):
        """Return source-image pixel corresponding to one output tile pixel."""
        wx_out = tx0 + (float(px) / tile_size) * tile_span
        wy_out = ty0 + (float(py) / tile_size) * tile_span
        wx_src, wy_src = self._inverse_transform(wx_out, wy_out)

        src_w, src_h = self.image.size
        sx = (wx_src - self._wx0) / self._layer_wx * src_w
        sy = (wy_src - self._wy0) / self._layer_wy * src_h
        return sx, sy

    # ------------------------------------------------------------------ #
    # Static numeric bathymetry grid
    # ------------------------------------------------------------------ #

    def _load_depth_grid(self):
        """Load a precomputed north-up depth grid from ``depth_grid.npz``.

        Required NPZ fields:
            depth_m       2-D float array; NaN means land/unmapped
            north/south/west/east

        Optional fields:
            spacing_m
            uncertainty_m

        No contour detection, OCR or image analysis is performed here.
        """
        if not os.path.isfile(self.depth_grid_path):
            self._depth_grid_error = f"missing {self.depth_grid_name}"
            return

        try:
            import numpy as np

            with np.load(self.depth_grid_path, allow_pickle=False) as data:
                depth = np.asarray(data["depth_m"], dtype=np.float32)
                if depth.ndim != 2 or depth.shape[0] < 2 or depth.shape[1] < 2:
                    raise ValueError("depth_m must be a 2-D grid of at least 2x2")

                north = float(data["north"])
                south = float(data["south"])
                west = float(data["west"])
                east = float(data["east"])
                if not (north > south and east > west):
                    raise ValueError("invalid depth-grid geographic bounds")

                self.depth_grid = depth
                self.depth_grid_north = north
                self.depth_grid_south = south
                self.depth_grid_west = west
                self.depth_grid_east = east

                if "spacing_m" in data.files:
                    self.depth_grid_spacing_m = float(data["spacing_m"])
                if "uncertainty_m" in data.files:
                    self.depth_uncertainty_m = max(
                        0.1, float(data["uncertainty_m"]))

            self._depth_grid_ready = True
            self._depth_grid_error = None
        except Exception as exc:
            self.depth_grid = None
            self._depth_grid_ready = False
            self._depth_grid_error = str(exc)

    def depth_at(self, lat, lon):
        """Return bilinearly interpolated depth from the static grid.

        Returns ``None`` outside the grid or over land/unmapped cells.
        ``depth_m`` is an approximate value from the prepared PDF-derived grid;
        ``uncertainty_m`` is the nominal error assigned to that lake package.
        """
        if not self._depth_grid_ready or self.depth_grid is None:
            return None

        lat = float(lat)
        lon = float(lon)
        if not (self.depth_grid_south <= lat <= self.depth_grid_north and
                self.depth_grid_west <= lon <= self.depth_grid_east):
            return None

        import numpy as np

        rows, cols = self.depth_grid.shape
        fy = ((self.depth_grid_north - lat) /
              (self.depth_grid_north - self.depth_grid_south)) * (rows - 1)
        fx = ((lon - self.depth_grid_west) /
              (self.depth_grid_east - self.depth_grid_west)) * (cols - 1)

        # Do not smear a water value onto adjacent land when interpolating at
        # the shoreline. The nearest 1 m cell must itself contain water.
        nearest_x = max(0, min(cols - 1, int(round(fx))))
        nearest_y = max(0, min(rows - 1, int(round(fy))))
        if not np.isfinite(self.depth_grid[nearest_y, nearest_x]):
            return None

        x0 = max(0, min(cols - 1, int(math.floor(fx))))
        y0 = max(0, min(rows - 1, int(math.floor(fy))))
        x1 = min(cols - 1, x0 + 1)
        y1 = min(rows - 1, y0 + 1)
        tx = max(0.0, min(1.0, fx - x0))
        ty = max(0.0, min(1.0, fy - y0))

        samples = (
            (self.depth_grid[y0, x0], (1.0 - tx) * (1.0 - ty)),
            (self.depth_grid[y0, x1], tx * (1.0 - ty)),
            (self.depth_grid[y1, x0], (1.0 - tx) * ty),
            (self.depth_grid[y1, x1], tx * ty),
        )
        weighted = 0.0
        total_weight = 0.0
        for value, weight in samples:
            if np.isfinite(value) and weight > 0.0:
                weighted += float(value) * weight
                total_weight += weight

        if total_weight <= 1e-9:
            return None

        depth_m = max(0.0, weighted / total_weight)
        return {
            "depth_m": float(depth_m),
            "uncertainty_m": float(self.depth_uncertainty_m),
            "grid_spacing_m": self.depth_grid_spacing_m,
        }

    @property
    def center(self):
        """Geographic centre after the calibration transform is applied."""
        wx = (self._wx0 + self._wx1) / 2.0
        wy = (self._wy0 + self._wy1) / 2.0
        wx, wy = self._forward_transform(wx, wy)
        return (webmercator_lat(wy), webmercator_lon(wx))

    def render_tile(self, zoom, x, y, tile_size, opacity=1.0):
        """Render the calibrated raster into one transparent XYZ tile."""
        from PIL import Image

        opacity = max(0.0, min(1.0, float(opacity)))
        if opacity <= 0.0:
            return None

        key = (int(zoom), int(x), int(y), int(tile_size), round(opacity, 3))
        with self._tile_cache_lock:
            cached = self._tile_cache.get(key)
            if cached is not None:
                return cached.copy()

        n = float(2 ** int(zoom))
        tile_span = 1.0 / n
        tx0 = x * tile_span
        ty0 = y * tile_span

        # PIL's affine transform needs output->input mapping.  The whole
        # georeferencing + rotation + scale chain is affine in Web-Mercator, so
        # three sample points give exact coefficients for this tile.
        p00 = self._source_pixel_for_tile_pixel(
            0.0, 0.0, tx0, ty0, tile_span, tile_size)
        p10 = self._source_pixel_for_tile_pixel(
            1.0, 0.0, tx0, ty0, tile_span, tile_size)
        p01 = self._source_pixel_for_tile_pixel(
            0.0, 1.0, tx0, ty0, tile_span, tile_size)

        a = p10[0] - p00[0]
        b = p01[0] - p00[0]
        c = p00[0]
        d = p10[1] - p00[1]
        e = p01[1] - p00[1]
        f = p00[1]

        # Fast reject: map the output tile corners to source coordinates and
        # skip tiles whose inverse-projected footprint cannot touch the image.
        src_w, src_h = self.image.size
        corners = [
            self._source_pixel_for_tile_pixel(
                0, 0, tx0, ty0, tile_span, tile_size),
            self._source_pixel_for_tile_pixel(
                tile_size, 0, tx0, ty0, tile_span, tile_size),
            self._source_pixel_for_tile_pixel(
                0, tile_size, tx0, ty0, tile_span, tile_size),
            self._source_pixel_for_tile_pixel(
                tile_size, tile_size, tx0, ty0, tile_span, tile_size),
        ]
        min_sx = min(v[0] for v in corners)
        max_sx = max(v[0] for v in corners)
        min_sy = min(v[1] for v in corners)
        max_sy = max(v[1] for v in corners)
        if max_sx < 0 or min_sx > src_w or max_sy < 0 or min_sy > src_h:
            return None

        if hasattr(Image, "Transform"):
            affine_mode = Image.Transform.AFFINE
        else:
            affine_mode = Image.AFFINE
        if hasattr(Image, "Resampling"):
            resample = Image.Resampling.BICUBIC
        else:
            resample = Image.BICUBIC

        tile = self.image.transform(
            (tile_size, tile_size),
            affine_mode,
            (a, b, c, d, e, f),
            resample=resample,
            fillcolor=(0, 0, 0, 0))

        if opacity < 0.999:
            alpha = tile.getchannel("A").point(lambda av: int(av * opacity))
            tile.putalpha(alpha)

        # Do not keep completely transparent tiles in memory.
        if tile.getbbox() is None:
            return None

        with self._tile_cache_lock:
            self._tile_cache[key] = tile.copy()
            excess = len(self._tile_cache) - self._TILE_CACHE_LIMIT
            if excess > 0:
                for old_key in list(self._tile_cache.keys())[:excess]:
                    self._tile_cache.pop(old_key, None)
        return tile


def discover_lake_depth_layers(directory=LAKE_DEPTH_DIR):
    """Return {display_name: LakeDepthRaster} for valid local packages."""
    layers = {}
    if not os.path.isdir(directory):
        return layers
    for root, _, files in os.walk(directory):
        if "metadata.json" not in files:
            continue
        meta_path = os.path.join(root, "metadata.json")
        try:
            layer = LakeDepthRaster(meta_path)
            # Disambiguate duplicate names without hiding either package.
            name = layer.name
            if name in layers:
                suffix = 2
                while f"{name} ({suffix})" in layers:
                    suffix += 1
                name = f"{name} ({suffix})"
            layers[name] = layer
        except Exception:
            # One malformed package should not prevent the map window opening.
            continue
    return layers

