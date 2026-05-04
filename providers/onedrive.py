# providers/onedrive.py
# OneDrive provider via Microsoft Graph API + MSAL OAuth2

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import List

from .base import CloudProvider, RemoteItem

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES = ["Files.Read", "Files.ReadWrite", "offline_access"]

# Default timeout in seconds for HTTP requests
_HTTP_TIMEOUT = 30


class OneDriveProvider(CloudProvider):
    """OneDrive cloud provider via Microsoft Graph API."""

    _PROVIDER_TYPE = "onedrive"
    _DISPLAY_NAME = "OneDrive"

    @property
    def provider_type(self) -> str:
        return self._PROVIDER_TYPE

    @property
    def display_name(self) -> str:
        return self._DISPLAY_NAME

    def __init__(self, config: dict):
        super().__init__(config)
        self._token = None
        self._token_expiry = None   # datetime in UTC when the access token expires
        self._msal_app = None       # cached PublicClientApplication for silent refresh

    @classmethod
    def get_config_schema(cls) -> dict:
        return {
            "fields": [
                {
                    "key": "client_id",
                    "label": "Application (Client) ID",
                    "type": "text",
                    "required": True,
                    "hint": "From Azure Portal → App registrations",
                },
                {
                    "key": "tenant_id",
                    "label": "Tenant ID",
                    "type": "text",
                    "required": False,
                    "hint": "Leave empty for personal Microsoft accounts ('consumers')",
                },
            ]
        }

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self) -> bool:
        try:
            import msal

            client_id = self.config["client_id"]
            tenant_id = self.config.get("tenant_id") or "consumers"
            authority = f"https://login.microsoftonline.com/{tenant_id}"

            token_cache_json = self.config.get("token_cache_json")
            cache = msal.SerializableTokenCache()
            if token_cache_json:
                cache.deserialize(token_cache_json)

            app = msal.PublicClientApplication(
                client_id, authority=authority, token_cache=cache
            )

            # Try silent first
            accounts = app.get_accounts()
            result = None
            if accounts:
                result = app.acquire_token_silent(SCOPES, account=accounts[0])

            if not result:
                # Interactive flow
                flow = app.initiate_device_flow(scopes=SCOPES)
                if "user_code" not in flow:
                    raise RuntimeError("Failed to start device code flow")

                from PySide6 import QtWidgets
                QtWidgets.QMessageBox.information(
                    None,
                    "OneDrive Authorization",
                    f"To sign in, go to:\n\n{flow['verification_uri']}\n\n"
                    f"And enter code: {flow['user_code']}",
                )
                result = app.acquire_token_by_device_flow(flow)

            if "access_token" not in result:
                raise RuntimeError(result.get("error_description", "Auth failed"))

            self._token = result["access_token"]
            self.config["token_cache_json"] = cache.serialize()
            # Store the MSAL app for silent token refresh later
            self._msal_app = app
            # Compute token expiry time (default to 1 hour if not provided)
            expires_in = result.get("expires_in", 3600)
            self._token_expiry = datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in - 60)
            logger.info("OneDrive authenticated successfully.")
            self._authenticated = True

            # SEC-5 fix: persist the token cache immediately so that it is not
            # lost if the user closes FreeCAD before any directory listing
            # completes (which is the only other place save_credentials is called).
            try:
                from core import get_auth_manager
                account_id = self.config.get("_account_id")
                if account_id:
                    get_auth_manager().save_credentials(account_id, self.config)
            except Exception as _persist_err:
                logger.warning(
                    "OneDrive: could not immediately persist token cache: %s", _persist_err
                )

            return True

        except Exception as e:
            self._authenticated = False
            raise RuntimeError(f"OneDrive authentication failed: {e}") from e

    def _refresh_token_if_needed(self):
        """
        Silently refresh the access token if it has expired or is about to expire.

        IMPORTANT: This method is called from worker threads (via _headers() →
        list_directory / download_file).  It must NEVER invoke self.authenticate()
        because authenticate() shows interactive Qt widgets which are not safe to
        create from a non-main thread.  Instead, we raise a RuntimeError so that
        the worker thread propagates it back to the UI as an error message, giving
        the user a chance to manually re-authenticate.
        """
        if self._token_expiry is None or datetime.now(tz=timezone.utc) < self._token_expiry:
            return  # Token is still valid
        if self._msal_app is None:
            raise RuntimeError(
                "OneDrive access token has expired and cannot be refreshed silently "
                "(no cached MSAL application). Please re-add the account to re-authenticate."
            )
        try:
            accounts = self._msal_app.get_accounts()
            if not accounts:
                raise RuntimeError(
                    "OneDrive access token has expired and there are no cached accounts "
                    "for silent refresh. Please re-add the account to re-authenticate."
                )
            result = self._msal_app.acquire_token_silent(SCOPES, account=accounts[0])
            if result and "access_token" in result:
                self._token = result["access_token"]
                cache = self._msal_app.token_cache
                if hasattr(cache, "serialize"):
                    self.config["token_cache_json"] = cache.serialize()
                expires_in = result.get("expires_in", 3600)
                self._token_expiry = datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in - 60)
                logger.debug("OneDrive token refreshed silently.")
            else:
                raise RuntimeError(
                    "OneDrive silent token refresh failed. "
                    "Please re-add the account to re-authenticate."
                )
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(
                f"OneDrive token refresh error: {e}. "
                "Please re-add the account to re-authenticate."
            ) from e

    def is_authenticated(self) -> bool:
        return self._authenticated and self._token is not None

    def _headers(self) -> dict:
        # Refresh token if expired before building the Authorization header
        self._refresh_token_if_needed()
        return {"Authorization": f"Bearer {self._token}"}

    # ------------------------------------------------------------------
    # Directory listing
    # ------------------------------------------------------------------

    def list_directory(self, path: str = "root") -> List[RemoteItem]:
        """
        `path` is either 'root' or a OneDrive item ID.
        Follows @odata.nextLink to return all items (not just first page).
        """
        if not self.is_authenticated():
            self.authenticate()

        import requests

        if path == "root" or path == "/":
            url = f"{GRAPH_BASE}/me/drive/root/children"
        else:
            url = f"{GRAPH_BASE}/me/drive/items/{path}/children"

        params = {"$select": "id,name,folder,file,size,lastModifiedDateTime,mimeType"}

        items = []
        while url:
            # Added timeout to prevent hanging indefinitely on slow connections
            response = requests.get(
                url, headers=self._headers(), params=params, timeout=_HTTP_TIMEOUT
            )
            response.raise_for_status()
            data = response.json()

            for entry in data.get("value", []):
                is_dir = "folder" in entry
                items.append(RemoteItem(
                    name=entry["name"],
                    path=entry["id"],
                    is_dir=is_dir,
                    size=entry.get("size"),
                    modified=entry.get("lastModifiedDateTime"),
                    mime_type=entry.get("file", {}).get("mimeType"),
                ))

            # Follow pagination link if present
            url = data.get("@odata.nextLink")
            params = {}  # nextLink already contains query params

        return self.filter_freecad_files(items)

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download_file(self, remote_item: RemoteItem, local_path: str) -> str:
        import requests

        url = f"{GRAPH_BASE}/me/drive/items/{remote_item.path}/content"
        response = requests.get(
            url, headers=self._headers(), stream=True, timeout=_HTTP_TIMEOUT
        )
        response.raise_for_status()

        local_dir = os.path.dirname(local_path)
        if local_dir:
            os.makedirs(local_dir, exist_ok=True)
        with open(local_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        return local_path

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    # Microsoft Graph API limits simple PUT uploads to 4 MB.
    # Larger files must use an upload session.
    _SIMPLE_UPLOAD_MAX = 4 * 1024 * 1024   # 4 MB
    # Each upload session chunk must be a multiple of 320 KiB
    _SESSION_CHUNK_SIZE = 10 * 320 * 1024  # 3.2 MB (10 × 320 KiB)

    def upload_file(self, local_path: str, remote_dir_path: str) -> bool:
        import requests

        filename = os.path.basename(local_path)
        file_size = os.path.getsize(local_path)

        if file_size <= self._SIMPLE_UPLOAD_MAX:
            # Small file: simple PUT (≤ 4 MB)
            url = f"{GRAPH_BASE}/me/drive/items/{remote_dir_path}:/{filename}:/content"
            with open(local_path, "rb") as f:
                response = requests.put(
                    url, headers=self._headers(), data=f, timeout=_HTTP_TIMEOUT
                )
            response.raise_for_status()
            return True

        # OD-2 fix: large file — use resumable upload session API
        # Step 1: create an upload session
        create_url = (
            f"{GRAPH_BASE}/me/drive/items/{remote_dir_path}:/{filename}:/createUploadSession"
        )
        session_resp = requests.post(
            create_url,
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
            timeout=_HTTP_TIMEOUT,
        )
        session_resp.raise_for_status()
        upload_url = session_resp.json()["uploadUrl"]

        # Step 2: upload chunks
        with open(local_path, "rb") as f:
            offset = 0
            while offset < file_size:
                chunk = f.read(self._SESSION_CHUNK_SIZE)
                if not chunk:
                    break
                end = offset + len(chunk) - 1
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{end}/{file_size}",
                }
                chunk_resp = requests.put(
                    upload_url, headers=headers, data=chunk, timeout=_HTTP_TIMEOUT
                )
                # 200/201 = complete, 202 = accepted (more chunks expected)
                if chunk_resp.status_code not in (200, 201, 202):
                    chunk_resp.raise_for_status()
                offset += len(chunk)

        return True
