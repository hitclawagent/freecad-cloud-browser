# core/auth_manager.py
# Manages OAuth tokens and credentials with secure persistence

import json
import os
import base64
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

_KEYRING_SERVICE = "FreeCAD-CloudBrowser"
_KEYRING_FERNET_KEY_ACCOUNT = "fernet_encryption_key"
_LEGACY_KEY_FILENAMES = (".credential_key", "secret.key")


def _get_keyring():
    """Try to import keyring; return None if not available."""
    try:
        import keyring
        return keyring
    except ImportError:
        return None


def _load_legacy_key_file(config_dir: str) -> Optional[bytes]:
    """Return the bytes from the first legacy key file found, or None."""
    for name in _LEGACY_KEY_FILENAMES:
        path = os.path.join(config_dir, name)
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    return f.read()
            except OSError:
                pass
    return None


def _delete_legacy_key_files(config_dir: str) -> None:
    """Remove any legacy on-disk key files after migration to keyring."""
    for name in _LEGACY_KEY_FILENAMES:
        path = os.path.join(config_dir, name)
        try:
            os.unlink(path)
            logger.info("Removed legacy Fernet key file: %s", path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Could not remove legacy key file %s: %s", path, exc)


def _get_fernet_key(config_dir: str) -> bytes:
    """
    Load or generate the Fernet encryption key.

    Priority:
    1. System keyring (Windows Credential Manager / macOS Keychain / libsecret)
    2. Migrate from legacy .credential_key / secret.key file → keyring
    3. Generate new key → keyring
    4. Fallback to key file when keyring is unavailable/locked (logs warning)
    """
    from cryptography.fernet import Fernet

    kr = _get_keyring()
    if kr is not None:
        try:
            import keyring.errors as _ke
            stored = kr.get_password(_KEYRING_SERVICE, _KEYRING_FERNET_KEY_ACCOUNT)
            if stored:
                # Keyring has the key — clean up any stale file from old installs
                _delete_legacy_key_files(config_dir)
                return stored.encode("ascii")

            # No key in keyring yet
            legacy = _load_legacy_key_file(config_dir)
            if legacy is not None:
                # Migrate: store in keyring first, THEN delete file (never delete first)
                try:
                    kr.set_password(
                        _KEYRING_SERVICE, _KEYRING_FERNET_KEY_ACCOUNT,
                        legacy.decode("ascii"),
                    )
                except UnicodeDecodeError:
                    # Corrupted / non-ASCII key file — generate a fresh key instead
                    logger.warning(
                        "Legacy key file contains non-ASCII bytes; "
                        "generating a new Fernet key. "
                        "Existing encrypted credentials will need to be re-entered."
                    )
                    key = Fernet.generate_key()
                    kr.set_password(
                        _KEYRING_SERVICE, _KEYRING_FERNET_KEY_ACCOUNT,
                        key.decode("ascii"),
                    )
                    _delete_legacy_key_files(config_dir)
                    logger.info("Generated new Fernet key; stored securely in system keyring.")
                    return key
                _delete_legacy_key_files(config_dir)
                logger.info(
                    "Migrated Fernet encryption key from file to system keyring."
                )
                return legacy

            # Generate a brand-new key and store in keyring
            key = Fernet.generate_key()
            kr.set_password(
                _KEYRING_SERVICE, _KEYRING_FERNET_KEY_ACCOUNT,
                key.decode("ascii"),
            )
            logger.info("Generated new Fernet key; stored securely in system keyring.")
            return key

        except (_ke.KeyringLocked, _ke.NoKeyringError, _ke.KeyringError) as exc:
            logger.warning(
                "System keyring unavailable (%s). "
                "Falling back to on-disk key file — credentials are less secure. "
                "Ensure a keyring backend is available for full protection.",
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Unexpected keyring error (%s). Falling back to on-disk key file.",
                exc,
            )

    # Fallback: on-disk key file (keyring unavailable or errored)
    legacy = _load_legacy_key_file(config_dir)
    if legacy is not None:
        return legacy

    key_path = os.path.join(config_dir, ".credential_key")
    key = Fernet.generate_key()
    logger.warning(
        "System keyring not available. Storing Fernet key at %s. "
        "Install a keyring backend for stronger protection.",
        key_path,
    )
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def _try_fernet_encrypt(data: str, config_dir: str) -> Optional[str]:
    """Encrypt data with Fernet; return None if cryptography is not installed."""
    try:
        from cryptography.fernet import Fernet
        key = _get_fernet_key(config_dir)
        return Fernet(key).encrypt(data.encode()).decode()
    except ImportError:
        return None
    except Exception as e:
        logger.warning("Fernet encryption failed: %s", e)
        return None


def _try_fernet_decrypt(token: str, config_dir: str) -> Optional[str]:
    """Decrypt a Fernet token; return None on any failure."""
    try:
        from cryptography.fernet import Fernet
        key = _get_fernet_key(config_dir)
        return Fernet(key).decrypt(token.encode()).decode()
    except ImportError:
        return None
    except Exception as e:
        logger.warning("Fernet decryption failed: %s", e)
        return None


SERVICE_NAME = "FreeCAD-CloudBrowser"


class AuthManager:
    """
    Handles secure storage and retrieval of authentication credentials.

    Strategy (in priority order):
    1. System keychain via `keyring` (most secure)
    2. Fernet-encrypted field in config JSON (requires `cryptography` package)
    3. Base64 fallback — only used if neither keyring nor cryptography is available.
       A warning is logged when this insecure path is taken.
    """

    def __init__(self, config_store):
        self._store = config_store
        self._keyring = _get_keyring()
        self._config_dir = config_store.config_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_credentials(self, account_id: str, credentials: Dict[str, Any]) -> None:
        """
        Persist credentials for an account.
        Sensitive fields (keys ending in '_secret', '_password', '_token', '_key')
        are stored in the system keychain if available, otherwise encrypted via Fernet,
        or as a last resort base64-obfuscated with a loud warning.
        """
        safe = {}
        sensitive = {}

        for k, v in credentials.items():
            if self._is_sensitive(k):
                sensitive[k] = str(v)
            else:
                safe[k] = v

        # Read existing account data once and reuse across all branches.
        existing = self._store.get_account(account_id) or {}

        # Store safe fields in config file (clear any stale encryption fields)
        existing.update(safe)
        existing.pop("_sensitive_b64", None)
        existing.pop("_sensitive_fernet", None)
        self._store.save_account(account_id, existing)

        if not sensitive:
            return

        # 1. System keychain
        if self._keyring:
            self._keyring.set_password(SERVICE_NAME, account_id, json.dumps(sensitive))
            return

        # 2. Fernet encryption
        # SEC-1 note: the Fernet key is stored in the same directory as the
        # encrypted data.  Anyone who gains access to the whole config directory
        # (backup, cloud sync of AppData, physical access) can decrypt credentials.
        # Install 'keyring' for OS-keychain-backed protection which avoids this.
        fernet_token = _try_fernet_encrypt(json.dumps(sensitive), self._config_dir)
        if fernet_token is not None:
            # SEC-1 fix: also delete any stale keyring entry that may exist from
            # a previous installation where keyring was available.  Without this,
            # load_credentials() would silently use the (possibly outdated) keyring
            # entry instead of the newly-written Fernet token.
            # We attempt this cleanup silently — keyring may not be installed.
            try:
                import keyring as _kr
                _kr.delete_password(SERVICE_NAME, account_id)
            except Exception:
                pass
            existing["_sensitive_fernet"] = fernet_token
            self._store.save_account(account_id, existing)
            return

        # 3. Last resort: base64 (insecure) — warn loudly
        # SEC-2: base64 is NOT encryption.  The data is trivially decodable by
        # anyone with access to config.json.  We refuse silent degradation and
        # surface this prominently so users cannot miss it.
        logger.warning(
            "SECURITY WARNING: Neither 'keyring' nor 'cryptography' is installed. "
            "Credentials for account %s are stored as base64 in config.json. "
            "This is NOT secure — base64 is encoding, not encryption. "
            "Install 'cryptography' (pip install cryptography) or 'keyring' "
            "(pip install keyring) for proper protection.",
            account_id,
        )
        existing["_sensitive_b64"] = base64.b64encode(json.dumps(sensitive).encode()).decode()
        self._store.save_account(account_id, existing)

    def load_credentials(self, account_id: str) -> Dict[str, Any]:
        """Load all credentials for an account, merging safe + sensitive fields."""
        data = dict(self._store.get_account(account_id) or {})

        if self._keyring:
            payload = self._keyring.get_password(SERVICE_NAME, account_id)
            if payload:
                try:
                    data.update(json.loads(payload))
                except (json.JSONDecodeError, ValueError) as e:
                    logger.error(
                        "Corrupted keyring entry for account %s: %s", account_id, e
                    )
            data.pop("_sensitive_b64", None)
            data.pop("_sensitive_fernet", None)
            return data

        # Try Fernet
        fernet_token = data.pop("_sensitive_fernet", None)
        if fernet_token:
            decrypted = _try_fernet_decrypt(fernet_token, self._config_dir)
            if decrypted:
                try:
                    data.update(json.loads(decrypted))
                except (json.JSONDecodeError, ValueError) as e:
                    logger.error(
                        "Corrupted Fernet credentials for account %s: %s", account_id, e
                    )
                data.pop("_sensitive_b64", None)
                return data
            else:
                # AM-1 fix: Fernet decryption returned None — either `cryptography`
                # is not installed (ImportError) or the key/token is corrupted.
                # Log prominently and fall through to the base64 fallback so that
                # users who downgraded from cryptography can still load their credentials
                # if a base64 copy also exists.  If no fallback exists, credentials
                # will simply be missing and the provider will re-authenticate.
                logger.error(
                    "Failed to decrypt Fernet credentials for account %s. "
                    "The 'cryptography' package may not be installed, or the key/token "
                    "is corrupted. Falling back to base64 storage if available.",
                    account_id,
                )
                # Fall through to base64 below

        # Fall back to base64
        b64 = data.pop("_sensitive_b64", None)
        if b64:
            try:
                data.update(json.loads(base64.b64decode(b64).decode()))
            except Exception as e:
                logger.error(
                    "Corrupted base64 credentials for account %s: %s", account_id, e
                )

        return data

    def delete_credentials(self, account_id: str) -> None:
        """Remove all stored credentials for an account."""
        self._store.delete_account(account_id)
        if self._keyring:
            try:
                self._keyring.delete_password(SERVICE_NAME, account_id)
            except Exception as e:
                logger.warning(
                    "Failed to remove keyring entry for account %s: %s. "
                    "Credentials may still be present in the system keychain.",
                    account_id, e,
                )

    def update_token(self, account_id: str, token_data: Dict[str, Any]) -> None:
        """
        Update OAuth token fields for an existing account.
        Called by providers after token refresh.
        """
        self.save_credentials(account_id, token_data)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    # B-4 fix: define _SENSITIVE_SUFFIXES before _is_sensitive so that readers
    # encounter the data before the method that references it.
    _SENSITIVE_SUFFIXES = (
        "_secret", "_password", "_token", "_key",
        "_json", "_cache_json",
    )

    @staticmethod
    def _is_sensitive(key: str) -> bool:
        return any(key.lower().endswith(s) for s in AuthManager._SENSITIVE_SUFFIXES)
