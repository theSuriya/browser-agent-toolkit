"""Admin endpoints: mint, list and revoke API keys (admin scope only)."""

from fastapi import APIRouter, Depends, Response, status

from app.auth import KeyStore, Principal, get_key_store, require_admin
from app.errors import AppError
from app.schemas import KeyCreate, KeyCreated, KeyInfo

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/keys", response_model=KeyCreated, status_code=status.HTTP_201_CREATED)
async def create_key(
    payload: KeyCreate,
    _: Principal = Depends(require_admin),
    store: KeyStore = Depends(get_key_store),
) -> KeyCreated:
    """Mint a key. The plaintext is returned once and never stored."""
    plaintext, record = store.mint(
        payload.name,
        scopes=payload.scopes or ["use"],
        rate_limit=payload.rate_limit,
    )
    return KeyCreated(id=record.id, name=record.name, key=plaintext, scopes=sorted(record.scopes))


@router.get("/keys", response_model=list[KeyInfo])
async def list_keys(
    _: Principal = Depends(require_admin),
    store: KeyStore = Depends(get_key_store),
) -> list[KeyInfo]:
    return [KeyInfo(**row) for row in store.list_public()]


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    key_id: str,
    _: Principal = Depends(require_admin),
    store: KeyStore = Depends(get_key_store),
) -> Response:
    if not store.revoke(key_id):
        raise AppError(404, "not_found", "Unknown key id.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
