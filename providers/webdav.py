# providers/webdav.py
# WebDAV provider using webdavclient3

import os
import posixpath
import logging
from typing import List

from .base import CloudProvider, RemoteItem

logger = logging.getLogger(__name__)


class WebDAVProvider(CloudProvider):
    """WebDAV cloud provider via webdavclient3."""

    _PROVIDER_TYPE = "webdav"
    _DISPLAY_NAME = "WebDAV"

    @property
    def provider_type(self) -> str:
        return self._PROVIDER_TYPE

    @property
    def display_name(self) -> str:
        return self._DISPLAY_NAME

    def __init__(self, config: dict):
        super().__init__(config)
        self._client = None

    @classmethod
    def get_config_schema(cls) -> dict:
        return {
            "fields": [
                {"key": "url",       "label": "Server URL",    "type": "text",     "required": True, "hint": "e.g. https://dav.example.com/remote.php/dav/files/user/"},
                {"key": "username",  "label": "Username",      "type": "text",     "required": True},
                {"key": "password",  "label": "Password",      "type": "password", "required": True},
                {"key": "base_path", "label": "Base Path",     "type": "text",     "required": False, "hint": "Starting subdirectory on the server"},
                {"key": "verify_ssl","label": "Verify SSL",    "type": "bool",     "required": False, "hint": "Disable for self-signed certificates"},
            ]
        }

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self) -> bool:
        try:
            from webdav3.client import Client

            options = {
                "webdav_hostname": self.config["url"],
                "webdav_login":    self.config["username"],
                "webdav_password": self.config["password"],
            }

            verify_ssl = self.config.get("verify_ssl", True)
            if not verify_ssl:
                logger.warning(
                    "WebDAV SSL verification is DISABLED for %s. "
                    "Credentials and data are transmitted without TLS validation. "
                    "Only use this on trusted networks with self-signed certificates.",
                    self.config.get("url", ""),
                )

            self._client = Client(options)
            # Set verify on the instance directly — webdavclient3 uses this
            # attribute, not the options dict, after construction.
            self._client.verify = verify_ssl

            # Validate connection
            self._client.list(self.config.get("base_path", "/"))
            self._authenticated = True
            return True

        except Exception as e:
            self._authenticated = False
            raise RuntimeError(f"WebDAV connection failed: {e}") from e

    def is_authenticated(self) -> bool:
        return self._authenticated and self._client is not None

    # ------------------------------------------------------------------
    # Directory listing
    # ------------------------------------------------------------------

    def list_directory(self, path: str = "/") -> List[RemoteItem]:
        if not self.is_authenticated():
            self.authenticate()

        base = self.config.get("base_path", "") or ""

        # M-4 fix: use posixpath.normpath to collapse any ".." segments that a
        # malicious server could inject via relative paths, preventing path
        # traversal outside the intended base directory.
        if path.startswith("/"):
            # Absolute path — normalise in place; ignore base.
            full_path = posixpath.normpath(path)
        else:
            joined = posixpath.join(base.rstrip("/") or "/", path)
            full_path = posixpath.normpath(joined)

        try:
            entries = self._client.list(full_path, get_info=True)
        except Exception as e:
            raise RuntimeError(f"WebDAV list failed for {full_path}: {e}") from e

        items = []
        for entry in entries:
            # webdavclient3 returns dicts when get_info=True
            if isinstance(entry, dict):
                name = entry.get("name", "").rstrip("/")
                is_dir = entry.get("isdir", False)
                path_key = entry.get("path", f"{full_path}/{name}")
                size_str = entry.get("size", None)
                # WD-1 fix: size_str may be a human-readable string like "1.2 KB"
                # from some WebDAV servers.  Guard against ValueError/TypeError.
                size = None
                if size_str and not is_dir:
                    try:
                        size = int(size_str)
                    except (ValueError, TypeError):
                        logger.debug(
                            "WebDAV: could not parse size %r for %s; treating as unknown.",
                            size_str, entry.get("name", ""),
                        )
                modified = entry.get("modified", None)
            else:
                name = str(entry).rstrip("/")
                is_dir = str(entry).endswith("/")
                path_key = f"{full_path.rstrip('/')}/{name}"
                size = None
                modified = None

            if not name or name == ".":
                continue

            items.append(RemoteItem(
                name=name,
                path=path_key,
                is_dir=is_dir,
                size=size,
                modified=str(modified) if modified else None,
            ))

        return self.filter_freecad_files(items)

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download_file(self, remote_item: RemoteItem, local_path: str) -> str:
        # Validate against path traversal: a malicious server could supply
        # a remote path like "../../etc/passwd".  We normalise local_path
        # against its own directory so only the basename can vary.
        safe_name = os.path.basename(local_path)
        safe_local = os.path.join(os.path.dirname(local_path), safe_name) if safe_name else local_path
        local_dir = os.path.dirname(safe_local)
        if local_dir:
            os.makedirs(local_dir, exist_ok=True)
        self._client.download_sync(
            remote_path=remote_item.path,
            local_path=safe_local,
        )
        return safe_local

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_file(self, local_path: str, remote_dir_path: str) -> bool:
        filename = os.path.basename(local_path)
        remote_path = f"{remote_dir_path.rstrip('/')}/{filename}"
        self._client.upload_sync(remote_path=remote_path, local_path=local_path)
        return True
