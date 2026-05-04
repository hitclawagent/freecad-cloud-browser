# providers/ftp.py
# FTP and SFTP provider

import os
import re
import stat
import posixpath
import logging
from datetime import datetime, timezone
from typing import List

from .base import CloudProvider, RemoteItem

logger = logging.getLogger(__name__)

# Timeout in seconds for FTP/SFTP connections and transfers
_TIMEOUT = 30

# Characters that are illegal in an FTP command argument.
# A path containing a newline or carriage return could inject extra FTP commands.
_UNSAFE_PATH_RE = re.compile(r"[\r\n\x00]")

# Regex matching the start of a Unix-style FTP LIST permission string.
# Group 1: file type char (d/l/-/c/b/p/s)
# Group 2: nine permission characters
_UNIX_PERMS_RE = re.compile(r"^([dl\-cbps])([rwxsStT\-]{9})\s")


def _validate_ftp_path(path: str) -> str:
    """
    BUG-5 fix: Raise ValueError if the path contains characters that could
    inject FTP commands (e.g. embedded CRLF sequences).
    Returns the path unchanged if it is safe.
    """
    if _UNSAFE_PATH_RE.search(path):
        raise ValueError(
            f"Unsafe characters detected in remote path: {path!r}. "
            "The path may not contain newline or null characters."
        )
    return path


class FTPProvider(CloudProvider):
    """FTP / SFTP cloud provider."""

    _PROVIDER_TYPE = "ftp"
    _DISPLAY_NAME = "FTP / SFTP"

    @property
    def provider_type(self) -> str:
        return self._PROVIDER_TYPE

    @property
    def display_name(self) -> str:
        return self._DISPLAY_NAME

    def __init__(self, config: dict):
        super().__init__(config)
        self._ftp = None       # ftplib.FTP / ftplib.FTP_TLS instance
        self._sftp = None      # paramiko.SFTPClient instance
        self._ssh = None       # paramiko.SSHClient instance
        self._use_sftp = False

    @classmethod
    def get_config_schema(cls) -> dict:
        return {
            "fields": [
                {"key": "protocol",  "label": "Protocol",   "type": "select", "options": ["FTP", "FTPS", "SFTP"], "required": True},
                {"key": "host",      "label": "Host",        "type": "text",   "required": True},
                {"key": "port",      "label": "Port",        "type": "number", "required": False, "hint": "Default: 21 (FTP/FTPS), 22 (SFTP)"},
                {"key": "username",  "label": "Username",    "type": "text",   "required": True},
                {"key": "password",  "label": "Password",    "type": "password", "required": False},
                {"key": "ssh_key",   "label": "SSH Key Path","type": "file",   "required": False, "hint": "Path to private key file (SFTP only)"},
                {"key": "base_path", "label": "Base Path",   "type": "text",   "required": False, "hint": "Starting directory on the server"},
                {
                    "key": "trust_host_key",
                    "label": "Trust Host Key",
                    "type": "bool",
                    "required": False,
                    "hint": (
                        "WARNING: Disabling host key verification exposes you to "
                        "man-in-the-middle attacks. Only enable for fully trusted, "
                        "isolated networks where you control all devices."
                    ),
                },
            ]
        }

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self) -> bool:
        try:
            protocol = self.config.get("protocol", "FTP").upper()
            host = self.config["host"]
            username = self.config.get("username", "anonymous")
            password = self.config.get("password", "")

            if protocol == "SFTP":
                self._connect_sftp(host, username, password)
            elif protocol == "FTPS":
                self._connect_ftps(host, username, password)
            else:
                self._connect_ftp(host, username, password)

            self._authenticated = True
            return True

        except Exception as e:
            self._authenticated = False
            # A-3 fix: always clean up any partially-opened sockets so we don't
            # leak file descriptors when a connection attempt fails mid-way.
            self.disconnect()
            raise RuntimeError(f"FTP/SFTP connection failed: {e}") from e

    def is_authenticated(self) -> bool:
        if self._use_sftp:
            return self._authenticated and self._sftp is not None
        return self._authenticated and self._ftp is not None

    def _connect_ftp(self, host, username, password):
        import ftplib
        port = int(self.config.get("port") or 21)
        self._ftp = ftplib.FTP()
        self._ftp.connect(host, port, timeout=_TIMEOUT)
        self._ftp.login(username, password)
        self._use_sftp = False

    def _connect_ftps(self, host, username, password):
        import ftplib
        port = int(self.config.get("port") or 21)
        self._ftp = ftplib.FTP_TLS()
        self._ftp.connect(host, port, timeout=_TIMEOUT)
        self._ftp.login(username, password)
        self._ftp.prot_p()  # Switch to secure data connection
        self._use_sftp = False

    def _connect_sftp(self, host, username, password):
        import paramiko
        port = int(self.config.get("port") or 22)
        self._ssh = paramiko.SSHClient()

        trust_host_key = self.config.get("trust_host_key", False)
        if trust_host_key:
            # SEC-3 fix: require explicit in-process confirmation before enabling
            # AutoAddPolicy, which is vulnerable to MITM attacks.
            from PySide6 import QtWidgets
            reply = QtWidgets.QMessageBox.warning(
                None,
                "Security Warning — Host Key Verification Disabled",
                f"You are about to connect to <b>{host}</b> with host key "
                "verification <b>disabled</b>.<br><br>"
                "This makes the connection vulnerable to <b>man-in-the-middle "
                "attacks</b>. An attacker on your network could intercept your "
                "credentials and data.<br><br>"
                "Only proceed if you are on a fully trusted, isolated network "
                "and you understand the risk.<br><br>"
                "Do you want to continue anyway?",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if reply != QtWidgets.QMessageBox.StandardButton.Yes:
                raise RuntimeError(
                    "SFTP connection aborted: user declined to disable host key verification."
                )
            logger.warning(
                "SFTP host key verification is DISABLED for %s (user confirmed). "
                "Vulnerable to man-in-the-middle attacks.",
                host,
            )
            self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else:
            # Load known hosts from the system known_hosts file if available
            known_hosts_path = os.path.expanduser("~/.ssh/known_hosts")
            if os.path.exists(known_hosts_path):
                self._ssh.load_host_keys(known_hosts_path)
            self._ssh.set_missing_host_key_policy(paramiko.RejectPolicy())

        connect_kwargs = dict(hostname=host, port=port, username=username, timeout=_TIMEOUT)
        ssh_key_path = self.config.get("ssh_key")
        if ssh_key_path and os.path.exists(ssh_key_path):
            connect_kwargs["key_filename"] = ssh_key_path
        elif password:
            connect_kwargs["password"] = password

        self._ssh.connect(**connect_kwargs)
        self._sftp = self._ssh.open_sftp()
        self._use_sftp = True

    # ------------------------------------------------------------------
    # Explicit disconnect (replaces unreliable __del__)
    # ------------------------------------------------------------------

    def disconnect(self):
        """Explicitly close all connections. Call when switching accounts."""
        try:
            if self._sftp:
                self._sftp.close()
        except Exception:
            pass
        try:
            if self._ssh:
                self._ssh.close()
        except Exception:
            pass
        try:
            if self._ftp:
                self._ftp.quit()
        except Exception:
            pass
        self._sftp = None
        self._ssh = None
        self._ftp = None
        self._authenticated = False

    # ------------------------------------------------------------------
    # Directory listing
    # ------------------------------------------------------------------

    def list_directory(self, path: str = "") -> List[RemoteItem]:
        if not self.is_authenticated():
            self.authenticate()

        base = self.config.get("base_path", "") or ""
        # FTP-1 fix: only use `path` directly when it is an absolute path AND the
        # base_path has already been prepended (i.e. the path came from a previous
        # listing).  On the initial call the panel passes "" (empty string from
        # _current_remote_dir()), so we fall through to the base_path join.
        # We treat "/" specially: it means "server root", not "start of base_path",
        # so we only bypass base_path if path is a non-trivial absolute path.
        if path and path != "/" and path.startswith("/"):
            full_path = path
        else:
            # "" or "/" → use base_path (default to "/" if base_path is empty)
            root = base.rstrip("/") or "/"
            if not path or path == "/":
                full_path = root
            else:
                full_path = posixpath.join(root, path.lstrip("/"))

        if self._use_sftp:
            return self._list_sftp(full_path)
        else:
            return self._list_ftp(full_path)

    def _list_sftp(self, path: str) -> List[RemoteItem]:
        items = []
        for attr in self._sftp.listdir_attr(path):
            is_dir = stat.S_ISDIR(attr.st_mode)
            full = f"{path.rstrip('/')}/{attr.filename}"
            modified = None
            if attr.st_mtime is not None:
                try:
                    modified = datetime.fromtimestamp(
                        attr.st_mtime, tz=timezone.utc
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                except (OSError, OverflowError, ValueError):
                    pass
            items.append(RemoteItem(
                name=attr.filename,
                path=full,
                is_dir=is_dir,
                size=attr.st_size if not is_dir else None,
                modified=modified,
            ))
        return self.filter_freecad_files(items)

    def _list_ftp(self, path: str) -> List[RemoteItem]:
        """
        BUG-4 fix: Parse FTP LIST output with a robust format detector.

        Unix format:
            drwxr-xr-x 2 user group 4096 Jan 15 12:00 dirname
            -rw-r--r-- 1 user group 1234 Jan 15 12:00 file.fcstd

        Windows/IIS format:
            01-15-24  12:00PM <DIR>  dirname
            01-15-24  12:00PM  123456  file.fcstd

        The Unix format is identified by the POSIX permission regex pattern
        (10 characters starting with d, -, l, c, b, p, or s) rather than
        just checking the first character, which was the previous fragile
        heuristic (BUG-4).
        """
        import ftplib

        items = []
        lines = []
        try:
            self._ftp.cwd(path)
            self._ftp.retrlines("LIST", lines.append)
        except ftplib.error_perm:
            return []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            unix_match = _UNIX_PERMS_RE.match(line)
            if unix_match:
                # Unix-style
                parts = line.split(None, 8)
                if len(parts) < 9:
                    continue
                permissions = parts[0]
                name = parts[8]
                is_dir = permissions[0] == "d"
                size = None
                try:
                    size = int(parts[4]) if not is_dir else None
                except (ValueError, IndexError):
                    pass
            else:
                # Windows/IIS-style: "01-15-24  12:00PM <DIR>         dirname"
                parts = line.split(None, 3)
                if len(parts) < 4:
                    # M-5 fix: log unrecognised lines at DEBUG level so that
                    # non-standard FTP server formats are visible during
                    # troubleshooting without spamming production logs.
                    logger.debug("FTP LIST: skipping unrecognised line: %r", line)
                    continue
                is_dir = parts[2].upper() == "<DIR>"
                name = parts[3]
                size = None
                if not is_dir:
                    try:
                        size = int(parts[2])
                    except ValueError:
                        pass

            full = f"{path.rstrip('/')}/{name}"
            items.append(RemoteItem(
                name=name,
                path=full,
                is_dir=is_dir,
                size=size,
            ))
        return self.filter_freecad_files(items)

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download_file(self, remote_item: RemoteItem, local_path: str) -> str:
        # Normalise to prevent path traversal via a malicious server-supplied name.
        safe_name = os.path.basename(local_path)
        safe_local = os.path.join(os.path.dirname(local_path), safe_name) if safe_name else local_path
        local_dir = os.path.dirname(safe_local)
        if local_dir:
            os.makedirs(local_dir, exist_ok=True)
        if self._use_sftp:
            self._sftp.get(remote_item.path, safe_local)
        else:
            # BUG-5 fix: validate remote path before interpolating into FTP command
            safe_remote = _validate_ftp_path(remote_item.path)
            with open(safe_local, "wb") as f:
                self._ftp.retrbinary(f"RETR {safe_remote}", f.write)
        return safe_local

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_file(self, local_path: str, remote_dir_path: str) -> bool:
        filename = os.path.basename(local_path)
        remote_path = f"{remote_dir_path.rstrip('/')}/{filename}"
        if self._use_sftp:
            self._sftp.put(local_path, remote_path)
        else:
            # BUG-5 fix: validate remote path before interpolating into FTP command
            safe_remote = _validate_ftp_path(remote_path)
            with open(local_path, "rb") as f:
                self._ftp.storbinary(f"STOR {safe_remote}", f)
        return True
