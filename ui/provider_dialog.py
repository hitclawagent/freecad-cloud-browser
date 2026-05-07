# ui/provider_dialog.py
# Dialogs for adding and managing cloud provider accounts

import os
from PySide6 import QtWidgets, QtCore
from PySide6.QtGui import QPalette

from providers import PROVIDER_REGISTRY, PROVIDER_DISPLAY_NAMES, PROVIDER_COMING_SOON, create_provider
from core import get_config_store, get_auth_manager


class _AuthWorker(QtCore.QThread):
    """Runs provider.authenticate() in a background thread to avoid freezing the UI."""
    succeeded = QtCore.Signal(object)   # emits the authenticated provider
    failed = QtCore.Signal(str)

    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self._provider = provider

    def run(self):
        try:
            self._provider.authenticate()
            self.succeeded.emit(self._provider)
        except Exception as e:
            self.failed.emit(str(e))


class ProviderDialog(QtWidgets.QDialog):
    """Dialog to add a new cloud provider account."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Cloud Account")
        self.setMinimumWidth(440)
        self._field_widgets = {}
        self._required_keys = set()
        # key → label mapping for user-friendly error messages
        self._field_labels = {}
        # M-8 fix: separate dict for "file" field line edits (avoids setting
        # dynamic attributes on QWidget instances which is fragile with Shiboken).
        self._file_line_edits = {}
        self._auth_worker = None
        self._tested_provider = None  # provider instance after successful test
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        # Provider type selector
        type_row = QtWidgets.QFormLayout()
        self._type_combo = QtWidgets.QComboBox()
        for ptype, pname in PROVIDER_DISPLAY_NAMES.items():
            if ptype in PROVIDER_COMING_SOON:
                self._type_combo.addItem(f"{pname}  (coming soon)", ptype)
                # Make the item non-selectable and visually grayed-out
                idx = self._type_combo.count() - 1
                item_model = self._type_combo.model()
                item = item_model.item(idx)
                item.setEnabled(False)
                item.setForeground(self.palette().color(QPalette.ColorRole.PlaceholderText))
            else:
                self._type_combo.addItem(pname, ptype)
        self._type_combo.currentIndexChanged.connect(self._on_provider_changed)
        type_row.addRow("Provider Type:", self._type_combo)

        self._name_edit = QtWidgets.QLineEdit()
        self._name_edit.setPlaceholderText("e.g. My Google Drive")
        type_row.addRow("Account Name:", self._name_edit)
        layout.addLayout(type_row)

        # Dynamic fields container
        self._fields_group = QtWidgets.QGroupBox("Connection Settings")
        self._fields_layout = QtWidgets.QFormLayout()
        self._fields_group.setLayout(self._fields_layout)
        layout.addWidget(self._fields_group)

        # Status label
        self._status_label = QtWidgets.QLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        # Buttons
        btn_box = QtWidgets.QDialogButtonBox()
        self._test_btn = btn_box.addButton("Test Connection", QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)
        self._save_btn = btn_box.addButton("Save", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_btn = btn_box.addButton("Cancel", QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        self._test_btn.clicked.connect(self._on_test)
        self._save_btn.clicked.connect(self._on_save)
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(btn_box)

        # Populate initial fields
        self._on_provider_changed(0)

    def _on_provider_changed(self, _index: int):
        # Clear existing dynamic fields
        while self._fields_layout.count():
            item = self._fields_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._field_widgets.clear()
        self._field_labels.clear()
        self._file_line_edits.clear()
        self._required_keys = set()
        self._tested_provider = None

        ptype = self._type_combo.currentData()
        cls = PROVIDER_REGISTRY.get(ptype)
        if not cls:
            return

        # NOTABLE-5 fix: call get_config_schema() as a classmethod so we don't
        # need to instantiate the provider with an empty config (which is fragile —
        # any provider accessing self.config["key"] in __init__ would crash).
        schema = cls.get_config_schema()
        fields = schema.get("fields", [])
        self._required_keys = {
            f["key"] for f in fields if f.get("required", False)
        }

        # C-2 fix: pre-compute the protocol default from the schema *before* the
        # widget-construction loop, so that when we reach the "port" field we can
        # determine the correct default port without reading _field_widgets (which
        # is only partially populated at that point).
        protocol_default = "FTP"
        for f in fields:
            if f["key"] == "protocol" and f.get("type") == "select":
                opts = f.get("options", [])
                if opts:
                    protocol_default = opts[0].upper()
                break

        for field in fields:
            key = field["key"]
            label = field.get("label", key)
            ftype = field.get("type", "text")
            hint = field.get("hint", "")
            required = field.get("required", False)

            # Store label for user-friendly validation messages
            self._field_labels[key] = label

            label_text = f"{'*' if required else ''}{label}:"

            if ftype == "password":
                widget = QtWidgets.QLineEdit()
                widget.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
            elif ftype == "number":
                widget = QtWidgets.QSpinBox()
                widget.setRange(1, 65535)
                # Apply sensible default port based on the pre-computed protocol
                # default (C-2 fix: avoids calling _collect_config() mid-loop).
                default_port = field.get("default")
                if default_port is None and key == "port":
                    default_port = 22 if protocol_default == "SFTP" else 21
                if default_port is not None:
                    try:
                        widget.setValue(int(default_port))
                    except (TypeError, ValueError):
                        pass
            elif ftype == "bool":
                widget = QtWidgets.QCheckBox()
                # Default to unchecked (False) for all bool fields.
                # Fields like trust_host_key should default to the secure option (False).
                widget.setChecked(False)
            elif ftype == "select":
                widget = QtWidgets.QComboBox()
                for opt in field.get("options", []):
                    widget.addItem(opt)
            elif ftype == "file":
                container = QtWidgets.QWidget()
                row = QtWidgets.QHBoxLayout(container)
                row.setContentsMargins(0, 0, 0, 0)
                line = QtWidgets.QLineEdit()
                browse = QtWidgets.QPushButton("Browse...")
                browse.clicked.connect(lambda checked, l=line: self._browse_file(l))
                row.addWidget(line)
                row.addWidget(browse)
                widget = container
                # M-8 fix: store the line edit reference in a separate dict instead
                # of adding a dynamic attribute to a QWidget (fragile with Shiboken).
                self._file_line_edits[key] = line
            else:
                widget = QtWidgets.QLineEdit()

            if hint and hasattr(widget, "setToolTip"):
                widget.setToolTip(hint)

            self._fields_layout.addRow(label_text, widget)
            self._field_widgets[key] = (ftype, widget)

    def _browse_file(self, line_edit: QtWidgets.QLineEdit):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select File")
        if path:
            line_edit.setText(path)

    def _collect_config(self) -> dict:
        config = {}
        for key, (ftype, widget) in self._field_widgets.items():
            if ftype == "number":
                config[key] = widget.value()
            elif ftype == "bool":
                config[key] = widget.isChecked()
            elif ftype == "select":
                config[key] = widget.currentText()
            elif ftype == "file":
                config[key] = self._file_line_edits[key].text()
            else:
                config[key] = widget.text()
        return config

    def _validate_required_fields(self) -> bool:
        """Validate that all required fields are filled. Returns True if valid."""
        config = self._collect_config()
        missing_labels = []
        for key in self._required_keys:
            value = config.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                # Use human-readable label instead of internal key name
                missing_labels.append(self._field_labels.get(key, key))
        if missing_labels:
            QtWidgets.QMessageBox.warning(
                self,
                "Missing Required Fields",
                f"Please fill in the following required fields:\n\u2022 " + "\n\u2022 ".join(missing_labels),
            )
            return False
        return True

    def _on_test(self):
        if not self._validate_required_fields():
            return

        # PD-2 fix: if a previous auth worker is still running (e.g. triggered
        # by a rapid second click before the buttons were re-enabled), disconnect
        # its signals and let it run to completion without affecting the UI.
        if self._auth_worker is not None and self._auth_worker.isRunning():
            try:
                self._auth_worker.succeeded.disconnect()
                self._auth_worker.failed.disconnect()
            except RuntimeError:
                pass
            self._auth_worker = None

        # Disable buttons during the async test to prevent double-clicks
        self._test_btn.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._status_label.setText("Testing connection...")
        self._set_status_color("neutral")

        ptype = self._type_combo.currentData()
        config = self._collect_config()
        config["name"] = self._name_edit.text() or "Test"

        provider = create_provider(ptype, config)

        # Run authenticate() in a background thread so the UI stays responsive
        self._auth_worker = _AuthWorker(provider)
        self._auth_worker.succeeded.connect(self._on_test_succeeded)
        self._auth_worker.failed.connect(self._on_test_failed)
        self._auth_worker.start()

    def _on_test_succeeded(self, provider):
        self._test_btn.setEnabled(True)
        self._save_btn.setEnabled(True)
        self._set_status_color("success")
        self._status_label.setText("Connection successful!")
        # Preserve the authenticated provider so that _on_save can reuse its
        # token/config (avoids a redundant re-authentication on Save).
        self._tested_provider = provider

    def _on_test_failed(self, error_msg: str):
        self._test_btn.setEnabled(True)
        self._save_btn.setEnabled(True)
        self._set_status_color("error")
        self._status_label.setText(f"Connection failed: {error_msg}")
        self._tested_provider = None

    def _set_status_color(self, state: str):
        """Set status label color using palette roles (works on light and dark themes)."""
        palette = self._status_label.palette()
        app_palette = QtWidgets.QApplication.palette()
        if state == "success":
            color = app_palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Link)
        elif state == "error":
            color = app_palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.BrightText)
        else:  # neutral
            color = app_palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText)
        palette.setColor(QPalette.ColorRole.WindowText, color)
        self._status_label.setPalette(palette)

    def closeEvent(self, event):
        """Ensure the auth worker thread is finished before closing."""
        if self._auth_worker is not None and self._auth_worker.isRunning():
            try:
                self._auth_worker.succeeded.disconnect()
                self._auth_worker.failed.disconnect()
            except RuntimeError:
                pass
            self._auth_worker.wait(3000)
        super().closeEvent(event)

    def _on_save(self):
        name = self._name_edit.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "Validation", "Please enter an account name.")
            return

        if not self._validate_required_fields():
            return

        ptype = self._type_combo.currentData()

        # If a test was performed successfully, use the provider's config which
        # may include OAuth tokens generated during authentication.
        if self._tested_provider is not None:
            config = self._tested_provider.config
        else:
            config = self._collect_config()

        store = get_config_store()
        auth_mgr = get_auth_manager()

        # add_account already stores provider_type and name; pass only extra data
        # to avoid duplicating those fields in the stored JSON.
        account_id = store.add_account(ptype, name, {})
        # save_credentials handles splitting into safe (written to config.json) and
        # sensitive (written to keyring/fernet/base64) fields.  This also persists
        # all non-sensitive provider fields (e.g. client_id, bucket, host) that
        # add_account did not store.
        config["name"] = name
        config["provider_type"] = ptype
        auth_mgr.save_credentials(account_id, config)

        # Warn the user if credentials are stored with weak protection (base64).
        acct_data = store.get_account(account_id) or {}
        if "_sensitive_b64" in acct_data:
            QtWidgets.QMessageBox.warning(
                self,
                "Security Warning",
                "Neither 'keyring' nor 'cryptography' is installed.\n\n"
                "Your credentials are stored as Base64 in the config file, which is "
                "NOT encrypted. Anyone with access to your user profile can read them.\n\n"
                "Install the 'cryptography' package (pip install cryptography) or "
                "'keyring' (pip install keyring) for proper credential protection.",
            )

        self.accept()


class ManageProvidersDialog(QtWidgets.QDialog):
    """Dialog to view, edit, and delete configured cloud accounts."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Cloud Accounts")
        self.setMinimumSize(500, 300)
        self._build_ui()
        self._refresh_list()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        self._list = QtWidgets.QListWidget()
        self._list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self._list)

        btn_row = QtWidgets.QHBoxLayout()
        self._delete_btn = QtWidgets.QPushButton("Delete Selected")
        self._delete_btn.clicked.connect(self._on_delete)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._delete_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _refresh_list(self):
        self._list.clear()
        store = get_config_store()
        self._accounts = store.list_accounts()
        for acc in self._accounts:
            ptype = acc.get("provider_type", "")
            label = f"{acc.get('name', 'Unnamed')}  [{PROVIDER_DISPLAY_NAMES.get(ptype, ptype)}]"
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, acc["id"])
            self._list.addItem(item)

    def _on_delete(self):
        selected = self._list.currentItem()
        if not selected:
            return
        account_id = selected.data(QtCore.Qt.ItemDataRole.UserRole)
        name = selected.text()

        reply = QtWidgets.QMessageBox.question(
            self, "Confirm Delete",
            f"Remove account:\n{name}?\n\nThis will delete all stored credentials.",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            auth_mgr = get_auth_manager()
            auth_mgr.delete_credentials(account_id)
            self._refresh_list()
