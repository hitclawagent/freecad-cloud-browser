# providers/s3.py
# Amazon S3 provider using boto3

import os
import logging
from typing import List

from .base import CloudProvider, RemoteItem

logger = logging.getLogger(__name__)


class S3Provider(CloudProvider):
    """Amazon S3 cloud provider via boto3."""

    _PROVIDER_TYPE = "s3"
    _DISPLAY_NAME = "Amazon S3"

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
                {"key": "access_key",    "label": "Access Key ID",     "type": "text",     "required": True},
                {"key": "secret_key",    "label": "Secret Access Key", "type": "password", "required": True},
                {"key": "bucket",        "label": "Bucket Name",       "type": "text",     "required": True},
                {"key": "region",        "label": "AWS Region",        "type": "text",     "required": False, "hint": "e.g. us-east-1 (leave empty for generic S3-compatible storage)"},
                {"key": "endpoint_url",  "label": "Endpoint URL",      "type": "text",     "required": False, "hint": "Optional: for S3-compatible storage (MinIO, etc.)"},
            ]
        }

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self) -> bool:
        try:
            import boto3

            kwargs = dict(
                aws_access_key_id=self.config["access_key"],
                aws_secret_access_key=self.config["secret_key"],
                region_name=self.config.get("region") or "us-east-1",
            )
            if self.config.get("endpoint_url"):
                kwargs["endpoint_url"] = self.config["endpoint_url"]

            self._client = boto3.client("s3", **kwargs)
            # Validate with list_objects instead of head_bucket (more compatible with S3-generic storage)
            self._client.list_objects_v2(Bucket=self.config["bucket"], MaxKeys=1)
            logger.info("S3 authenticated successfully (bucket: %s).", self.config["bucket"])
            self._authenticated = True
            return True

        except Exception as e:
            self._authenticated = False
            raise RuntimeError(f"S3 authentication failed: {e}") from e

    def is_authenticated(self) -> bool:
        return self._authenticated and self._client is not None

    # ------------------------------------------------------------------
    # Directory listing
    # ------------------------------------------------------------------

    def list_directory(self, path: str = "") -> List[RemoteItem]:
        """
        `path` is an S3 key prefix (e.g. '' for root, 'projects/', 'projects/v2/').
        """
        if not self.is_authenticated():
            self.authenticate()

        bucket = self.config["bucket"]
        prefix = path if path.endswith("/") or path == "" else path + "/"
        if prefix == "/":
            prefix = ""

        paginator = self._client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/")

        items = []
        for page in pages:
            # Common prefixes = sub-directories
            for cp in page.get("CommonPrefixes", []):
                folder_path = cp["Prefix"]
                folder_name = folder_path.rstrip("/").split("/")[-1]
                items.append(RemoteItem(
                    name=folder_name,
                    path=folder_path,
                    is_dir=True,
                ))

            # Objects = files
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key == prefix:
                    continue  # Skip the directory marker itself
                filename = key.split("/")[-1]
                if not filename:
                    continue
                items.append(RemoteItem(
                    name=filename,
                    path=key,
                    is_dir=False,
                    size=obj.get("Size"),
                    # BUG-7 fix: use isoformat() to preserve timezone info.
                    # boto3 returns timezone-aware UTC datetimes; strftime() was
                    # silently dropping the offset, making the string ambiguous.
                    modified=obj["LastModified"].isoformat() if obj.get("LastModified") else None,
                ))

        return self.filter_freecad_files(items)

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download_file(self, remote_item: RemoteItem, local_path: str) -> str:
        # Guard against empty dirname (e.g. when local_path has no directory component)
        local_dir = os.path.dirname(local_path)
        if local_dir:
            os.makedirs(local_dir, exist_ok=True)
        self._client.download_file(self.config["bucket"], remote_item.path, local_path)
        return local_path

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_file(self, local_path: str, remote_dir_path: str) -> bool:
        filename = os.path.basename(local_path)
        prefix = remote_dir_path.rstrip("/") + "/" if remote_dir_path else ""
        remote_key = f"{prefix}{filename}"
        self._client.upload_file(local_path, self.config["bucket"], remote_key)
        return True

    def create_folder(self, remote_dir_path: str, folder_name: str) -> bool:
        """S3 has no real folders — create a zero-byte key ending with '/'."""
        prefix = remote_dir_path.rstrip("/") + "/" if remote_dir_path else ""
        key = f"{prefix}{folder_name.strip('/')}/"
        self._client.put_object(Bucket=self.config["bucket"], Key=key, Body=b"")
        return True

    def delete_item(self, remote_item) -> bool:
        if remote_item.is_dir:
            # Delete all objects under the prefix.
            # delete_objects accepts at most 1000 keys per call (AWS limit).
            paginator = self._client.get_paginator("list_objects_v2")
            prefix = remote_item.path if remote_item.path.endswith("/") else remote_item.path + "/"
            for page in paginator.paginate(Bucket=self.config["bucket"], Prefix=prefix):
                objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                if not objects:
                    continue
                # Batch in chunks of 1000 to stay within the AWS API limit
                for i in range(0, len(objects), 1000):
                    batch = objects[i:i + 1000]
                    self._client.delete_objects(
                        Bucket=self.config["bucket"],
                        Delete={"Objects": batch},
                    )
        else:
            self._client.delete_object(Bucket=self.config["bucket"], Key=remote_item.path)
        return True
