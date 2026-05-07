# core/__init__.py
import threading

from .config_store import ConfigStore
from .auth_manager import AuthManager
from .file_cache import FileCache
from .sync_manager import SyncManager

# Singletons shared across the plugin session.
# Protected by an RLock (re-entrant lock) to ensure thread-safe lazy
# initialisation even when accessor functions call each other while already
# holding the lock (e.g. get_file_cache -> get_config_store).
_config_store = None
_auth_manager = None
_file_cache = None
_lock = threading.RLock()


def get_config_store() -> ConfigStore:
    global _config_store
    with _lock:
        if _config_store is None:
            _config_store = ConfigStore()
    return _config_store


def get_auth_manager() -> AuthManager:
    global _auth_manager
    with _lock:
        if _auth_manager is None:
            _auth_manager = AuthManager(get_config_store())
    return _auth_manager


def get_file_cache() -> FileCache:
    global _file_cache
    with _lock:
        if _file_cache is None:
            store = get_config_store()
            cache_dir = store.get_setting("cache_dir")
            _file_cache = FileCache(cache_dir)
    return _file_cache


def get_sync_manager() -> SyncManager:
    """Returns the module-level SyncManager singleton."""
    return SyncManager.instance()
