import time
from urllib.parse import parse_qs, urlparse

import pytest

from app.storage import LocalArtifactStore


def _store(tmp_path, secret="secret"):
    return LocalArtifactStore(str(tmp_path), "http://testserver", secret)


def _params(url):
    query = parse_qs(urlparse(url).query)
    return int(query["exp"][0]), query["sig"][0]


async def test_save_then_signed_url_roundtrip(tmp_path):
    store = _store(tmp_path)
    await store.save("org1/shot.png", b"png-bytes")
    url = store.signed_url("org1/shot.png", 60)

    assert "/artifacts/org1/shot.png?" in url
    exp, sig = _params(url)
    assert store.verify("org1/shot.png", exp, sig) is True
    assert await store.load("org1/shot.png") == b"png-bytes"


async def test_tampered_or_wrong_key_signature_is_rejected(tmp_path):
    store = _store(tmp_path)
    await store.save("org1/shot.png", b"data")
    exp, sig = _params(store.signed_url("org1/shot.png", 60))

    assert store.verify("org1/shot.png", exp, "deadbeef") is False
    assert store.verify("org1/other.png", exp, sig) is False


async def test_expired_signature_is_rejected(tmp_path):
    store = _store(tmp_path)
    expired = int(time.time()) - 5
    assert store.verify("any.png", expired, store._sign("any.png", expired)) is False


async def test_different_secret_cannot_forge_a_signature(tmp_path):
    a = _store(tmp_path / "a", secret="secret-a")
    exp = int(time.time()) + 60
    assert a.verify("k.png", exp, a._sign("k.png", exp)) is True
    b = _store(tmp_path / "b", secret="secret-b")
    assert b.verify("k.png", exp, a._sign("k.png", exp)) is False


async def test_load_returns_none_for_missing_artifact(tmp_path):
    store = _store(tmp_path)
    assert await store.load("missing.png") is None


def test_path_traversal_is_blocked(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store._path("../escape.png")


def test_content_type_is_inferred_from_extension(tmp_path):
    store = _store(tmp_path)
    assert store.content_type("a/shot.png") == "image/png"
    assert store.content_type("a/har.har") == "application/json"
    assert store.content_type("a/blob.bin") == "application/octet-stream"
