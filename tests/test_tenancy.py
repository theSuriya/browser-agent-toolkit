"""Self-serve signup, per-organization key isolation and account context."""

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
def app(tmp_path, monkeypatch):
    application = _build_app(tmp_path, monkeypatch, API_KEYS="bootstrap-admin-key")
    yield application
    from app.config import get_settings

    get_settings.cache_clear()


def _signup(client, email: str, org: str) -> dict:
    resp = client.post("/accounts/signup", json={"email": email, "org_name": org})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_signup_is_public_and_returns_a_key(app):
    with TestClient(app) as client:
        body = _signup(client, "a@example.com", "Acme")
    assert body["key"].startswith("bat_")
    assert body["plan"] == "free"
    assert body["org_id"] and body["key_id"]


def test_signup_validates_email(app):
    with TestClient(app) as client:
        resp = client.post("/accounts/signup", json={"email": "not-an-email", "org_name": "X"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_failed"


def test_account_me_reflects_org_and_plan(app):
    with TestClient(app) as client:
        body = _signup(client, "a@example.com", "Acme")
        me = client.get("/accounts/me", headers={"Authorization": f"Bearer {body['key']}"})
    assert me.status_code == 200
    payload = me.json()
    assert payload["org_id"] == body["org_id"]
    assert payload["plan"] == "free"
    assert "admin" in payload["scopes"]


def test_usage_is_scoped_to_the_org(app):
    with TestClient(app) as client:
        body = _signup(client, "a@example.com", "Acme")
        usage = client.get("/usage", headers={"Authorization": f"Bearer {body['key']}"})
    assert usage.status_code == 200
    payload = usage.json()
    assert payload["plan"] == "free"
    assert payload["agent_tasks"]["used"] == 0
    assert payload["browser_seconds"]["included"] == 30 * 60


def test_keys_are_isolated_between_orgs(app):
    with TestClient(app) as client:
        a = _signup(client, "a@example.com", "Acme")
        b = _signup(client, "b@example.com", "Globex")
        keys_a = client.get("/accounts/keys", headers={"Authorization": f"Bearer {a['key']}"}).json()
        keys_b = client.get("/accounts/keys", headers={"Authorization": f"Bearer {b['key']}"}).json()
    ids_a = {k["id"] for k in keys_a}
    ids_b = {k["id"] for k in keys_b}
    assert ids_a.isdisjoint(ids_b)
    assert b["key_id"] not in ids_a
    assert a["key_id"] in ids_a
