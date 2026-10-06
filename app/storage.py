"""Artifact storage with expiring signed URLs.

Local disk (the default) writes files under ``ARTIFACTS_DIR`` and serves them through
``/artifacts/{key}`` guarded by an HMAC signature over the key and expiry. When
``S3_BUCKET`` is set, artifacts go to S3 and ``signed_url`` returns a native presigned
URL, so the local serving route is not used.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from pathlib import Path

_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".json": "application/json",
    ".har": "application/json",
    ".pdf": "application/pdf",
    ".html": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}


class LocalArtifactStore:
    kind = "local"

    def __init__(self, root: str, base_url: str, secret: str) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._base = base_url.rstrip("/")
        self._secret = secret.encode("utf-8")

    def _path(self, key: str) -> Path:
        candidate = (self._root / key).resolve()
        root = self._root.resolve()
        if root not in candidate.parents:
            raise ValueError("artifact key escapes the storage root")
        return candidate

    @staticmethod
    def content_type(key: str) -> str:
        return _CONTENT_TYPES.get(Path(key).suffix.lower(), "application/octet-stream")

    async def save(self, key: str, data: bytes, content_type: str | None = None) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)
        return key

    def _sign(self, key: str, exp: int) -> str:
        return hmac.new(self._secret, f"{key}:{exp}".encode("utf-8"), hashlib.sha256).hexdigest()

    def signed_url(self, key: str, ttl_seconds: int) -> str:
        exp = int(time.time()) + ttl_seconds
        return f"{self._base}/artifacts/{key}?exp={exp}&sig={self._sign(key, exp)}"

    def verify(self, key: str, exp: int, sig: str) -> bool:
        if exp < int(time.time()):
            return False
        return hmac.compare_digest(self._sign(key, exp), sig)

    async def load(self, key: str) -> bytes | None:
        path = self._path(key)
        if not path.is_file():
            return None
        return await asyncio.to_thread(path.read_bytes)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_file():
            await asyncio.to_thread(path.unlink)


class S3ArtifactStore:
    kind = "s3"

    def __init__(self, bucket: str, region: str = "", endpoint_url: str = "") -> None:
        self._bucket = bucket
        self._client_args: dict[str, str] = {"region_name": region} if region else {}
        if endpoint_url:
            self._client_args["endpoint_url"] = endpoint_url

    def _client(self):
        import boto3  # lazy: the SDK is only required when S3 is configured

        return boto3.client("s3", **self._client_args)

    async def save(self, key: str, data: bytes, content_type: str | None = None) -> str:
        def _put() -> None:
            self._client().put_object(
                Bucket=self._bucket, Key=key, Body=data,
                ContentType=content_type or "application/octet-stream",
            )

        await asyncio.to_thread(_put)
        return key

    def signed_url(self, key: str, ttl_seconds: int) -> str:
        return self._client().generate_presigned_url(
            "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=ttl_seconds
        )

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client().delete_object, Bucket=self._bucket, Key=key)


def build_store(settings) -> LocalArtifactStore | S3ArtifactStore:
    if settings.s3_bucket:
        return S3ArtifactStore(settings.s3_bucket, settings.s3_region, settings.s3_endpoint_url)
    secret = settings.artifact_signing_secret or "insecure-development-secret"
    return LocalArtifactStore(settings.artifacts_dir, settings.artifact_base_url, secret)
