"""Persistent XYZ map-tile cache shared by both ground-station UIs.

Tiles live in one SQLite database (map_downloads/tiles.db) with the same core
schema as tkintermapview, so the desktop map, its OfflineLoader and the web
UI all read and fill the same cache. A tile saved for offline use in one app
works offline in the other.
"""

import os
import re
import sqlite3
import threading
import time
from email.utils import parsedate_to_datetime

import requests
from PIL import UnidentifiedImageError


MAP_CACHE_DIR     = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "map_downloads")
MAP_CACHE_DB      = os.path.join(MAP_CACHE_DIR, "tiles.db")

OPENSEAMAP_TILE_SERVER = "https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png"
OPENSEAMAP_MAX_ZOOM = 18

# Normal viewed tiles are cached persistently. Seven days is also the minimum
# fallback cache lifetime requested by the public OpenStreetMap tile policy
# when no useful HTTP cache header is supplied.
VIEW_TILE_FALLBACK_TTL_S = 7 * 24 * 60 * 60

# Identify this application instead of using tkintermapview's generic UA.
MAP_USER_AGENT = "SailboatGroundStationMonitor/1.0 (TkinterMapView)"

TILE_SERVERS = {
    "CartoDB Voyager": "https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png",
    "OpenStreetMap":   "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "CartoDB Light":   "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
    "CartoDB Dark":    "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
}


def prepare_tile_cache_database(path):
    """Create/tune the shared SQLite tile cache before map threads start."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-32768")       # ~32 MiB SQLite page cache
        conn.execute("PRAGMA busy_timeout=5000")

        # Same core schema used by tkintermapview. Keeping it compatible means
        # OfflineLoader and the normal map can share one database.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS server (
                url VARCHAR(300) PRIMARY KEY NOT NULL,
                max_zoom INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tiles (
                zoom INTEGER NOT NULL,
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                server VARCHAR(300) NOT NULL,
                tile_image BLOB NOT NULL,
                CONSTRAINT fk_server FOREIGN KEY (server) REFERENCES server (url),
                CONSTRAINT pk_tiles PRIMARY KEY (zoom, x, y, server)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sections (
                position_a VARCHAR(100) NOT NULL,
                position_b VARCHAR(100) NOT NULL,
                zoom_a INTEGER NOT NULL,
                zoom_b INTEGER NOT NULL,
                server VARCHAR(300) NOT NULL,
                CONSTRAINT fk_server FOREIGN KEY (server) REFERENCES server (url),
                CONSTRAINT pk_sections
                    PRIMARY KEY (position_a, position_b, zoom_a, zoom_b, server)
            )
        """)

        # Extra metadata used only by this application. Tiles inserted by
        # tkintermapview's OfflineLoader have no row here and are therefore
        # treated as intentionally-offline/permanent tiles.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tile_cache_meta (
                zoom INTEGER NOT NULL,
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                server VARCHAR(300) NOT NULL,
                cached_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                etag TEXT,
                last_modified TEXT,
                CONSTRAINT pk_tile_cache_meta PRIMARY KEY (zoom, x, y, server)
            )
        """)
        conn.commit()
    finally:
        conn.close()


class TileCache:
    """Disk-first tile source with HTTP-cache-aware refresh.

    get() prefers the SQLite copy, refreshes a stale one with a conditional
    request, and falls back to the stale copy when the network is down. A tile
    that failed is not retried for NETWORK_RETRY_DELAY_S. Callers pass their
    own SQLite cursor (one per thread) and a ``decode`` callable that turns
    tile bytes into whatever they draw, raising on a body that isn't an image.
    """

    NETWORK_RETRY_DELAY_S = 60

    def __init__(self, user_agent=MAP_USER_AGENT):
        self.user_agent = user_agent
        self._http_local = threading.local()
        self._failed_tile_until = {}

    def _session(self):
        session = getattr(self._http_local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": self.user_agent})
            self._http_local.session = session
        return session

    @staticmethod
    def tile_url(server, zoom, x, y):
        return (server.replace("{x}", str(x))
                      .replace("{y}", str(y))
                      .replace("{z}", str(zoom)))

    @staticmethod
    def _expires_from_headers(headers, now):
        cache_control = headers.get("Cache-Control", "")
        m = re.search(r"(?:^|,)\s*max-age\s*=\s*(\d+)", cache_control,
                      flags=re.IGNORECASE)
        if m:
            return now + max(0, int(m.group(1)))

        expires = headers.get("Expires")
        if expires:
            try:
                dt = parsedate_to_datetime(expires)
                if dt.tzinfo is not None:
                    return max(now, dt.timestamp())
            except Exception:
                pass

        return now + VIEW_TILE_FALLBACK_TTL_S

    @staticmethod
    def _configure_thread_db(db_cursor):
        if db_cursor is None:
            return
        try:
            db_cursor.execute("PRAGMA busy_timeout=5000")
        except Exception:
            pass

    def read_disk_tile(self, db_cursor, server, zoom, x, y):
        if db_cursor is None:
            return None, None
        try:
            self._configure_thread_db(db_cursor)
            db_cursor.execute(
                "SELECT tile_image FROM tiles "
                "WHERE zoom=? AND x=? AND y=? AND server=?",
                (zoom, x, y, server))
            row = db_cursor.fetchone()
            if row is None:
                return None, None

            db_cursor.execute(
                "SELECT cached_at, expires_at, etag, last_modified "
                "FROM tile_cache_meta "
                "WHERE zoom=? AND x=? AND y=? AND server=?",
                (zoom, x, y, server))
            meta = db_cursor.fetchone()
            return row[0], meta
        except sqlite3.OperationalError:
            return None, None
        except Exception:
            return None, None

    def _write_disk_tile(self, db_cursor, server, zoom, x, y,
                         image_bytes, response_headers, max_zoom):
        if db_cursor is None or image_bytes is None:
            return
        now = time.time()
        expires_at = self._expires_from_headers(response_headers, now)
        try:
            self._configure_thread_db(db_cursor)
            db_cursor.execute(
                "INSERT OR IGNORE INTO server (url, max_zoom) VALUES (?, ?)",
                (server, max_zoom))
            db_cursor.execute(
                "INSERT OR REPLACE INTO tiles "
                "(zoom, x, y, server, tile_image) VALUES (?, ?, ?, ?, ?)",
                (zoom, x, y, server, sqlite3.Binary(image_bytes)))
            db_cursor.execute(
                "INSERT OR REPLACE INTO tile_cache_meta "
                "(zoom, x, y, server, cached_at, expires_at, etag, last_modified) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (zoom, x, y, server, now, expires_at,
                 response_headers.get("ETag"),
                 response_headers.get("Last-Modified")))
            db_cursor.connection.commit()
        except sqlite3.OperationalError:
            # A temporary writer collision must not make the map disappear.
            try:
                db_cursor.connection.rollback()
            except Exception:
                pass
        except Exception:
            try:
                db_cursor.connection.rollback()
            except Exception:
                pass

    def _refresh_disk_meta(self, db_cursor, server, zoom, x, y,
                           old_meta, response_headers):
        if db_cursor is None:
            return
        now = time.time()
        expires_at = self._expires_from_headers(response_headers, now)
        old_etag = old_meta[2] if old_meta else None
        old_last_modified = old_meta[3] if old_meta else None
        try:
            db_cursor.execute(
                "INSERT OR REPLACE INTO tile_cache_meta "
                "(zoom, x, y, server, cached_at, expires_at, etag, last_modified) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (zoom, x, y, server, now, expires_at,
                 response_headers.get("ETag") or old_etag,
                 response_headers.get("Last-Modified") or old_last_modified))
            db_cursor.connection.commit()
        except Exception:
            try:
                db_cursor.connection.rollback()
            except Exception:
                pass

    def get(self, server, zoom, x, y, db_cursor, decode, max_zoom=19):
        """Return one source tile as ``decode(bytes)``, preferring disk."""
        cached_bytes, meta = self.read_disk_tile(
            db_cursor, server, zoom, x, y)

        now = time.time()
        # No metadata means the tile came from OfflineLoader or another
        # explicit offline source; keep using it without an online refresh.
        if cached_bytes is not None and meta is None:
            try:
                return decode(cached_bytes)
            except Exception:
                cached_bytes = None

        # Runtime-cached tile still inside its HTTP/fallback lifetime.
        if cached_bytes is not None and meta is not None and now < meta[1]:
            try:
                return decode(cached_bytes)
            except Exception:
                cached_bytes = None

        fail_key = (server, int(zoom), int(x), int(y))
        if self._failed_tile_until.get(fail_key, 0) > now:
            if cached_bytes is not None:
                try:
                    return decode(cached_bytes)
                except Exception:
                    pass
            return None

        headers = {}
        if cached_bytes is not None and meta is not None:
            if meta[2]:
                headers["If-None-Match"] = meta[2]
            if meta[3]:
                headers["If-Modified-Since"] = meta[3]

        try:
            response = self._session().get(
                self.tile_url(server, zoom, x, y),
                headers=headers, timeout=(3.5, 10))

            if response.status_code == 304 and cached_bytes is not None:
                self._refresh_disk_meta(
                    db_cursor, server, zoom, x, y, meta, response.headers)
                self._failed_tile_until.pop(fail_key, None)
                return decode(cached_bytes)

            if response.status_code == 404:
                self._failed_tile_until[fail_key] = (
                    now + self.NETWORK_RETRY_DELAY_S)
                return None

            response.raise_for_status()
            image_bytes = response.content

            # Validate before committing bad HTML/error bodies into SQLite.
            image = decode(image_bytes)
            self._write_disk_tile(
                db_cursor, server, zoom, x, y, image_bytes, response.headers,
                max_zoom)
            self._failed_tile_until.pop(fail_key, None)
            return image

        except (requests.RequestException, UnidentifiedImageError,
                OSError, ValueError):
            self._failed_tile_until[fail_key] = (
                now + self.NETWORK_RETRY_DELAY_S)
            # A stale cached tile is much better than a blank map while the
            # network/server is temporarily unavailable.
            if cached_bytes is not None:
                try:
                    return decode(cached_bytes)
                except Exception:
                    pass
            return None
