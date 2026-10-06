"""Serving for locally-stored artifacts through expiring HMAC-signed URLs.

The URL is the credential: it is reachable without a bearer token (like a presigned
S3 link) but only while the signature is valid. When artifacts live in S3 the store
hands out native presigned URLs and this route is not used.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response

from app.errors import AppError
from app.storage import LocalArtifactStore

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("/{key:path}")
async def download_artifact(
    key: str,
    request: Request,
    exp: int = Query(..., description="Expiry, unix seconds"),
    sig: str = Query(..., description="HMAC signature"),
) -> Response:
    store = request.app.state.storage
    if not isinstance(store, LocalArtifactStore):
        raise AppError(404, "not_found", "Artifacts are served directly by object storage.")
    if not store.verify(key, exp, sig):
        raise AppError(403, "invalid_signature", "This artifact link is invalid or has expired.")
    data = await store.load(key)
    if data is None:
        raise AppError(404, "not_found", "Artifact not found.")
    return Response(content=data, media_type=store.content_type(key))
