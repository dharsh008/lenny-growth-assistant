"""A tiny fake of both the Ollama HTTP API (/api/tags) and the Anthropic Messages
API (/v1/messages, streaming + non-streaming). Lets us exercise the *real* Claude
Agent SDK subprocess path in CI without a GPU or an API key.

Responses are chosen by a `[[task:<name>]]` marker that our prompts include, so
the full pipeline (route -> retrieve -> generate -> parse artifacts) is testable.
Run standalone:  python -m tests.mock_llm_server  (port 11999)"""
import json
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
CALLS: list[dict] = []
MODEL = "mock-model"
FAIL_NEXT = {"n": 0}  # set via POST /__fail to simulate upstream 500s


def _flatten(x) -> str:
    if isinstance(x, str):
        return x
    if isinstance(x, list):
        return " ".join(_flatten(i.get("text", "") if isinstance(i, dict) else i) for i in x)
    return str(x)


def answer_for(system: str, prompt: str) -> str:
    task = (re.search(r"\[\[task:([a-z_0-9]+)\]\]", system) or [None, "qa"])[1]
    if task == "qa":
        if "NOT_SUPPORTED_TEST" in prompt:
            return "NOT_SUPPORTED"
        return "Lenny's guests describe finding product-market fit as an iterative search [1]. Superhuman measured it with a survey [2]."
    if task == "condense":
        return "product-market fit survey method"
    if task == "essay_plan":
        return json.dumps({
            "headline": "7 Lessons On Finding Product-Market Fit From Lenny's Guests",
            "hook": "Most founders chase PMF like a finish line. The best ones treat it like a dial.",
            "audience": "early-stage product builders", "pattern": "Lessons", "takeaway": "Run a PMF survey this week.",
            "sections": [{"heading": f"Lesson {i}", "point": f"Point {i}", "source": 1} for i in range(1, 6)],
        })
    if task == "essay_draft":
        body = "\n\n".join(f"## Lesson {i}\n\n" + ("Teams learn fastest when they talk to users [1]. " * 40) for i in range(1, 6))
        return "# 7 Lessons On Finding Product-Market Fit\n\n**Most founders chase PMF like a finish line.**\n\n" + body + "\n\n## Takeaway\n\n- Run the survey [2]"
    if task == "artifact_md":
        return "Here is your doc.\n<artifact type=\"markdown\" title=\"PMF Checklist\">\n# PMF Checklist\n\n- Talk to users [1]\n- Run the survey [2]\n</artifact>\nLet me know what to tweak."
    if task == "artifact_html":
        return ("Here is a one-page summary.\n<artifact type=\"html\" title=\"PMF One-Pager\">\n<h1>PMF</h1><p style=\"color:#333\">Talk to users.</p>"
                "<script>alert(1)</script><img src=x onerror=alert(1)>\n</artifact>\nDone.")
    return "ok"


def _sse(events):
    for name, data in events:
        yield f"event: {name}\ndata: {json.dumps(data)}\n\n"


@app.get("/api/tags")
async def tags():
    return {"models": [{"name": MODEL}, {"name": f"{MODEL}:latest"}]}


@app.post("/__fail")
async def fail(n: int = 1):
    FAIL_NEXT["n"] = n
    return {"ok": True}


@app.get("/__calls")
async def calls():
    return CALLS


@app.post("/v1/messages/count_tokens")
async def count_tokens(request: Request):
    return {"input_tokens": 10}


@app.post("/v1/messages")
async def messages(request: Request):
    body = await request.json()
    system = _flatten(body.get("system", ""))
    prompt = " ".join(_flatten(m.get("content")) for m in body.get("messages", []) if m.get("role") == "user")
    CALLS.append({"system": system[:2000], "prompt": prompt[:4000], "model": body.get("model"), "stream": body.get("stream")})
    if FAIL_NEXT["n"] > 0:
        FAIL_NEXT["n"] -= 1
        return JSONResponse({"type": "error", "error": {"type": "api_error", "message": "mock upstream failure"}}, status_code=500)
    if "[[task:" not in system:  # background helper calls from the CLI (titles, etc.)
        text = "ok"
    else:
        text = answer_for(system, prompt)
    msg_id = "msg_mock"
    usage = {"input_tokens": 10, "output_tokens": max(1, len(text) // 4)}
    if not body.get("stream"):
        return {"id": msg_id, "type": "message", "role": "assistant", "model": body.get("model", MODEL),
                "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None, "usage": usage}
    pieces = [text[i : i + 24] for i in range(0, len(text), 24)] or [""]
    events = [
        ("message_start", {"type": "message_start", "message": {"id": msg_id, "type": "message", "role": "assistant", "model": body.get("model", MODEL), "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("ping", {"type": "ping"}),
        *[("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": p}}) for p in pieces],
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": usage["output_tokens"]}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return StreamingResponse(_sse(events), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=11999)
