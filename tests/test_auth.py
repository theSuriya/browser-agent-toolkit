"""API-key authentication and per-key session isolation."""

import pytest
from fastapi.testclient import TestClient


def _build_app(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("REQUIRE_AUTH", "true")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "keys.db"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    return create_app()


@pytest.fixture
def auth_app(tmp_path, monkeypatch):
    app = _build_app(tmp_path, monkeypatch, API_KEYS="bootstrap-admin-key", RATE_LIMIT_PER_MIN="100")
    yield app
    from app.config import get_settings

    get_settings.cache_clear()


ADMIN = {"Authorization": "Bearer bootstrap-admin-key"}


def _mint(client, name="tester"):
    resp = client.post("/admin/keys", json={"name": name}, headers=ADMIN)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["key"], body["id"]


def test_missing_key_is_401(auth_app):
    with TestClient(auth_app) as client:
        response = client.get("/sessions")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_invalid_key_is_401(auth_app):
    with TestClient(auth_app) as client:
        response = client.get("/sessions", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_health_is_public(auth_app):
    with TestClient(auth_app) as client:
        assert client.get("/health").status_code == 200


def test_valid_key_is_allowed(auth_app):
    with TestClient(auth_app) as client:
        key, _ = _mint(client)
        response = client.get("/sessions", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200
    assert response.json() == []


def test_admin_endpoint_requires_admin_scope(auth_app):
    with TestClient(auth_app) as client:
        key, _ = _mint(client, name="plain")  # default scope is "use", not "admin"
        response = client.get("/admin/keys", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_revoked_key_is_rejected(auth_app):
    with TestClient(auth_app) as client:
        key, key_id = _mint(client)
        headers = {"Authorization": f"Bearer {key}"}
        assert client.get("/sessions", headers=headers).status_code == 200
        assert client.delete(f"/admin/keys/{key_id}", headers=ADMIN).status_code == 204
        assert client.get("/sessions", headers=headers).status_code == 401


def test_sessions_are_isolated_per_key(auth_app):
    with TestClient(auth_app) as client:
        key_a, _ = _mint(client, "key-a")
        key_b, _ = _mint(client, "key-b")
        ha = {"Authorization": f"Bearer {key_a}"}
        hb = {"Authorization": f"Bearer {key_b}"}

        created = client.post("/sessions", headers=ha)
        assert created.status_code == 201, created.text
        sid = created.json()["id"]

        # B cannot see A's session ...
        assert all(item["id"] != sid for item in client.get("/sessions", headers=hb).json())
        # ... and cannot drive or delete it: it is reported as missing (404).
        driven = client.post(
            f"/sessions/{sid}/actions",
            json={"tool": "snapshot", "arguments": {}},
            headers=hb,
        )
        assert driven.status_code == 404
        assert client.delete(f"/sessions/{sid}", headers=hb).status_code == 404
        # A still can.
        assert client.delete(f"/sessions/{sid}", headers=ha).status_code == 204


def test_rate_limit_returns_429(tmp_path, monkeypatch):
    app = _build_app(tmp_path, monkeypatch, API_KEYS="rl-key", RATE_LIMIT_PER_MIN="2")
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer rl-key"}
            assert client.get("/sessions", headers=headers).status_code == 200
            assert client.get("/sessions", headers=headers).status_code == 200
            limited = client.get("/sessions", headers=headers)
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "rate_limited"
    finally:
        from app.config import get_settings

        get_settings.cache_clear()
