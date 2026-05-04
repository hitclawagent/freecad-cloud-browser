# providers/dropbox.py
# Dropbox provider using OAuth2 + dropbox SDK

import os
import logging
from typing import List

from .base import CloudProvider, RemoteItem

logger = logging.getLogger(__name__)

# Files are always uploaded via chunked session upload to avoid loading
# the entire file into RAM regardless of size.
_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB per chunk


class DropboxProvider(CloudProvider):
    """Dropbox cloud provider via OAuth2."""

    _PROVIDER_TYPE = "dropbox"
    _DISPLAY_NAME = "Dropbox"

    @property
    def provider_type(self) -> str:
        return self._PROVIDER_TYPE

    @property
    def display_name(self) -> str:
        return self._DISPLAY_NAME

    def __init__(self, config: dict):
        super().__init__(config)
        self._dbx = None

    @classmethod
    def get_config_schema(cls) -> dict:
        return {
            "fields": [
                {
                    "key": "app_key",
                    "label": "App Key",
                    "type": "text",
                    "required": True,
                    "hint": "From Dropbox App Console",
                },
                {
                    "key": "app_secret",
                    "label": "App Secret",
                    "type": "password",
                    "required": True,
                },
                {
                    "key": "refresh_token",
                    "label": "Refresh Token",
                    "type": "password",
                    "required": False,
                    "hint": "Generated during OAuth flow (stored automatically)",
                },
            ]
        }

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self) -> bool:
        try:
            import dropbox
            from dropbox import DropboxOAuth2FlowNoRedirect

            app_key = self.config["app_key"]
            app_secret = self.config["app_secret"]
            refresh_token = self.config.get("refresh_token")

            if refresh_token:
                self._dbx = dropbox.Dropbox(
                    oauth2_refresh_token=refresh_token,
                    app_key=app_key,
                    app_secret=app_secret,
                )
            else:
                # Interactive OAuth flow
                auth_flow = DropboxOAuth2FlowNoRedirect(
                    app_key, app_secret,
                    token_access_type="offline"
                )
                authorize_url = auth_flow.start()

                # Show URL to user via Qt dialog
                from PySide6 import QtWidgets
                QtWidgets.QMessageBox.information(
                    None,
                    "Dropbox Authorization",
                    f"Open this URL in your browser to authorize:\n\n{authorize_url}\n\n"
                    "Then copy the authorization code and paste it in the next dialog.",
                )
                auth_code, ok = QtWidgets.QInputDialog.getText(
                    None, "Dropbox Auth Code", "Paste the authorization code:"
                )
                # BUG-1 fix: raise instead of returning False so callers
                # (which only catch exceptions) receive proper feedback.
                if not ok or not auth_code.strip():
                    raise RuntimeError("Authentication cancelled by user.")

                oauth_result = auth_flow.finish(auth_code.strip())
                self.config["refresh_token"] = oauth_result.refresh_token
                self._dbx = dropbox.Dropbox(
                    oauth2_refresh_token=oauth_result.refresh_token,
                    app_key=app_key,
                    app_secret=app_secret,
                )

            self._dbx.users_get_current_account()  # Validate connection
            logger.info("Dropbox authenticated successfully.")
            self._authenticated = True

            # Persist the refresh_token immediately after OAuth so it is not lost
            # if FreeCAD exits before the first directory listing completes
            # (same pattern used by GoogleDriveProvider and OneDriveProvider).
            try:
                from core import get_auth_manager
                account_id = self.config.get("_account_id")
                if account_id:
                    clean = {k: v for k, v in self.config.items() if not k.startswith("_")}
                    get_auth_manager().save_credentials(account_id, clean)
            except Exception as _persist_err:
                logger.warning(
                    "Dropbox: could not immediately persist refresh token: %s", _persist_err
                )

            return True

        except Exception as e:
            self._authenticated = False
            raise RuntimeError(f"Dropbox authentication failed: {e}") from e

    def is_authenticated(self) -> bool:
        return self._authenticated and self._dbx is not None

    # ------------------------------------------------------------------
    # Directory listing
    # ------------------------------------------------------------------

    def list_directory(self, path: str = "") -> List[RemoteItem]:
        """
        `path` is a Dropbox path string (e.g. '' for root, '/MyFolder').
        """
        if not self.is_authenticated():
            self.authenticate()

        import dropbox as _dbx_mod

        # Dropbox root is represented as ""
        dbx_path = "" if path in ("/", "") else path

        result = self._dbx.files_list_folder(dbx_path)
        entries = list(result.entries)

        while result.has_more:
            result = self._dbx.files_list_folder_continue(result.cursor)
            entries.extend(result.entries)

        items = []
        for entry in entries:
            is_dir = isinstance(entry, _dbx_mod.files.FolderMetadata)
            # Avoid "None" string for folders that have no client_modified
            raw_modified = getattr(entry, "client_modified", None)
            modified = str(raw_modified) if raw_modified is not None else None
            item = RemoteItem(
                name=entry.name,
                path=entry.path_lower,
                is_dir=is_dir,
                size=getattr(entry, "size", None),
                modified=modified,
            )
            items.append(item)

        return self.filter_freecad_files(items)

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download_file(self, remote_item: RemoteItem, local_path: str) -> str:
        local_dir = os.path.dirname(local_path)
        if local_dir:
            os.makedirs(local_dir, exist_ok=True)
        self._dbx.files_download_to_file(local_path, remote_item.path)
        return local_path

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_file(self, local_path: str, remote_dir_path: str) -> bool:
        """
        Stream-upload using Dropbox chunked session API for all file sizes.
        This avoids loading the entire file into RAM and handles files of
        any size correctly (PERF-3 + DBX-1 fix).
        """
        import dropbox

        filename = os.path.basename(local_path)
        remote_path = f"{remote_dir_path}/{filename}".replace("//", "/")
        commit = dropbox.files.CommitInfo(
            path=remote_path,
            mode=dropbox.files.WriteMode.overwrite,
        )
        self._chunked_upload(local_path, commit)
        return True

    def _chunked_upload(self, local_path: str, commit) -> None:
        """
        Upload *local_path* to Dropbox using a chunked upload session.

        Cursor management (DBX-1 fix):
        - cursor.offset always reflects the byte position of the FIRST byte of
          the NEXT chunk to be sent (i.e. the total bytes successfully appended
          so far).
        - We increment cursor.offset AFTER each successful append/finish call,
          never before, so the API always receives the correct offset.
        """
        import dropbox as _dropbox

        file_size = os.path.getsize(local_path)

        with open(local_path, "rb") as f:
            # Start session with the first chunk
            first_chunk = f.read(_CHUNK_SIZE)
            session_start = self._dbx.files_upload_session_start(first_chunk)
            cursor = _dropbox.files.UploadSessionCursor(
                session_id=session_start.session_id,
                offset=len(first_chunk),
            )

            # If the whole file fit in the first chunk, finish immediately
            if cursor.offset >= file_size:
                self._dbx.files_upload_session_finish(b"", cursor, commit)
                return

            # Stream remaining chunks
            while True:
                chunk = f.read(_CHUNK_SIZE)
                if not chunk:
                    # File was an exact multiple of _CHUNK_SIZE; finish with empty body
                    self._dbx.files_upload_session_finish(b"", cursor, commit)
                    return

                # Peek: is this the last chunk?
                # cursor.offset is the start position of `chunk` in the stream.
                next_offset = cursor.offset + len(chunk)
                if next_offset >= file_size:
                    # Last chunk — finish the session
                    self._dbx.files_upload_session_finish(chunk, cursor, commit)
                    return

                # Not the last chunk — append and advance the cursor.
                # A-6 fix: create a new UploadSessionCursor with the updated
                # offset instead of mutating cursor.offset directly, which may
                # be read-only in some versions of the Dropbox SDK (Stone-generated
                # dataclass) and would otherwise cause an AttributeError or silently
                # produce an infinite loop.
                self._dbx.files_upload_session_append_v2(chunk, cursor)
                cursor = _dropbox.files.UploadSessionCursor(
                    session_id=cursor.session_id,
                    offset=next_offset,
                )
