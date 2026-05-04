# core/config_store.py
# Persistent storage of provider configurations (non-sensitive data)

import copy
import json
import logging
import os
import tempfile
import threading
import uuid
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


def _default_config_path() -> str:
    """Returns the path to the plugin config directory."""
    # Prefer FreeCAD user config dir if available
    try:
        import FreeCAD
        user_dir = FreeCAD.getUserAppDataDir()
    except ImportError:
        user_dir = os.path.expanduser("~")

    plugin_dir = os.path.join(user_dir, "CloudBrowser")
    os.makedirs(plugin_dir, exist_ok=True)
    return os.path.join(plugin_dir, "config.json")


class ConfigStore:
    """
    JSON-based persistence for provider account configurations.

    Schema:
    {
        "accounts": {
            "<account_id>": {
                "provider_type": "google_drive",
                "name": "My Google Drive",
                ... provider-specific non-sensitive fields ...
            },
            ...
        },
        "settings": {
            "show_all_files": false,
            "cache_dir": "/tmp/freecad_cloud_cache"
        }
    }
    """

    def __init__(self, config_path: Optional[str] = None):
        self._path = config_path or _default_config_path()
        self._lock = threading.RLock()
        self._data = self._load()

    @property
    def config_dir(self) -> str:
        """Return the directory that contains the config file."""
        return os.path.dirname(self._path)

    # ------------------------------------------------------------------
    # Internal I/O
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError as e:
                logger.error(
                    "Config file at %s is corrupted (JSON parse error: %s). "
                    "Starting with an empty configuration. "
                    "The original file has been left untouched for manual recovery.",
                    self._path, e,
                )
            except OSError as e:
                logger.error("Could not read config file %s: %s", self._path, e)
        return {"accounts": {}, "settings": {}}

    def _save(self):
        # Write to a temp file then rename atomically to prevent a corrupt config
        # on disk-full or process crash mid-write.
        dir_name = os.path.dirname(self._path)
        try:
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, self._path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as e:
            logger.error("Failed to save Cloud Browser config to %s: %s", self._path, e)
            # A-4 fix: re-raise the *original* exception unchanged so that callers
            # catching specific subclasses (PermissionError, FileNotFoundError, …)
            # still receive the correct type.
            raise

    # ------------------------------------------------------------------
    # Account API
    # ------------------------------------------------------------------

    def list_accounts(self) -> List[Dict[str, Any]]:
        """Return all accounts as a list of dicts with 'id' included."""
        with self._lock:
            return [
                {"id": aid, **copy.deepcopy(adata)}
                for aid, adata in self._data["accounts"].items()
            ]

    def get_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._data["accounts"].get(account_id)
            return copy.deepcopy(entry) if entry is not None else None

    def save_account(self, account_id: str, data: Dict[str, Any]):
        """Create or update an account entry."""
        with self._lock:
            self._data["accounts"][account_id] = data
            self._save()

    def add_account(self, provider_type: str, name: str, data: Dict[str, Any]) -> str:
        """Create a new account and return its generated ID."""
        account_id = str(uuid.uuid4())
        entry = {"provider_type": provider_type, "name": name, **data}
        self.save_account(account_id, entry)
        return account_id

    def delete_account(self, account_id: str):
        with self._lock:
            self._data["accounts"].pop(account_id, None)
            self._save()

    # ------------------------------------------------------------------
    # Settings API
    # ------------------------------------------------------------------

    def get_setting(self, key: str, default=None):
        with self._lock:
            return self._data["settings"].get(key, default)

    def set_setting(self, key: str, value):
        with self._lock:
            self._data["settings"][key] = value
            self._save()
