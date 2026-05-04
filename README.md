# FreeCAD Cloud Browser

A FreeCAD workbench plugin that lets you browse and open files directly from cloud storage providers and remote servers, without leaving FreeCAD.

## Supported Providers

| Provider | Auth Method | Notes |
|---|---|---|
| Google Drive | OAuth2 (browser) | Requires Google Cloud OAuth credentials |
| Dropbox | OAuth2 (browser) | Requires Dropbox App credentials |
| OneDrive | OAuth2 (device code) | Requires Azure App registration |
| Amazon S3 | Access Key + Secret | Also works with MinIO and S3-compatible storage |
| FTP / FTPS / SFTP | Username + Password / SSH key | |
| WebDAV | Basic / Digest Auth | Works with Nextcloud, ownCloud, etc. |

## Installation

### 1. Install Python dependencies

Inside FreeCAD's bundled Python (or the Python FreeCAD uses):

```bash
pip install -r requirements.txt
```

If you only need specific providers, install only their dependencies (see `requirements.txt` for per-provider packages).

### 2. Install the plugin

Copy the `freecad-cloud-browser` folder to your FreeCAD Mod directory:

| OS | Path |
|---|---|
| Windows | `%APPDATA%\FreeCAD\Mod\freecad-cloud-browser` |
| macOS | `~/Library/Preferences/FreeCAD/Mod/freecad-cloud-browser` |
| Linux | `~/.local/share/FreeCAD/Mod/freecad-cloud-browser` |

### 3. Restart FreeCAD

The "Cloud Browser" workbench will appear in the workbench dropdown.

## Usage

1. Switch to the **Cloud Browser** workbench
2. Go to **Cloud Browser → Add Cloud Provider** and configure an account
3. Open the browser panel via **Cloud Browser → Open Cloud Browser**
4. Select your account, browse the remote folders, and double-click any file to open it in FreeCAD

### Supported file formats

All formats natively supported by FreeCAD are shown:
`.FCStd`, `.step`, `.stp`, `.iges`, `.igs`, `.stl`, `.obj`, `.dxf`, `.svg`, `.brep`, `.3mf`, `.ifc`, `.wrl`, and more.

## Provider Setup

### Google Drive

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project → Enable the **Google Drive API**
3. Create **OAuth 2.0 Credentials** (Desktop app type)
4. Copy the **Client ID** and **Client Secret** into the plugin dialog

### Dropbox

1. Go to [Dropbox App Console](https://www.dropbox.com/developers/apps)
2. Create a new app with **Full Dropbox** access
3. Copy **App Key** and **App Secret**

### OneDrive

1. Go to [Azure Portal](https://portal.azure.com) → App registrations → New registration
2. Set redirect URI to `https://login.microsoftonline.com/common/oauth2/nativeclient`
3. Under **API permissions** add `Files.Read` and `Files.ReadWrite`
4. Copy the **Application (Client) ID**

### Amazon S3

Use an IAM user with `s3:ListBucket`, `s3:GetObject`, and optionally `s3:PutObject` permissions on the target bucket.

### FTP / SFTP

Enter host, port, username, and password (or SSH private key path for SFTP).

### WebDAV

Works with any WebDAV server including Nextcloud, ownCloud, and generic WebDAV endpoints. Enter the full server URL including the DAV path.

## Architecture

```
freecad-cloud-browser/
├── InitGui.py                  # FreeCAD workbench registration
├── CloudBrowserWorkbench.py    # Commands and toolbar
├── providers/
│   ├── base.py                 # Abstract CloudProvider base class
│   ├── google_drive.py
│   ├── dropbox.py
│   ├── onedrive.py
│   ├── s3.py
│   ├── ftp.py
│   └── webdav.py
├── ui/
│   ├── browser_panel.py        # Main browsing panel (FreeCAD task panel)
│   └── provider_dialog.py      # Add / manage provider dialogs
├── core/
│   ├── auth_manager.py         # Credential storage (keyring + fallback)
│   ├── config_store.py         # JSON config persistence
│   └── file_cache.py           # Local download cache
└── requirements.txt
```

## Configuration storage

- Non-sensitive settings are stored in `<FreeCAD user dir>/CloudBrowser/config.json`
- Sensitive credentials (tokens, passwords) are stored in the **system keychain** via `keyring` when available, or base64-encoded in the config file as a fallback

## License

MIT
