# providers/base.py
# Abstract base class for all cloud storage providers

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List
import os


@dataclass
class RemoteItem:
    """Represents a file or folder on a remote storage."""
    name: str
    path: str                       # Full remote path / ID
    is_dir: bool
    size: Optional[int] = None      # bytes, None if unknown
    modified: Optional[str] = None  # ISO 8601 string
    mime_type: Optional[str] = None
    icon: Optional[str] = None      # Optional icon name hint


@dataclass
class ProviderConfig:
    """Base configuration for a provider instance."""
    name: str           # User-defined display name for this account
    provider_type: str  # e.g. "google_drive"
    extra: dict = field(default_factory=dict)  # Provider-specific fields


# File extensions considered compatible with FreeCAD
FREECAD_EXTENSIONS = frozenset({
    ".fcstd", ".fcstd1",
    ".step", ".stp",
    ".iges", ".igs",
    ".stl",
    ".obj",
    ".dxf",
    ".svg",
    ".brep", ".brp",
    ".3mf",
    ".ifc",
    ".wrl", ".vrml",
    ".dat",
    ".inp",
    ".med",
    ".unv",
    ".bdf",
    ".nas",
})


def is_freecad_compatible(filename: str) -> bool:
    """Returns True if the file extension is compatible with FreeCAD."""
    ext = os.path.splitext(filename)[1].lower()
    return ext in FREECAD_EXTENSIONS


class CloudProvider(ABC):
    """
    Abstract base class that all cloud providers must implement.
    Each provider handles authentication, listing and downloading files.
    """

    def __init__(self, config: dict):
        self.config = config
        self._authenticated = False

    @property
    @abstractmethod
    def provider_type(self) -> str:
        """String identifier for this provider type (e.g. 'google_drive')."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name for this provider (e.g. 'Google Drive')."""
        ...

    @property
    def account_name(self) -> str:
        """User-configured name for this account instance."""
        return self.config.get("name", self.display_name)

    @abstractmethod
    def authenticate(self) -> bool:
        """
        Perform authentication (OAuth flow, credential validation, etc.).
        Returns True on success.
        Raises RuntimeError (or a subclass) on failure — never returns False.
        """
        ...

    @abstractmethod
    def is_authenticated(self) -> bool:
        """Returns True if the provider currently has valid credentials."""
        ...

    @abstractmethod
    def list_directory(self, path: str = "/") -> List[RemoteItem]:
        """
        List contents of a remote directory.
        `path` is provider-specific (folder ID for Google Drive, key prefix for S3, etc.)
        Returns a list of RemoteItem instances.
        """
        ...

    @abstractmethod
    def download_file(self, remote_item: RemoteItem, local_path: str) -> str:
        """
        Download a remote file to `local_path`.
        Returns the final local path where the file was saved.
        """
        ...

    @abstractmethod
    def upload_file(self, local_path: str, remote_dir_path: str) -> bool:
        """
        Upload a local file to the given remote directory. Returns True on success.

        IMPORTANT: this method is required by the SyncManager auto-sync feature.
        Every provider MUST implement it so that files opened from the cloud are
        automatically re-uploaded when the user saves them in FreeCAD.
        If upload is temporarily unsupported, raise NotImplementedError with a
        clear message — do NOT leave the default base-class stub.
        """

    def create_folder(self, remote_dir_path: str, folder_name: str) -> bool:
        """Create a new folder inside remote_dir_path. Returns True on success."""
        raise NotImplementedError(f"{self.display_name} does not support folder creation.")

    def delete_item(self, remote_item: "RemoteItem") -> bool:
        """Delete a file or folder. Returns True on success."""
        raise NotImplementedError(f"{self.display_name} does not support deletion.")

    def filter_freecad_files(self, items: List[RemoteItem]) -> List[RemoteItem]:
        """Filter a list of RemoteItems to only include FreeCAD-compatible files and folders."""
        return [
            item for item in items
            if item.is_dir or is_freecad_compatible(item.name)
        ]

    @classmethod
    def get_config_schema(cls) -> dict:
        """
        Returns a JSON-schema-like dict describing the fields needed to configure this provider.
        Used by the UI to render the correct form fields.

        NOTABLE-5 fix: this is a classmethod so that provider_dialog.py can call
        cls.get_config_schema() directly without instantiating with an empty config,
        which was fragile (any provider accessing self.config in __init__ would crash).

        Example:
            {
                "fields": [
                    {"key": "bucket", "label": "Bucket Name", "type": "text", "required": True},
                    {"key": "region", "label": "AWS Region", "type": "text", "required": True},
                ]
            }
        """
        return {"fields": []}

    def __repr__(self):
        return f"<{self.__class__.__name__} name={self.account_name!r}>"
