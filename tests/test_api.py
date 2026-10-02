import hashlib
import hmac
import json

import httpx
import pytest

from agentflow.agents import orchestrator
from agentflow.api.app import app
from agentflow.config import WorkflowConfig, get_settings
from agentflow.runner import open_persistence, start_run
from agentflow.schemas import Spec


class FakeQueue:
    def __init__(self):
        self.jobs: list[tuple[str, tuple, dict]] = []

    async def enqueue_job(self, fn, *args, **kwargs):
        self.jobs.append((fn, args, kwargs))


@pytest.fixture
async def client(tmp_path):
    async with open_persistence(in_memory=True) as p:
        app.state.persistence = p
        app.state.queue = FakeQueue()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_create_run_enqueues(client):
    r = await client.post("/runs", json={"prompt": "add login", "repo": "octo/shop"})
    assert r.status_code == 202
    fn, args, _ = app.state.queue.jobs[0]
    assert fn == "start_run_job" and args[0] == "add login"
    assert args[1]["target_repo"]["owner"] == "octo"


async def test_bearer_token_enforced(client, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "s3cret")
    get_settings.cache_clear()
    assert (await client.post("/runs", json={"prompt": "abc"})).status_code == 401
    r = await client.post("/runs", json={"prompt": "abc"}, headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 202


def _signed(secret: str, payload: dict) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Hub-Signature-256": sig, "X-GitHub-Event": "issues", "Content-Type": "application/json"}


async def test_github_issue_label_triggers_run(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "hook")
    get_settings.cache_clear()
    payload = {
        "action": "labeled",
        "label": {"name": "agentflow"},
        "issue": {"number": 7, "title": "Add search", "body": "full-text search over products"},
        "repository": {"full_name": "octo/shop", "name": "shop", "default_branch": "main"},
    }
    body, headers = _signed("hook", payload)
    r = await client.post("/webhooks/github", content=body, headers=headers)
    assert r.json()["status"] == "queued"
    fn, args, _ = app.state.queue.jobs[-1]
    assert fn == "start_run_job" and args[3] == 7 and args[4] == "github"

    bad = await client.post("/webhooks/github", content=body, headers={**headers, "X-Hub-Signature-256": "sha256=0"})
    assert bad.status_code == 401

    other, headers = _signed("hook", {**payload, "label": {"name": "bug"}})
    assert (await client.post("/webhooks/github", content=other, headers=headers)).json()["status"] == "ignored"


async def test_approval_flow_and_telegram_button(client, tmp_path, monkeypatch):
    async def fake_spec(prompt, context, previous=None, feedback=None):
        return Spec(
            id="s", title="T", goal="g", constraints=[], acceptance_criteria=["a", "b"], technical_notes="", out_of_scope=[]
        )

    monkeypatch.setattr(orchestrator, "generate_spec", fake_spec)
    (tmp_path / "a.py").write_text("x = 1\n")

    assert (await client.post("/runs/nope/approval", json={"approved": True})).status_code == 409

    await start_run(app.state.persistence, "p", WorkflowConfig(local_path=str(tmp_path)), thread_id="t-api")
    state = (await client.get("/runs/t-api")).json()
    assert state["status"] == "awaiting_approval" and state["pending_approval"]["type"] == "spec_approval"

    r = await client.post("/webhooks/telegram", json={"callback_query": {"id": "1", "data": "approve|t-api"}})
    assert r.status_code == 200
    fn, args, kwargs = app.state.queue.jobs[-1]
    assert fn == "resume_run_job" and args[:2] == ("t-api", True)
    assert kwargs["_job_id"].startswith("resume:t-api:")

    await client.post("/webhooks/telegram", json={"message": {"text": "/reject t-api make it async"}})
    assert app.state.queue.jobs[-1][1] == ("t-api", False, "make it async")


async def test_metrics_exposed(client):
    await client.get("/healthz")
    body = (await client.get("/metrics")).text
    assert "agentflow_http_requests_total" in body


async def test_search_exposes_dense_lexical_and_fused(client, monkeypatch):
    from pathlib import Path

    from langchain_core.embeddings import DeterministicFakeEmbedding

    from agentflow.rag.indexer import index_repo

    fixture = Path(__file__).parent / "fixtures" / "sample_repo"
    await index_repo(fixture, "shop", app.state.persistence.chunk_store, DeterministicFakeEmbedding(size=1024))
    assert (await client.get("/repos")).json() == {"repos": ["shop"]}

    body = (await client.get("/search", params={"repo": "shop", "q": "hash_password login", "k": 3})).json()
    assert set(body) >= {"dense", "lexical", "hybrid", "embeddings"}
    assert body["lexical"][0]["path"] == "shop/auth.py"
    assert len(body["hybrid"]) == 3 and body["hybrid"][0]["snippet"]


async def test_cors_allows_guide_origin(client):
    r = await client.options(
        "/healthz", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"}
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
