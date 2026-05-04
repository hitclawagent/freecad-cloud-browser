# providers/google_drive.py
# Google Drive provider using OAuth2 + Google API Python Client

import os
import json
import logging
from typing import List

from .base import CloudProvider, RemoteItem

logger = logging.getLogger(__name__)

# Scopes:
# - drive.file: read + write for files created by this app
# - drive:       full read + write access to all files in Drive
# We request drive (full) so the browser can open any existing file, not only
# those created by this app.  If you prefer the minimal-permission approach,
# replace with "drive.file" (upload only) + "drive.readonly" (read any file).
SCOPES = [
    "https://www.googleapis.com/auth/drive",
]
TOKEN_FILE_KEY = "token_json"


class GoogleDriveProvider(CloudProvider):
    """Google Drive cloud provider via OAuth2."""

    _PROVIDER_TYPE = "google_drive"
    _DISPLAY_NAME = "Google Drive"

    @property
    def provider_type(self) -> str:
        return self._PROVIDER_TYPE

    @property
    def display_name(self) -> str:
        return self._DISPLAY_NAME

    def __init__(self, config: dict):
        super().__init__(config)
        self._service = None
        self._creds = None

    # ------------------------------------------------------------------
    # Configuration schema (used by UI)
    # ------------------------------------------------------------------

    @classmethod
    def get_config_schema(cls) -> dict:
        return {
            "fields": [
                {
                    "key": "client_id",
                    "label": "Client ID",
                    "type": "text",
                    "required": True,
                    "hint": "From Google Cloud Console → OAuth 2.0 Credentials",
                },
                {
                    "key": "client_secret",
                    "label": "Client Secret",
                    "type": "password",
                    "required": True,
                },
            ]
        }

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self) -> bool:
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials

            creds = None
            token_json = self.config.get(TOKEN_FILE_KEY)
            if token_json:
                creds = Credentials.from_authorized_user_info(
                    json.loads(token_json), SCOPES
                )

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    try:
                        creds.refresh(Request())
                    except Exception as refresh_err:
                        # Token refresh failed (revoked or network error); clear and re-authenticate.
                        logger.warning(
                            "Google Drive token refresh failed (%s). "
                            "Re-authentication required.",
                            refresh_err,
                        )
                        creds = None

                if not creds or not creds.valid:
                    # SEC-5: Use only http://localhost redirect (OOB flow deprecated)
                    client_config = {
                        "installed": {
                            "client_id": self.config["client_id"],
                            "client_secret": self.config["client_secret"],
                            "redirect_uris": ["http://localhost"],
                            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                            "token_uri": "https://oauth2.googleapis.com/token",
                        }
                    }
                    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
                    # Use a fixed port so the redirect URI is predictable.
                    # In Google Cloud Console add: http://localhost:8085
                    creds = flow.run_local_server(port=8085)

                # Persist token back to config
                self.config[TOKEN_FILE_KEY] = creds.to_json()

            self._creds = creds
            self._build_service()
            self._authenticated = True

            # GD-1 fix: persist the token immediately after authentication so it
            # is not lost if the first list_directory() call fails.  The token is
            # also persisted inside _on_directory_loaded in browser_panel.py, but
            # that only runs on success.  Persisting here ensures the refresh_token
            # survives even if the subsequent API call throws.
            try:
                from core import get_auth_manager
                account_id = self.config.get("_account_id")
                if account_id:
                    clean = {k: v for k, v in self.config.items() if not k.startswith("_")}
                    get_auth_manager().save_credentials(account_id, clean)
            except Exception as _persist_err:
                logger.warning(
                    "Google Drive: could not immediately persist token: %s", _persist_err
                )

            return True

        except Exception as e:
            self._authenticated = False
            raise RuntimeError(f"Google Drive authentication failed: {e}") from e

    def _build_service(self):
        from googleapiclient.discovery import build
        self._service = build("drive", "v3", credentials=self._creds)

    def is_authenticated(self) -> bool:
        return self._authenticated and self._service is not None

    # ------------------------------------------------------------------
    # Directory listing
    # ------------------------------------------------------------------

    def list_directory(self, path: str = "root") -> List[RemoteItem]:
        """
        `path` is a Google Drive folder ID (default: 'root').
        Returns files and subfolders inside that folder.
        Follows nextPageToken to return all items (not just first 500).
        """
        if not self.is_authenticated():
            self.authenticate()

        query = f"'{path}' in parents and trashed = false"
        fields = "nextPageToken, files(id, name, mimeType, size, modifiedTime)"

        items = []
        page_token = None
        while True:
            kwargs = dict(q=query, fields=fields, pageSize=500)
            if page_token:
                kwargs["pageToken"] = page_token

            results = self._service.files().list(**kwargs).execute()

            for f in results.get("files", []):
                is_dir = f["mimeType"] == "application/vnd.google-apps.folder"
                items.append(RemoteItem(
                    name=f["name"],
                    path=f["id"],
                    is_dir=is_dir,
                    size=int(f["size"]) if not is_dir and f.get("size") else None,
                    modified=f.get("modifiedTime"),
                    mime_type=f.get("mimeType"),
                ))

            page_token = results.get("nextPageToken")
            if not page_token:
                break

        return self.filter_freecad_files(items)

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download_file(self, remote_item: RemoteItem, local_path: str) -> str:
        from googleapiclient.http import MediaIoBaseDownload
        from googleapiclient.errors import HttpError

        request = self._service.files().get_media(fileId=remote_item.path)
        local_dir = os.path.dirname(local_path)
        if local_dir:
            os.makedirs(local_dir, exist_ok=True)

        try:
            with open(local_path, "wb") as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
        except HttpError as e:
            # A-5 fix: surface HTTP error code and message so callers (and the
            # DownloadWorker error signal) provide actionable info to the user.
            raise RuntimeError(
                f"Google Drive download failed (HTTP {e.resp.status}): {e.reason}"
            ) from e

        return local_path

    # ------------------------------------------------------------------
    # Upload (optional)
    # ------------------------------------------------------------------

    def upload_file(self, local_path: str, remote_dir_path: str) -> bool:
        from googleapiclient.http import MediaFileUpload

        filename = os.path.basename(local_path)
        media = MediaFileUpload(local_path, resumable=True)
        file_metadata = {"name": filename, "parents": [remote_dir_path]}

        self._service.files().create(
            body=file_metadata, media_body=media, fields="id"
        ).execute()
        return True
