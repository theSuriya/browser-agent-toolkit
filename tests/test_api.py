"""HTTP contract tests that need no browser."""

from fastapi.testclient import TestClient

from app.main import app


def test_health():
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_tools_are_discoverable():
    with TestClient(app) as client:
        names = {tool["name"] for tool in client.get("/tools").json()["tools"]}
    assert {"navigate", "snapshot", "click", "type_text", "screenshot"} <= names


def test_sitecheck_rejects_bad_url():
    with TestClient(app) as client:
        response = client.post("/sitecheck", json={"url": "not-a-url"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_unknown_session_is_404():
    with TestClient(app) as client:
        response = client.post("/sessions/nope/actions", json={"tool": "snapshot", "arguments": {}})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


def test_unknown_tool_is_400():
    with TestClient(app) as client:
        response = client.post("/sessions/whatever/actions", json={"tool": "teleport", "arguments": {}})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unknown_tool"


def test_session_list_empty_initially():
    with TestClient(app) as client:
        response = client.get("/sessions")
    assert response.status_code == 200
    assert response.json() == []
