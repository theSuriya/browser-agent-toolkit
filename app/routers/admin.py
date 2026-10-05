"""Admin endpoints: mint, list and revoke API keys (admin scope only)."""

from fastapi import APIRouter, Depends, Response, status

from app.auth import KeyStore, Principal, can_grant, get_key_store, require_admin
from app.errors import AppError
from app.schemas import KeyCreate, KeyCreated, KeyInfo

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/keys", response_model=KeyCreated, status_code=status.HTTP_201_CREATED)
async def create_key(
    payload: KeyCreate,
    principal: Principal = Depends(require_admin),
    store: KeyStore = Depends(get_key_store),
) -> KeyCreated:
    """Mint a key in the caller's organization. The plaintext is returned once."""
    # An admin may not grant a scope they do not hold.
    requested = set(payload.scopes or ["use"])
    if not can_grant(principal.scopes, requested):
        raise AppError(403, "forbidden", "You cannot grant scopes you do not hold.")
    plaintext, record = await store.mint(
        payload.name,
        scopes=list(requested),
        rate_limit=payload.rate_limit,
        org_id=principal.org_id,
    )
    return KeyCreated(id=record.id, name=record.name, key=plaintext, scopes=sorted(record.scopes))


@router.get("/keys", response_model=list[KeyInfo])
async def list_keys(
    principal: Principal = Depends(require_admin),
    store: KeyStore = Depends(get_key_store),
) -> list[KeyInfo]:
    return [KeyInfo(**row) for row in await store.list_public(principal.org_id)]


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    key_id: str,
    principal: Principal = Depends(require_admin),
    store: KeyStore = Depends(get_key_store),
) -> Response:
    if not await store.revoke(principal.org_id, key_id):
        raise AppError(404, "not_found", "Unknown key id.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
