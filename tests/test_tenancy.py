"""Self-serve signup, per-organization key management and org isolation (no browser)."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "true")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "keys.db"))
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    application = create_app()
    yield application
    get_settings.cache_clear()


def _signup(client, email, org):
    resp = client.post("/accounts/signup", json={"email": email, "org_name": org})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body, {"Authorization": f"Bearer {body['key']}"}


def test_signup_is_public_and_issues_working_key(app):
    with TestClient(app) as client:
        body, headers = _signup(client, "owner@acme.test", "Acme")
        assert body["plan"] == "free"
        assert body["key"].startswith("bat_")
        # The issued key authenticates and lists empty sessions.
        assert client.get("/sessions", headers=headers).status_code == 200
        me = client.get("/accounts/me", headers=headers).json()
        assert me["org_id"] == body["org_id"]
        assert me["plan"] == "free"


def test_signup_rejects_bad_email(app):
    with TestClient(app) as client:
        resp = client.post("/accounts/signup", json={"email": "nope", "org_name": "X"})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_failed"


def test_keys_are_scoped_to_the_organization(app):
    with TestClient(app) as client:
        a, ha = _signup(client, "a@acme.test", "Org A")
        b, hb = _signup(client, "b@beta.test", "Org B")

        created = client.post("/accounts/keys", json={"name": "ci"}, headers=ha)
        assert created.status_code == 201, created.text
        a_key_id = created.json()["id"]

        b_key_ids = {k["id"] for k in client.get("/accounts/keys", headers=hb).json()}
        assert a_key_id not in b_key_ids  # B cannot see A's key

        # B cannot revoke A's key: reported as missing so existence is not leaked.
        assert client.delete(f"/accounts/keys/{a_key_id}", headers=hb).status_code == 404
        # A can revoke its own key.
        assert client.delete(f"/accounts/keys/{a_key_id}", headers=ha).status_code == 204


def test_cannot_grant_scopes_you_do_not_hold(app):
    with TestClient(app) as client:
        _, headers = _signup(client, "owner@acme.test", "Acme")
        denied = client.post("/accounts/keys", json={"name": "bad", "scopes": ["superuser"]}, headers=headers)
        assert denied.status_code == 403

        # A use-only key cannot manage keys at all.
        limited = client.post("/accounts/keys", json={"name": "readonly", "scopes": ["use"]}, headers=headers)
        assert limited.status_code == 201
        limited_headers = {"Authorization": f"Bearer {limited.json()['key']}"}
        assert client.get("/accounts/keys", headers=limited_headers).status_code == 403


def test_usage_endpoint_reports_plan_and_quota(app):
    with TestClient(app) as client:
        _, headers = _signup(client, "owner@acme.test", "Acme")
        usage = client.get("/usage", headers=headers).json()
        assert usage["plan"] == "free"
        assert usage["browser_seconds"]["used"] == 0
        assert usage["browser_seconds"]["included"] == 1800
        assert usage["agent_tasks"]["included"] == 5


def test_readiness_endpoint(app):
    with TestClient(app) as client:
        assert client.get("/ready").status_code == 200
