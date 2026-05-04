"""
check_deps.py — Dependency availability checker for FreeCAD Cloud Browser.

Called at workbench activation. Does NOT install anything automatically —
dependencies are managed by the FreeCAD Addon Manager via package.xml.
If a dependency is missing, shows a clear message to the user.
"""

import importlib.util
import FreeCAD

# Map: import_name -> (pip_name, required_for)
_DEPS = {
    "requests":             ("requests",                  "all providers"),
    "googleapiclient":      ("google-api-python-client",  "Google Drive"),
    "google_auth_httplib2": ("google-auth-httplib2",      "Google Drive"),
    "google_auth_oauthlib": ("google-auth-oauthlib",      "Google Drive"),
    "dropbox":              ("dropbox",                   "Dropbox"),
    "msal":                 ("msal",                     "OneDrive"),
    "boto3":                ("boto3",                    "Amazon S3"),
    "paramiko":             ("paramiko",                 "SFTP"),
    "webdav3":              ("webdavclient3",             "WebDAV/Nextcloud"),
    "keyring":              ("keyring",                  "secure credential storage"),
    "cryptography":         ("cryptography",             "encrypted credential fallback"),
}


def check_dependencies() -> dict:
    """
    Check which dependencies are available.
    Returns a dict: {import_name: bool}.
    Logs a warning in FreeCAD console if anything critical is missing.
    """
    status = {name: importlib.util.find_spec(name) is not None for name in _DEPS}

    missing_critical = [
        f"  - {pip} (needed for {reason})"
        for name, (pip, reason) in _DEPS.items()
        if not status[name] and reason in ("all providers",)
    ]

    missing_optional = [
        f"  - {pip} (needed for {reason})"
        for name, (pip, reason) in _DEPS.items()
        if not status[name] and reason not in ("all providers",)
    ]

    if missing_critical:
        FreeCAD.Console.PrintError(
            "[Cloud Browser] Critical dependencies missing. "
            "Please reinstall via the FreeCAD Addon Manager:\n"
            + "\n".join(missing_critical) + "\n"
        )

    if missing_optional:
        FreeCAD.Console.PrintWarning(
            "[Cloud Browser] Some provider dependencies are missing "
            "(affected providers will be unavailable):\n"
            + "\n".join(missing_optional) + "\n"
            "Reinstall via the FreeCAD Addon Manager to resolve.\n"
        )

    return status
