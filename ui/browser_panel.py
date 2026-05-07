# ui/browser_panel.py
# Main Cloud Browser dialog

import os
import logging
from dataclasses import replace
from typing import Optional
import FreeCAD
import FreeCADGui
from PySide6 import QtWidgets, QtCore
from PySide6.QtGui import QPalette

from core import get_config_store, get_auth_manager, get_file_cache, get_sync_manager
from providers import create_provider, PROVIDER_DISPLAY_NAMES

logger = logging.getLogger(__name__)


class DownloadWorker(QtCore.QThread):
    progress = QtCore.Signal(str)
    finished = QtCore.Signal(str)
    error = QtCore.Signal(str)

    def __init__(self, provider, remote_item, local_path, parent=None):
        super().__init__(parent)
        self._provider = provider
        self._item = remote_item
        self._local_path = local_path

    def run(self):
        try:
            self.progress.emit(f"Downloading {self._item.name}...")
            result = self._provider.download_file(self._item, self._local_path)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class UploadWorker(QtCore.QThread):
    progress = QtCore.Signal(str)
    finished = QtCore.Signal()
    error = QtCore.Signal(str)

    def __init__(self, provider, local_path, remote_dir, parent=None):
        super().__init__(parent)
        self._provider = provider
        self._local_path = local_path
        self._remote_dir = remote_dir

    def run(self):
        try:
            filename = os.path.basename(self._local_path)
            self.progress.emit(f"Uploading {filename}...")
            self._provider.upload_file(self._local_path, self._remote_dir)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class ListDirectoryWorker(QtCore.QThread):
    """Runs list_directory in a background thread to avoid freezing the UI."""
    finished = QtCore.Signal(list)
    error = QtCore.Signal(str)

    def __init__(self, provider, path, parent=None):
        super().__init__(parent)
        self._provider = provider
        self._path = path
        self._cancelled = False

    def cancel(self):
        """Signal this worker to discard its result when done."""
        self._cancelled = True

    def run(self):
        try:
            items = self._provider.list_directory(self._path)
            if not self._cancelled:
                self.finished.emit(items)
        except Exception as e:
            if not self._cancelled:
                self.error.emit(str(e))


class CloudBrowserDialog(QtWidgets.QDialog):
    """Main Cloud Browser dialog window."""

    def __init__(self, parent=None):
        if parent is None:
            try:
                parent = FreeCADGui.getMainWindow()
            except Exception:
                pass
        super().__init__(parent)

        self._provider = None
        self._provider_id = None
        # NOTABLE-3 fix: store (path_id, display_name) tuples instead of raw IDs.
        # This lets the breadcrumb show human-readable folder names for providers
        # like Google Drive and OneDrive that use opaque item IDs as paths.
        self._path_stack = []   # List of (path_id: str, display_name: str)
        # Track all active workers to prevent premature garbage collection
        self._workers = []
        self._list_worker = None
        # PERF-1 fix: keep track of ALL list workers so we can wait on them at close
        self._all_list_workers = []
        # BP-1 fix: guard against re-entrant close (reject() → closeEvent() loop)
        self._closing = False

        self.setWindowTitle("Cloud Browser")
        self.setMinimumSize(560, 500)
        self._build_ui()
        self._refresh_account_list()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(6)

        # --- Account selector ---
        account_row = QtWidgets.QHBoxLayout()
        self._account_combo = QtWidgets.QComboBox()
        self._account_combo.setToolTip("Select a configured cloud account")
        self._account_combo.currentIndexChanged.connect(self._on_account_changed)

        add_btn = QtWidgets.QPushButton("Add Account")
        add_btn.setToolTip("Add a new cloud account")
        add_btn.clicked.connect(self._on_add_provider)

        manage_btn = QtWidgets.QPushButton("Manage")
        manage_btn.setToolTip("Manage cloud accounts")
        manage_btn.clicked.connect(self._on_manage_providers)

        account_row.addWidget(QtWidgets.QLabel("Account:"))
        account_row.addWidget(self._account_combo, 1)
        account_row.addWidget(add_btn)
        account_row.addWidget(manage_btn)
        layout.addLayout(account_row)

        # --- Path bar ---
        self._path_label = QtWidgets.QLabel("/")
        self._path_label.setProperty("class", "secondary-text")
        self._path_label.setWordWrap(True)
        layout.addWidget(self._path_label)

        # SEC-4 fix: persistent TLS warning banner (hidden by default)
        self._tls_warning_label = QtWidgets.QLabel(
            "⚠  SSL/TLS certificate verification is DISABLED for this connection. "
            "Data is transmitted without TLS validation."
        )
        self._tls_warning_label.setObjectName("tlsWarning")
        # Use palette ToolTipBase/ToolTipText roles for a warning-like appearance
        # that adapts to light and dark themes without hardcoded hex colors.
        pal = self._tls_warning_label.palette()
        app_pal = QtWidgets.QApplication.palette()
        pal.setColor(QPalette.ColorRole.Window,
                     app_pal.color(QPalette.ColorRole.ToolTipBase))
        pal.setColor(QPalette.ColorRole.WindowText,
                     app_pal.color(QPalette.ColorRole.ToolTipText))
        self._tls_warning_label.setAutoFillBackground(True)
        self._tls_warning_label.setPalette(pal)
        self._tls_warning_label.setContentsMargins(6, 4, 6, 4)
        self._tls_warning_label.setWordWrap(True)
        self._tls_warning_label.setVisible(False)
        layout.addWidget(self._tls_warning_label)

        # --- Toolbar ---
        toolbar = QtWidgets.QHBoxLayout()

        self._back_btn = QtWidgets.QPushButton("< Back")
        self._back_btn.setEnabled(False)
        self._back_btn.clicked.connect(self._on_back)

        self._refresh_btn = QtWidgets.QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._on_refresh)

        self._new_folder_btn = QtWidgets.QPushButton("New Folder")
        self._new_folder_btn.setEnabled(False)
        self._new_folder_btn.setToolTip("Create a new folder in the current directory")
        self._new_folder_btn.clicked.connect(self._on_new_folder)

        self._upload_btn = QtWidgets.QPushButton("Upload File")
        self._upload_btn.setEnabled(False)
        self._upload_btn.setToolTip("Upload a local file to the current directory")
        self._upload_btn.clicked.connect(self._on_upload_file)

        self._filter_edit = QtWidgets.QLineEdit()
        self._filter_edit.setPlaceholderText("Filter...")
        self._filter_edit.setMaximumWidth(140)
        self._filter_edit.textChanged.connect(self._apply_filter)

        toolbar.addWidget(self._back_btn)
        toolbar.addWidget(self._refresh_btn)
        toolbar.addWidget(self._new_folder_btn)
        toolbar.addWidget(self._upload_btn)
        toolbar.addStretch()
        toolbar.addWidget(QtWidgets.QLabel("Filter:"))
        toolbar.addWidget(self._filter_edit)
        layout.addLayout(toolbar)

        # --- File list (wrapped in a stack for empty-state overlay) ---
        self._file_list = QtWidgets.QTreeWidget()
        self._file_list.setHeaderLabels(["Name", "Size", "Modified"])
        self._file_list.setColumnWidth(0, 260)
        self._file_list.setColumnWidth(1, 80)
        self._file_list.setAlternatingRowColors(True)
        self._file_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self._file_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._file_list.itemSelectionChanged.connect(self._on_selection_changed)
        # Context menu
        self._file_list.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self._file_list.customContextMenuRequested.connect(self._on_context_menu)

        self._empty_label = QtWidgets.QLabel(
            "No accounts configured.\nClick \"Add Account\" to connect your first cloud storage."
        )
        self._empty_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)

        self._list_stack = QtWidgets.QStackedWidget()
        self._list_stack.addWidget(self._file_list)   # index 0
        self._list_stack.addWidget(self._empty_label) # index 1
        self._list_stack.setCurrentIndex(1)           # start with empty-state
        layout.addWidget(self._list_stack)

        # --- Status / progress ---
        self._status_label = QtWidgets.QLabel("")
        self._status_label.setProperty("class", "secondary-text")
        layout.addWidget(self._status_label)

        self._progress_bar = QtWidgets.QProgressBar()
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        # --- Bottom buttons ---
        btn_row = QtWidgets.QHBoxLayout()
        self._open_btn = QtWidgets.QPushButton("Open in FreeCAD")
        self._open_btn.setEnabled(False)
        self._open_btn.setDefault(True)
        self._open_btn.clicked.connect(self._on_open_file)

        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.reject)

        btn_row.addWidget(self._open_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Close event — cleanup (PERF-1 + disconnect fix)
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        """
        Ensure all background threads are waited on and the active provider
        connection is closed before the dialog is destroyed.
        """
        # BP-1 fix: guard against re-entrant calls (reject() → closeEvent() →
        # super().reject() → QDialog::close() → closeEvent() again).
        if self._closing:
            super().closeEvent(event)
            return
        self._closing = True

        # Cancel the current listing worker
        if self._list_worker and self._list_worker.isRunning():
            self._list_worker.cancel()

        # Wait for all list workers (PERF-1 fix: prevents dangling threads)
        for w in list(self._all_list_workers):
            if w.isRunning():
                w.wait(3000)  # wait up to 3 s; don't block forever

        # Wait for all download/upload workers
        for w in list(self._workers):
            if w.isRunning():
                w.wait(3000)

        # Explicitly disconnect the provider (releases FTP/SFTP TCP connections)
        if self._provider is not None and hasattr(self._provider, "disconnect"):
            try:
                self._provider.disconnect()
            except Exception:
                pass

        super().closeEvent(event)

    def reject(self):
        # Call super().reject() so the modal event loop (exec()) is properly
        # terminated. super().reject() calls done(Rejected) which exits the
        # exec() loop; it will also trigger closeEvent() once, and the
        # _closing guard prevents re-entrance from that path.
        super().reject()

    # ------------------------------------------------------------------
    # Account management
    # ------------------------------------------------------------------

    def _refresh_account_list(self):
        store = get_config_store()
        self._accounts = store.list_accounts()
        self._account_combo.blockSignals(True)
        self._account_combo.clear()
        if not self._accounts:
            self._account_combo.addItem("-- No accounts configured --")
            self._empty_label.setText(
                "No accounts configured.\n"
                "Click \"Add Account\" to connect your first cloud storage."
            )
            self._list_stack.setCurrentIndex(1)
        else:
            for acc in self._accounts:
                ptype = acc.get("provider_type", "")
                label = f"{acc.get('name', 'Unnamed')} ({PROVIDER_DISPLAY_NAMES.get(ptype, ptype)})"
                self._account_combo.addItem(label)
            self._list_stack.setCurrentIndex(0)
        self._account_combo.blockSignals(False)
        self._account_combo.setCurrentIndex(0)
        self._on_account_changed(0)

    def _on_account_changed(self, index: int):
        # Disconnect any active provider (explicit FTP disconnect to free the connection)
        if self._provider is not None and hasattr(self._provider, "disconnect"):
            self._provider.disconnect()
        self._provider = None
        self._path_stack.clear()
        self._file_list.clear()
        self._path_label.setText("/")
        self._tls_warning_label.setVisible(False)
        self._back_btn.setEnabled(False)
        self._open_btn.setEnabled(False)
        self._new_folder_btn.setEnabled(False)
        self._upload_btn.setEnabled(False)

        if not self._accounts:
            return

        # BUG-3 fix: guard against out-of-range index (race condition or empty list)
        if index < 0 or index >= len(self._accounts):
            return

        acc = self._accounts[index]
        self._provider_id = acc["id"]
        auth_mgr = get_auth_manager()
        full_config = auth_mgr.load_credentials(self._provider_id)
        # Pass account id via a private key so providers (e.g. OneDrive) can
        # persist the token cache immediately after authenticate().
        # The leading underscore marks it as an internal key that must be
        # stripped before writing back to the credential store.
        full_config["_account_id"] = self._provider_id

        # SEC-4 fix: show TLS warning banner for WebDAV connections with verify_ssl=False
        if acc.get("provider_type") == "webdav" and not full_config.get("verify_ssl", True):
            self._tls_warning_label.setVisible(True)

        try:
            self._provider = create_provider(acc["provider_type"], full_config)
            self._load_directory()
        except Exception as e:
            self._set_status(f"Error: {e}", error=True)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _current_remote_dir(self) -> str:
        """Return the path ID of the current directory (top of stack)."""
        return self._path_stack[-1][0] if self._path_stack else ""

    def _load_directory(self, path: Optional[str] = None):
        """List directory in a background QThread to avoid freezing the UI."""
        if self._provider is None:
            return

        if path is None:
            path = self._current_remote_dir()

        # Cancel any previous listing so its result is discarded even if
        # the underlying network call cannot be interrupted.
        if self._list_worker and self._list_worker.isRunning():
            self._list_worker.cancel()
            try:
                self._list_worker.finished.disconnect()
                self._list_worker.error.disconnect()
            except RuntimeError:
                pass  # Already disconnected

        self._set_status("Loading...", busy=True)
        self._file_list.clear()
        self._list_stack.setCurrentIndex(0)  # show tree (spinner in status bar) while loading
        self._refresh_btn.setEnabled(False)

        self._list_worker = ListDirectoryWorker(self._provider, path)
        # PERF-1 fix: track all list workers
        self._all_list_workers.append(self._list_worker)
        self._list_worker.finished.connect(self._on_directory_loaded)
        self._list_worker.error.connect(self._on_directory_error)
        # Remove from tracking list when done
        self._list_worker.finished.connect(
            lambda _items, w=self._list_worker: self._remove_list_worker(w)
        )
        self._list_worker.error.connect(
            lambda _err, w=self._list_worker: self._remove_list_worker(w)
        )
        self._list_worker.start()

    def _remove_list_worker(self, worker):
        try:
            self._all_list_workers.remove(worker)
        except ValueError:
            pass
        # Also purge any other finished workers that may have been missed
        # (e.g. cancelled before emitting a signal).
        self._all_list_workers = [w for w in self._all_list_workers if w.isRunning()]

    def _on_directory_loaded(self, items):
        self._refresh_btn.setEnabled(True)
        # Persist any OAuth tokens that may have been refreshed or generated
        # during the list_directory call (e.g. Google Drive token refresh).
        # Strip internal bookkeeping keys (prefixed with '_') before persisting
        # so they don't accumulate as noise in the credential store.
        if self._provider is not None and self._provider_id is not None:
            try:
                auth_mgr = get_auth_manager()
                clean_config = {
                    k: v for k, v in self._provider.config.items()
                    if not k.startswith("_")
                }
                auth_mgr.save_credentials(self._provider_id, clean_config)
            except Exception as e:
                logger.warning(
                    "Failed to persist updated credentials for account %s: %s",
                    self._provider_id, e,
                )
        items.sort(key=lambda x: (not x.is_dir, x.name.lower()))

        style = self.style()
        for item in items:
            tree_item = QtWidgets.QTreeWidgetItem()
            tree_item.setText(0, item.name)
            tree_item.setText(1, self._format_size(item.size) if item.size else "")
            # Guard against unexpected None or non-string modified values
            modified_str = item.modified or ""
            tree_item.setText(2, modified_str[:10] if modified_str else "")
            tree_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, item)
            if item.is_dir:
                tree_item.setIcon(0, style.standardIcon(
                    QtWidgets.QStyle.StandardPixmap.SP_DirIcon))
            else:
                tree_item.setIcon(0, style.standardIcon(
                    QtWidgets.QStyle.StandardPixmap.SP_FileIcon))
            self._file_list.addTopLevelItem(tree_item)

        self._update_path_label()
        self._new_folder_btn.setEnabled(True)
        self._upload_btn.setEnabled(True)
        if items:
            self._list_stack.setCurrentIndex(0)
            self._set_status(f"{len(items)} items")
        else:
            self._empty_label.setText("This folder is empty.")
            self._list_stack.setCurrentIndex(1)
            self._set_status("0 items")

    def _on_directory_error(self, error_msg: str):
        self._refresh_btn.setEnabled(True)
        self._set_status(f"Error: {error_msg}", error=True)

    def _on_back(self):
        if self._path_stack:
            self._path_stack.pop()
        self._back_btn.setEnabled(len(self._path_stack) > 0)
        self._load_directory(self._current_remote_dir())

    def _on_refresh(self):
        self._load_directory(self._current_remote_dir())

    def _on_item_double_clicked(self, tree_item, _column):
        remote_item = tree_item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if remote_item is None:
            return
        if remote_item.is_dir:
            # NOTABLE-3 fix: push (path_id, display_name) tuple so the breadcrumb
            # can show the folder name rather than its opaque ID.
            self._path_stack.append((remote_item.path, remote_item.name))
            self._back_btn.setEnabled(True)
            self._load_directory(remote_item.path)
        else:
            self._open_remote_file(remote_item)

    def _on_selection_changed(self):
        selected = self._file_list.selectedItems()
        if not selected:
            self._open_btn.setEnabled(False)
            return
        remote_item = selected[0].data(0, QtCore.Qt.ItemDataRole.UserRole)
        self._open_btn.setEnabled(bool(remote_item and not remote_item.is_dir))

    def _on_open_file(self):
        selected = self._file_list.selectedItems()
        if not selected:
            return
        remote_item = selected[0].data(0, QtCore.Qt.ItemDataRole.UserRole)
        if remote_item and not remote_item.is_dir:
            self._open_remote_file(remote_item)

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _on_context_menu(self, pos: QtCore.QPoint):
        if self._provider is None:
            return

        item = self._file_list.itemAt(pos)
        remote_item = item.data(0, QtCore.Qt.ItemDataRole.UserRole) if item else None

        menu = QtWidgets.QMenu(self)

        if remote_item and not remote_item.is_dir:
            action_open = menu.addAction("Open in FreeCAD")
            action_open.triggered.connect(lambda: self._open_remote_file(remote_item))
            menu.addSeparator()

        action_upload = menu.addAction("Upload File Here")
        action_upload.triggered.connect(self._on_upload_file)

        action_folder = menu.addAction("New Folder Here")
        action_folder.triggered.connect(self._on_new_folder)

        if remote_item:
            menu.addSeparator()
            action_delete = menu.addAction("Delete")
            action_delete.triggered.connect(lambda: self._on_delete_item(remote_item))

        menu.exec(self._file_list.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # New Folder
    # ------------------------------------------------------------------

    def _on_new_folder(self):
        if self._provider is None:
            return

        name, ok = QtWidgets.QInputDialog.getText(
            self, "New Folder", "Folder name:"
        )
        if not ok or not name.strip():
            return
        name = name.strip()

        try:
            self._provider.create_folder(self._current_remote_dir(), name)
            self._set_status(f"Folder '{name}' created")
            self._load_directory(self._current_remote_dir())
        except NotImplementedError:
            QtWidgets.QMessageBox.warning(
                self, "Not Supported",
                "This provider does not support folder creation."
            )
        except Exception as e:
            self._set_status(f"Error creating folder: {e}", error=True)

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def _on_upload_file(self):
        if self._provider is None:
            return

        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Select file(s) to upload"
        )
        if not paths:
            return

        for local_path in paths:
            self._start_upload(local_path)

    def _start_upload(self, local_path: str):
        remote_dir = self._current_remote_dir()
        worker = UploadWorker(self._provider, local_path, remote_dir)
        worker.progress.connect(lambda msg: self._set_status(msg, busy=True))
        worker.finished.connect(self._on_upload_finished)
        worker.error.connect(lambda err: self._set_status(f"Upload error: {err}", error=True))
        # Track all workers to avoid premature GC
        worker.finished.connect(lambda: self._remove_worker(worker))
        worker.error.connect(lambda _: self._remove_worker(worker))
        self._workers.append(worker)
        worker.start()

    def _remove_worker(self, worker):
        """Remove a completed/errored worker from the tracking list."""
        try:
            self._workers.remove(worker)
        except ValueError:
            pass

    def _on_upload_finished(self):
        self._set_status("Upload complete")
        self._load_directory(self._current_remote_dir())

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def _on_delete_item(self, remote_item):
        reply = QtWidgets.QMessageBox.question(
            self, "Confirm Delete",
            f"Delete '{remote_item.name}'?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            self._provider.delete_item(remote_item)
            self._set_status(f"Deleted '{remote_item.name}'")
            self._load_directory(self._current_remote_dir())
        except NotImplementedError:
            QtWidgets.QMessageBox.warning(
                self, "Not Supported",
                "This provider does not support deletion."
            )
        except Exception as e:
            self._set_status(f"Error deleting: {e}", error=True)

    # ------------------------------------------------------------------
    # File opening (download + open)
    # ------------------------------------------------------------------

    def _open_remote_file(self, remote_item):
        if self._provider is None:
            return
        cache = get_file_cache()
        # Sanitize the filename to prevent path traversal attacks.
        safe_name = os.path.basename(remote_item.name)
        local_path = cache.get_local_path(
            self._provider.provider_type,
            remote_item.path,
            safe_name,
        )
        # The remote directory is the parent of the file path.
        remote_dir = self._current_remote_dir()

        if cache.is_cached(self._provider.provider_type, remote_item.path, safe_name,
                           remote_modified=remote_item.modified):
            self._open_in_freecad(local_path, remote_dir)
            return

        # Create a sanitized copy of the remote_item with the safe name
        safe_item = replace(remote_item, name=safe_name)

        worker = DownloadWorker(self._provider, safe_item, local_path, parent=self)
        worker.progress.connect(lambda msg: self._set_status(msg, busy=True))
        worker.finished.connect(lambda path: self._on_download_finished(path, remote_dir))
        worker.error.connect(lambda err: self._set_status(f"Download error: {err}", error=True))
        worker.finished.connect(lambda _: self._remove_worker(worker))
        worker.error.connect(lambda _: self._remove_worker(worker))
        self._workers.append(worker)
        worker.start()

    def _on_download_finished(self, local_path: str, remote_dir: str = ""):
        self._set_status("Download complete")
        self._open_in_freecad(local_path, remote_dir)

    def _open_in_freecad(self, local_path: str, remote_dir: str = ""):
        try:
            FreeCAD.openDocument(local_path)
            # Register the file with the sync manager so that saves are
            # automatically mirrored back to the cloud.
            get_sync_manager().register(local_path, self._provider, remote_dir)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, "Open Error", f"Failed to open file in FreeCAD:\n{e}"
            )

    # ------------------------------------------------------------------
    # Filter
    # ------------------------------------------------------------------

    def _apply_filter(self, text: str):
        text = text.lower()
        for i in range(self._file_list.topLevelItemCount()):
            item = self._file_list.topLevelItem(i)
            item.setHidden(text not in item.text(0).lower())

    # ------------------------------------------------------------------
    # Provider dialogs
    # ------------------------------------------------------------------

    def _on_add_provider(self):
        from ui.provider_dialog import ProviderDialog
        dlg = ProviderDialog(parent=self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._refresh_account_list()

    def _on_manage_providers(self):
        from ui.provider_dialog import ManageProvidersDialog
        dlg = ManageProvidersDialog(parent=self)
        dlg.exec()
        self._refresh_account_list()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_path_label(self):
        if not self._path_stack:
            self._path_label.setText("/")
        else:
            # NOTABLE-3 fix: show the display_name part of each (path_id, display_name) tuple
            self._path_label.setText(" / ".join(name for _, name in self._path_stack))

    def _set_status(self, message: str, busy: bool = False, error: bool = False):
        self._status_label.setText(message)
        palette = self._status_label.palette()
        app_palette = QtWidgets.QApplication.palette()
        if error:
            # Use Link color role — visible on both light and dark themes.
            color = app_palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Link)
        else:
            color = app_palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText)
        palette.setColor(QPalette.ColorRole.WindowText, color)
        self._status_label.setPalette(palette)
        self._progress_bar.setVisible(busy)

    @staticmethod
    def _format_size(size_bytes) -> str:
        if size_bytes is None or size_bytes < 0:
            return ""
        # M-2 fix: include PB so that files >1 TB are not shown as "NNNN.N TB".
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size_bytes < 1024:
                return f"{size_bytes:.0f} {unit}" if unit == "B" else f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} PB"
