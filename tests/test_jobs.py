"""Job API contract, async worker execution, retry and webhook delivery."""

import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from app import jobs as job_store
from app import repository
from app.db import init_models, make_engine, make_sessionmaker
from app.queue import InProcessJobQueue
from app.worker import Worker


# --- HTTP contract ----------------------------------------------------------
@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "true")
    monkeypatch.setenv("API_KEYS", "bootstrap-admin-key")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "keys.db"))
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("RUN_INLINE_WORKER", "false")
    monkeypatch.setenv("REDIS_URL", "")

    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client


def _signup(client, email, org):
    response = client.post("/accounts/signup", json={"email": email, "org_name": org})
    assert response.status_code in (200, 201), response.text
    return response.json()


def _auth(account):
    return {"Authorization": f"Bearer {account['key']}"}


def test_jobs_require_authentication(app_client):
    assert app_client.get("/jobs").status_code == 401
    assert (
        app_client.post("/jobs", json={"kind": "sitecheck", "input": {"url": "https://x.test"}}).status_code
        == 401
    )


def test_create_job_returns_202_and_is_listed(app_client):
    account = _signup(app_client, "jobs@acme.test", "Acme")
    headers = _auth(account)

    response = app_client.post(
        "/jobs", json={"kind": "sitecheck", "input": {"url": "https://example.com"}}, headers=headers
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["kind"] == "sitecheck"

    listed = app_client.get("/jobs", headers=headers).json()
    assert [job["id"] for job in listed["jobs"]] == [body["id"]]


def test_invalid_job_input_is_rejected(app_client):
    account = _signup(app_client, "bad@acme.test", "Bad")
    response = app_client.post("/jobs", json={"kind": "agent", "input": {}}, headers=_auth(account))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_unknown_job_is_404(app_client):
    account = _signup(app_client, "missing@acme.test", "Missing")
    assert app_client.get("/jobs/nope", headers=_auth(account)).status_code == 404


def test_jobs_are_isolated_between_orgs(app_client):
    first = _signup(app_client, "one@acme.test", "One")
    second = _signup(app_client, "two@acme.test", "Two")
    job_id = app_client.post(
        "/jobs",
        json={"kind": "sitecheck", "input": {"url": "https://example.com"}},
        headers=_auth(first),
    ).json()["id"]
    assert app_client.get(f"/jobs/{job_id}", headers=_auth(second)).status_code == 404


# --- Worker execution -------------------------------------------------------
def _capture_server(sink: dict):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server API
            length = int(self.headers.get("content-length", 0))
            sink["body"] = self.rfile.read(length)
            sink["signature"] = self.headers.get("x-bat-signature")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_args):  # silence the test server
            return

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread, f"http://127.0.0.1:{httpd.server_port}/hook"


async def _worker_engine(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/worker.db")
    await init_models(engine)
    sessionmaker = make_sessionmaker(engine)
    async with sessionmaker() as session:
        await repository.seed_plans(session)
        org = await repository.create_organization(session, "Worker Org", plan_code="developer")
        await session.commit()
        _plaintext, _record = await repository.create_api_key(session, org.id, "worker")
        await session.commit()
        org_id = org.id
    return engine, sessionmaker, org_id


async def test_worker_runs_job_records_usage_and_fires_webhook(tmp_path):
    engine, sessionmaker, org_id = await _worker_engine(tmp_path)
    received: dict = {}
    httpd, thread, url = _capture_server(received)
    try:
        async with sessionmaker() as session:
            job = await job_store.create_job(
                session,
                org_id=org_id,
                key_id=None,
                kind="agent",
                payload={"task": "buy milk"},
                callback_url=url,
            )
            envelope = job_store.envelope(job)
            job_id = job.id

        async def fake_executor(env, session_factory):
            return {
                "result": {"ok": True, "task": env["payload"]["task"]},
                "usage": {"agent_tasks": 1, "browser_seconds": 2.0},
            }

        queue = InProcessJobQueue()
        await queue.enqueue(envelope)
        worker = Worker(
            sessionmaker=sessionmaker, queue=queue, registry=None, executor=fake_executor, worker_id="w-test"
        )
        assert await worker.process_once(timeout=1.0) is True

        async with sessionmaker() as session:
            stored = await job_store.get_job(session, org_id, job_id)
            usage = await repository.usage_since(session, org_id, repository.month_start())

        assert stored.status == job_store.STATUS_SUCCEEDED
        assert stored.result == {"ok": True, "task": "buy milk"}
        assert stored.attempts == 1
        assert usage.get("agent_tasks") == 1.0
        assert usage.get("browser_seconds") == 2.0

        assert received.get("body"), "webhook was not delivered"
        payload = json.loads(received["body"])
        assert payload["status"] == "succeeded"
        assert payload["job_id"] == job_id
    finally:
        httpd.shutdown()
        thread.join()
        await engine.dispose()


async def test_worker_retries_once_then_succeeds(tmp_path):
    engine, sessionmaker, org_id = await _worker_engine(tmp_path)
    try:
        async with sessionmaker() as session:
            job = await job_store.create_job(
                session, org_id=org_id, key_id=None, kind="agent", payload={"task": "x"}
            )
            envelope = job_store.envelope(job)
            job_id = job.id

        calls = {"n": 0}

        async def flaky(env, session_factory):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("browser crashed")
            return {"result": {"ok": True}, "usage": {"agent_tasks": 1}}

        queue = InProcessJobQueue()
        await queue.enqueue(envelope)
        worker = Worker(sessionmaker=sessionmaker, queue=queue, registry=None, executor=flaky, worker_id="w-test")
        await worker.process_once(timeout=1.0)

        async with sessionmaker() as session:
            stored = await job_store.get_job(session, org_id, job_id)

        assert calls["n"] == 2
        assert stored.status == job_store.STATUS_SUCCEEDED
        assert stored.attempts == 2
    finally:
        await engine.dispose()


async def test_worker_fails_job_when_attempts_exhausted(tmp_path):
    engine, sessionmaker, org_id = await _worker_engine(tmp_path)
    try:
        async with sessionmaker() as session:
            job = await job_store.create_job(
                session, org_id=org_id, key_id=None, kind="agent", payload={"task": "x"}
            )
            envelope = job_store.envelope(job)
            job_id = job.id

        async def always_fail(env, session_factory):
            raise RuntimeError("boom")

        queue = InProcessJobQueue()
        await queue.enqueue(envelope)
        worker = Worker(
            sessionmaker=sessionmaker, queue=queue, registry=None, executor=always_fail, worker_id="w-test"
        )
        await worker.process_once(timeout=1.0)

        async with sessionmaker() as session:
            stored = await job_store.get_job(session, org_id, job_id)

        assert stored.status == job_store.STATUS_FAILED
        assert "boom" in (stored.error or "")
        assert stored.attempts == 2
    finally:
        await engine.dispose()


async def test_deliver_webhook_signs_the_body():
    received: dict = {}
    httpd, thread, url = _capture_server(received)
    try:
        ok = await job_store.deliver_webhook(
            url, {"job_id": "j1", "status": "succeeded"}, secret="shh", timeout=5
        )
    finally:
        httpd.shutdown()
        thread.join()

    assert ok is True
    expected = "sha256=" + hmac.new(b"shh", received["body"], hashlib.sha256).hexdigest()
    assert received["signature"] == expected
    assert json.loads(received["body"])["job_id"] == "j1"
