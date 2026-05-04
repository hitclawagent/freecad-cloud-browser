# core/file_cache.py
# Temporary local cache for downloaded remote files

import logging
import os
import hashlib
import tempfile
import shutil
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _default_cache_dir() -> str:
    try:
        import FreeCAD
        user_dir = FreeCAD.getUserAppDataDir()
    except ImportError:
        user_dir = tempfile.gettempdir()
    cache_dir = os.path.join(user_dir, "CloudBrowser", "cache")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


class FileCache:
    """
    Local disk cache for files downloaded from cloud storage.

    Each file is stored under:
        <cache_dir>/<provider_type>/<safe_hash>/filename

    The hash is computed from (provider_type + remote_path) so the same
    remote path always maps to the same cache location, enabling reuse.
    """

    def __init__(self, cache_dir: Optional[str] = None):
        self._root = cache_dir or _default_cache_dir()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_local_path(self, provider_type: str, remote_path: str, filename: str) -> str:
        """
        Return the expected local cache path WITHOUT creating any directories.
        Use this for read-only checks (PERF-3 fix).
        """
        key = self._cache_key(provider_type, remote_path)
        dir_path = os.path.join(self._root, provider_type, key)
        return os.path.join(dir_path, filename)

    def get_local_path(self, provider_type: str, remote_path: str, filename: str) -> str:
        """
        Returns the expected local cache path for a remote file, creating the
        cache directory if it does not already exist.
        The file itself may or may not exist yet.
        """
        key = self._cache_key(provider_type, remote_path)
        dir_path = os.path.join(self._root, provider_type, key)
        os.makedirs(dir_path, exist_ok=True)
        return os.path.join(dir_path, filename)

    def is_cached(self, provider_type: str, remote_path: str, filename: str,
                  remote_modified: str = None) -> bool:
        """
        Returns True if the file is already cached locally and is still fresh.

        PERF-3 fix: uses _resolve_local_path (no makedirs) instead of
        get_local_path so that a cache hit doesn't incur an unnecessary
        filesystem syscall.

        If `remote_modified` is provided (ISO-8601 string or similar), the cache
        entry is considered stale when the local file is older than the remote
        modification time.  If `remote_modified` is None the check falls back to
        existence-only (legacy behaviour).
        """
        path = self._resolve_local_path(provider_type, remote_path, filename)
        if not os.path.isfile(path):
            return False
        if remote_modified:
            try:
                # Normalise both timestamps to UTC epoch seconds for comparison.
                # Handles:
                #   ISO-8601 with Z suffix:          2024-01-15T10:00:00Z
                #   ISO-8601 with positive offset:   2024-01-15T10:00:00+02:00
                #   ISO-8601 with negative offset:   2024-01-15T10:00:00-05:00
                #   ISO-8601 without timezone:       2024-01-15T10:00:00 (assumed UTC)
                #   Unix timestamp as string:        1705316400 or 1705316400.0
                remote_str = remote_modified.strip()
                # Try parsing as Unix timestamp first (plain integer or float)
                try:
                    unix_ts = float(remote_str)
                    remote_dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc)
                except ValueError:
                    # ISO-8601 parsing: replace trailing Z with +00:00 so
                    # fromisoformat handles it on Python < 3.11 as well.
                    iso_str = remote_str
                    if iso_str.endswith("Z"):
                        iso_str = iso_str[:-1] + "+00:00"
                    remote_dt = datetime.fromisoformat(iso_str)
                    # Attach UTC if no timezone info is present
                    if remote_dt.tzinfo is None:
                        remote_dt = remote_dt.replace(tzinfo=timezone.utc)
                    else:
                        remote_dt = remote_dt.astimezone(timezone.utc)
                local_mtime = os.path.getmtime(path)
                local_dt = datetime.fromtimestamp(local_mtime, tz=timezone.utc)
                if remote_dt > local_dt:
                    return False  # Remote file is newer — cache is stale
            except Exception as exc:
                # If we cannot parse the timestamp, keep the cached file
                # but log a debug message to aid troubleshooting.
                logger.debug(
                    "Could not parse remote_modified %r for cache check: %s",
                    remote_modified, exc,
                )
        return True

    def invalidate(self, provider_type: str, remote_path: str, filename: str):
        """Remove a specific file from cache."""
        path = self._resolve_local_path(provider_type, remote_path, filename)
        if os.path.isfile(path):
            os.remove(path)

    def clear_provider(self, provider_type: str):
        """Remove all cached files for a given provider."""
        dir_path = os.path.join(self._root, provider_type)
        if os.path.isdir(dir_path):
            shutil.rmtree(dir_path)

    def clear_all(self):
        """Remove all cached files."""
        if os.path.isdir(self._root):
            shutil.rmtree(self._root)
        os.makedirs(self._root, exist_ok=True)

    def cache_size_bytes(self) -> int:
        """Return total size of the cache in bytes."""
        total = 0
        for dirpath, _, filenames in os.walk(self._root):
            for f in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
        return total

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _cache_key(provider_type: str, remote_path: str) -> str:
        """Create a short deterministic hash key from the remote path."""
        raw = f"{provider_type}::{remote_path}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:20]
