# core/sync_manager.py
# Automatic cloud sync-back on FreeCAD document save.
#
# When a file is opened from the cloud browser, it is registered here with its
# provider and remote directory. A FreeCAD DocumentObserver fires on every
# save; if the saved document matches a registered file the sync manager
# uploads it back to the cloud in a background thread.
#
# The sync manager is a module-level singleton so it survives the panel being
# closed and re-opened within the same FreeCAD session.

import logging
import os
import threading

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Background upload thread (no Qt dependency — safe to import anywhere)
# ---------------------------------------------------------------------------

class _SyncUploadThread(threading.Thread):
    """Uploads a file to the cloud in a daemon thread."""

    def __init__(self, provider, local_path: str, remote_dir: str, on_done=None, on_error=None):
        super().__init__(daemon=True)
        self._provider = provider
        self._local_path = local_path
        self._remote_dir = remote_dir
        self._on_done = on_done
        self._on_error = on_error

    def run(self):
        try:
            filename = os.path.basename(self._local_path)
            logger.info("SyncManager: uploading %s → %s", filename, self._remote_dir)
            self._provider.upload_file(self._local_path, self._remote_dir)
            logger.info("SyncManager: upload complete for %s", filename)
            if self._on_done:
                self._on_done(filename)
        except Exception as exc:
            logger.error("SyncManager: upload failed for %s: %s", self._local_path, exc)
            if self._on_error:
                self._on_error(os.path.basename(self._local_path), str(exc))


# ---------------------------------------------------------------------------
# DocumentObserver
# ---------------------------------------------------------------------------

class _CloudDocumentObserver:
    """
    FreeCAD DocumentObserver that triggers a cloud upload whenever a tracked
    document is saved.
    """

    def __init__(self, sync_manager):
        self._mgr = sync_manager

    def slotSaveDocument(self, doc):
        """Called by FreeCAD after a document has been saved to disk."""
        try:
            local_path = doc.FileName
        except Exception:
            return
        if not local_path:
            return
        self._mgr._on_document_saved(local_path)


# ---------------------------------------------------------------------------
# SyncManager singleton
# ---------------------------------------------------------------------------

class SyncManager:
    """
    Singleton that tracks open cloud files and re-uploads them on save.

    Registration:
        SyncManager.instance().register(local_path, provider, remote_dir)

    Unregistration happens automatically when the file no longer exists or
    can be triggered manually:
        SyncManager.instance().unregister(local_path)
    """

    _instance = None
    _lock = threading.Lock()

    @classmethod
    def instance(cls) -> "SyncManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    def __init__(self):
        # local_path (str) → {"provider": ..., "remote_dir": str}
        self._tracked: dict = {}
        self._tracked_lock = threading.Lock()
        self._observer = _CloudDocumentObserver(self)
        self._observer_registered = False
        self._active_threads: list = []

    # ------------------------------------------------------------------
    # Observer lifecycle
    # ------------------------------------------------------------------

    def _ensure_observer(self):
        if self._observer_registered:
            return
        try:
            import FreeCAD
            FreeCAD.addDocumentObserver(self._observer)
            self._observer_registered = True
            logger.debug("SyncManager: DocumentObserver registered")
        except Exception as exc:
            logger.warning("SyncManager: could not register DocumentObserver: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, local_path: str, provider, remote_dir: str):
        """
        Track a locally-cached file so that saves are mirrored to the cloud.

        :param local_path:  Absolute path of the local cache file.
        :param provider:    Authenticated provider instance (S3Provider, etc.)
        :param remote_dir:  Remote directory to upload back to (e.g. "designs/").
        """
        self._ensure_observer()
        with self._tracked_lock:
            self._tracked[os.path.normpath(local_path)] = {
                "provider": provider,
                "remote_dir": remote_dir,
            }
        logger.info(
            "SyncManager: registered %s → %s [%s]",
            os.path.basename(local_path), remote_dir, provider.provider_type,
        )

    def unregister(self, local_path: str):
        """Stop tracking a file."""
        key = os.path.normpath(local_path)
        with self._tracked_lock:
            self._tracked.pop(key, None)
        logger.debug("SyncManager: unregistered %s", key)

    def is_tracked(self, local_path: str) -> bool:
        return os.path.normpath(local_path) in self._tracked

    def tracked_count(self) -> int:
        return len(self._tracked)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_document_saved(self, local_path: str):
        key = os.path.normpath(local_path)
        with self._tracked_lock:
            entry = self._tracked.get(key)
        if entry is None:
            return

        provider = entry["provider"]
        remote_dir = entry["remote_dir"]

        def _log_done(filename):
            try:
                import FreeCAD
                FreeCAD.Console.PrintMessage(
                    f"[Cloud Browser] ✓ {filename} synced to {provider.provider_type.upper()}\n"
                )
            except Exception:
                pass

        def _log_error(filename, err):
            try:
                import FreeCAD
                FreeCAD.Console.PrintError(
                    f"[Cloud Browser] ✗ Failed to sync {filename}: {err}\n"
                )
            except Exception:
                pass

        t = _SyncUploadThread(provider, local_path, remote_dir, on_done=_log_done, on_error=_log_error)
        # Keep a reference to avoid GC while thread is running
        self._active_threads = [x for x in self._active_threads if x.is_alive()]
        self._active_threads.append(t)
        t.start()
        logger.info("SyncManager: sync triggered for %s", os.path.basename(local_path))
