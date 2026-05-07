# providers/__init__.py
# Each provider is imported individually so that a missing optional dependency
# (or any other import error) in one provider disables only that provider
# instead of the entire plugin (BUG-2 fix).

import importlib
import logging

logger = logging.getLogger(__name__)

PROVIDER_REGISTRY = {}
PROVIDER_DISPLAY_NAMES_FULL = {
    "s3":     "S3",
    "ftp":    "FTP / SFTP",
    "webdav": "WebDAV",
}

# Providers that are present in the UI as placeholders but not yet enabled.
# They appear grayed-out in the combo box and cannot be selected.
PROVIDER_COMING_SOON = {"ftp", "webdav"}


def _try_register(key, module_path, class_name):
    try:
        # Use the package name so relative imports resolve correctly.
        mod = importlib.import_module(module_path, package="providers")
        PROVIDER_REGISTRY[key] = getattr(mod, class_name)
    except Exception as exc:
        logger.warning(
            "Provider '%s' could not be loaded and will be unavailable: %s",
            key, exc,
        )

_try_register("s3",     ".s3",     "S3Provider")
_try_register("ftp",    ".ftp",    "FTPProvider")
_try_register("webdav", ".webdav", "WebDAVProvider")

# Expose display names for all known providers (including coming-soon placeholders)
PROVIDER_DISPLAY_NAMES = dict(PROVIDER_DISPLAY_NAMES_FULL)


def create_provider(provider_type: str, config: dict):
    """Factory: creates a provider instance from type string and config dict."""
    cls = PROVIDER_REGISTRY.get(provider_type)
    if cls is None:
        raise ValueError(f"Unknown or unavailable provider type: {provider_type}")
    return cls(config)
