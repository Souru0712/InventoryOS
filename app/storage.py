"""Where uploaded invoice photos live: a directory in development, a bucket or volume deployed.

The database stores a key such as "3/<sha256>.jpg", never a filesystem path, so the same row
works wherever the file itself is kept.
"""

from __future__ import annotations

import logging
import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import get_settings

log = logging.getLogger(__name__)


class StorageError(RuntimeError):
    """A stored file could not be written, read, or reached."""


class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    def get(self, key: str) -> bytes: ...

    def check(self) -> None: ...


class LocalStorage:
    """A directory. Deployed, it has to be a mounted persistent volume or a redeploy empties it."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise StorageError(f"storage key {key!r} escapes the upload directory")
        return path

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as exc:
            raise StorageError(f"could not write {key}: {exc}") from exc

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except OSError as exc:
            raise StorageError(f"could not read {key}: {exc}") from exc

    def check(self) -> None:
        # One probe per worker: web processes sharing a volume must not delete each other's.
        probe = self._path(f".readyz-{os.getpid()}-{threading.get_ident()}")
        try:
            probe.parent.mkdir(parents=True, exist_ok=True)
            probe.write_bytes(b"ok")
            readback = probe.read_bytes()
            probe.unlink()
        except OSError as exc:
            raise StorageError(f"upload directory {self.root} is not writable: {exc}") from exc
        if readback != b"ok":
            raise StorageError(f"upload directory {self.root} did not return what was written")


class S3Storage:
    """Any S3-compatible bucket. Credentials fall back to boto3's own chain when not given."""

    def __init__(self, bucket: str, prefix: str = "", client=None, **client_options: str):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        if client is None:
            import boto3

            client = boto3.client(
                "s3", **{name: value for name, value in client_options.items() if value}
            )
        self.client = client

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            self.client.put_object(
                Bucket=self.bucket, Key=self._key(key), Body=data, ContentType=content_type
            )
        except Exception as exc:
            raise StorageError(f"could not upload {self._key(key)}: {exc}") from exc

    def get(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()
        except Exception as exc:
            raise StorageError(f"could not download {self._key(key)}: {exc}") from exc

    def check(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception as exc:
            raise StorageError(f"bucket {self.bucket} is not reachable: {exc}") from exc


@lru_cache
def get_storage() -> Storage:
    settings = get_settings()
    if settings.storage_backend == "s3":
        return S3Storage(
            settings.s3_bucket,
            settings.s3_prefix,
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
        )
    return LocalStorage(settings.upload_dir)
