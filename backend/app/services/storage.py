"""Object storage abstraction.

Local filesystem for development, S3-compatible for production. Uploaded
images live here (never in the database). Refs are opaque strings:
  local://<key>   or   s3://<bucket>/<key>
"""
import os
import uuid
from abc import ABC, abstractmethod

from app.config import settings


class StorageBackend(ABC):
    @abstractmethod
    def save(self, data: bytes, filename: str, content_type: str) -> str:
        """Persist bytes and return an opaque storage ref."""

    @abstractmethod
    def load(self, ref: str) -> bytes:
        """Retrieve bytes for a previously saved ref."""


class LocalStorage(StorageBackend):
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)

    def _safe_key(self, filename: str) -> str:
        # Strip any path components to avoid traversal.
        base = os.path.basename(filename or "upload")
        return f"{uuid.uuid4().hex}_{base}"

    def save(self, data: bytes, filename: str, content_type: str) -> str:
        key = self._safe_key(filename)
        with open(os.path.join(self.base_dir, key), "wb") as f:
            f.write(data)
        return f"local://{key}"

    def load(self, ref: str) -> bytes:
        key = ref.replace("local://", "", 1)
        path = os.path.join(self.base_dir, os.path.basename(key))
        with open(path, "rb") as f:
            return f.read()


class S3Storage(StorageBackend):
    def __init__(self):
        import boto3  # imported lazily so dev doesn't need boto3 configured

        self.client = boto3.client(
            "s3", region_name=settings.s3_region, endpoint_url=settings.s3_endpoint_url
        )
        self.bucket = settings.s3_bucket

    def save(self, data: bytes, filename: str, content_type: str) -> str:
        key = f"{uuid.uuid4().hex}_{os.path.basename(filename or 'upload')}"
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return f"s3://{self.bucket}/{key}"

    def load(self, ref: str) -> bytes:
        _, _, rest = ref.partition("s3://")
        bucket, _, key = rest.partition("/")
        obj = self.client.get_object(Bucket=bucket, Key=key)
        return obj["Body"].read()


def _build_storage() -> StorageBackend:
    if settings.storage_backend.lower() == "s3":
        return S3Storage()
    return LocalStorage(settings.storage_local_dir)


storage: StorageBackend = _build_storage()
