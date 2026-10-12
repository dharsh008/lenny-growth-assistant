"""API-level tests on SQLite with a fake LLM: persistence, session isolation, routing, grounding gate,
artifact sanitisation, structured errors, provider failure handling."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.core.errors import ProviderUnavailable
from tests.conftest import write_fixture


class FakeLLM:
    provider, model = "ollama", "fake"

    def __init__(self, reply):
        self.reply = reply

    async def stream(self, system, prompt):
        r = self.reply(system, prompt) if callable(self.reply) else self.reply
        for i in range(0, len(r), 30):
            yield r[i:i + 30]

    async def complete(self, system, prompt):
        return "".join([c async for c in self.stream(system, prompt)])


def reply(system, prompt):
    if "[[task:artifact_html]]" in system:
        return 'Here.<artifact type="html" title="Card"><p onclick="x()">Hi</p><script>alert(1)</script></artifact>'
    if "[[task:artifact_md]]" in system:
        return 'Doc:<artifact type="markdown" title="Doc"># Doc\n\n- point [1]\n</artifact>'
    if "[[task:essay_plan]]" in system:
        return "{}"
    if "[[task:essay_draft]]" in system:
        return "# Essay\n\nHook.\n\n## A\n\n**x y z** [1]\n\n- a\n- b\n- c\n\n## Takeaway\n\nDo it [2] [3]."
    if "[[task:condense]]" in system:
        return "product market fit survey"
    return "Ada says use a survey [1]. Also [9]."


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import os
    from pathlib import Path
    write_fixture(Path(os.environ["TRANSCRIPTS_DIR"]))
    from app.main import create_app
    app = create_app()
    with TestClient(app) as c:
        async def ingest_now():
            from app.ingest.service import ingest
            await ingest(download_if_missing=False)
            await app.state.retrieval.reload()
        import time
        for _ in range(100):  # wait for the background bootstrap (creates the schema)
            if app.state.bootstrap.get('stage') in ('empty_knowledge_base', 'ready', 'failed'):
                break
            time.sleep(0.1)
        c.portal.call(ingest_now)
        app.state.llm = FakeLLM(reply)

        async def resolve(requested):
            if getattr(app.state, "down", False):
                raise ProviderUnavailable("Ollama not reachable", details={"provider": "ollama"})
            return app.state.llm, None
        app.state.registry.resolve = resolve
        yield c


def H(user="user-aaaa-1111"):
    return {"X-User-Id": user}


def turn(client, sid, content, mode="auto", user="user-aaaa-1111"):
    r = client.post(f"/api/sessions/{sid}/messages/stream", json={"content": content, "mode": mode}, headers=H(user))
    assert r.status_code == 200, r.text
    events = {}
    for frame in r.text.split("\n\n"):
        lines = [l for l in frame.split("\n") if not l.startswith(":")]
        if len(lines) == 2:
            events.setdefault(lines[0][7:], []).append(json.loads(lines[1][6:]))
    return events


def new_session(client, user="user-aaaa-1111"):
    return client.post("/api/sessions", json={}, headers=H(user)).json()["id"]


def test_health_and_readiness(client):
    assert client.get("/api/health").json()["status"] == "ok"
    body = client.get("/api/health/ready").json()
    assert body["status"] == "ready" and body["knowledge_base"]["chunks"] > 0 and "providers" in body["llm"]


def test_user_header_validation_and_structured_errors(client):
    r = client.post("/api/sessions", json={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request" and r.json()["error"]["request_id"]
    sid = new_session(client)
    r = client.post(f"/api/sessions/{sid}/messages/stream", json={"content": ""}, headers=H())
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert client.get("/api/sessions/nope", headers=H()).status_code == 404


def test_grounded_answer_persisted_with_validated_citations(client):
    sid = new_session(client)
    ev = turn(client, sid, "How do I measure product-market fit with a survey?")
    msg = ev["done"][0]["message"]
    assert msg["route"] == "qa" and "[9]" not in msg["content"]  # hallucinated citation removed
    assert msg["sources"] and msg["sources"][0]["guest"] and msg["sources"][0]["url"].startswith("https://www.youtube.com")
    detail = client.get(f"/api/sessions/{sid}", headers=H()).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"] and detail["messages"][1]["created_at"]


def test_sessions_are_isolated_between_sessions_and_users(client):
    a, b = new_session(client), new_session(client)
    turn(client, a, "How do I measure product-market fit with a survey?")
    assert len(client.get(f"/api/sessions/{b}", headers=H()).json()["messages"]) == 0
    assert client.get(f"/api/sessions/{a}", headers=H("other-user-2222")).status_code == 404


def test_off_topic_is_refused_without_calling_the_llm(client):
    sid = new_session(client)
    ev = turn(client, sid, "zzzxqv qwertyuiop blorptastic")
    assert ev["done"][0]["message"]["route"] == "refusal" and "provider" not in ev


def test_llm_not_supported_sentinel_becomes_refusal(client):
    client.app.state.llm = FakeLLM("NOT_SUPPORTED")
    try:
        sid = new_session(client)
        ev = turn(client, sid, "How do I measure product-market fit with a survey?")
        assert ev["done"][0]["message"]["route"] == "refusal"
        assert not any("NOT_SUPPORTED" in d["text"] for d in ev.get("delta", []))
    finally:
        client.app.state.llm = FakeLLM(reply)


def test_html_artifact_is_sanitised_before_storage(client):
    sid = new_session(client)
    ev = turn(client, sid, "Create an HTML card of product-market fit survey")
    art = ev["artifact"][0]["artifact"]
    assert art["kind"] == "html" and art["sanitized"]
    assert "<script" not in art["content"] and "onclick" not in art["content"]
    assert art["report"]["sanitizer"]["removed"]["script"] == 1
    stored = client.get(f"/api/artifacts/{art['id']}", headers=H()).json()
    assert "<script" not in stored["content"]
    dl = client.get(f"/api/artifacts/{art['id']}/download", headers=H())
    assert "sandbox" in dl.headers["content-security-policy"]


def test_markdown_artifact_and_followup_context(client):
    sid = new_session(client)
    turn(client, sid, "How do I measure product-market fit with a survey?")
    ev = turn(client, sid, "make a markdown checklist of that")
    assert ev["artifact"][0]["artifact"]["kind"] == "markdown"
    assert ev["retrieval"][0]["strength"] in ("strong", "weak")  # follow-up reused conversation topic


def test_ship30_route_produces_skill_report(client):
    sid = new_session(client)
    ev = turn(client, sid, "Write a Ship 30 for 30 essay on product-market fit survey")
    art = ev["artifact"][0]["artifact"]
    assert art["skill"] == "ship30" and "skill_lint" in art["report"] and "### Sources" in art["content"]
    assert "plan" in ev


def test_provider_down_returns_structured_error_and_persists_it(client):
    client.app.state.down = True
    try:
        sid = new_session(client)
        ev = turn(client, sid, "How do I measure product-market fit with a survey?")
        assert ev["error"][0]["code"] == "provider_unavailable"
        msgs = client.get(f"/api/sessions/{sid}", headers=H()).json()["messages"]
        assert msgs[0]["role"] == "user" and msgs[1]["route"] == "error"  # user message is never lost
    finally:
        client.app.state.down = False


def test_provider_listing_and_session_provider_switch(client):
    assert {p["name"] for p in client.get("/api/providers").json()} == {"ollama", "anthropic"}
    sid = new_session(client)
    r = client.patch(f"/api/sessions/{sid}", json={"provider": "anthropic"}, headers=H())
    assert r.json()["provider"] == "anthropic"
    assert client.patch(f"/api/sessions/{sid}", json={"provider": "bogus"}, headers=H()).status_code == 422


def test_delete_session_cascades(client):
    sid = new_session(client)
    turn(client, sid, "How do I measure product-market fit with a survey?")
    assert client.delete(f"/api/sessions/{sid}", headers=H()).status_code == 204
    assert client.get(f"/api/sessions/{sid}", headers=H()).status_code == 404
