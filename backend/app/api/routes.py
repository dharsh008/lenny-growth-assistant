import asyncio
import json
import re
import time

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user
from app.api.schemas import (ArtifactOut, ErrorResponse, MessageCreate, ProviderOut, SessionCreate, SessionDetail,
                             SessionOut, SessionPatch)
from app.core.config import get_settings
from app.core.errors import BadRequest, IndexNotReady, NotFound
from app.core.logging import get_logger
from app.db.models import Artifact, ChatSession, Chunk, Episode, Message, User
from app.db.session import db_ping, get_db
from app.ingest.service import ingest

log = get_logger("api")
router = APIRouter(prefix="/api")
ERR = {400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}
_tasks: set[asyncio.Task] = set()  # keep strong refs so background turns survive client disconnects


# ----------------------------------------------------------------------------- health
@router.get("/health", tags=["ops"], summary="Liveness: process is up")
async def health(request: Request):
    return {"status": "ok", "version": request.app.version}


@router.get("/health/ready", tags=["ops"], summary="Readiness: database, knowledge base and model provider status")
async def ready(request: Request):
    st = request.app.state
    db_ok, db_err = await db_ping()
    providers = [p.__dict__ for p in await st.registry.all_status()]
    default = get_settings().llm_provider
    default_ok = next((p["available"] for p in providers if p["name"] == default), False)
    kb = st.retrieval.status()
    ok = db_ok and kb["ready"]
    body = {
        "status": "ready" if ok else "degraded",
        "database": {"ok": db_ok, "error": db_err},
        "knowledge_base": {**kb, "bootstrap": st.bootstrap},
        "llm": {"default_provider": default, "default_available": default_ok, "providers": providers,
                "fallback_provider": get_settings().llm_fallback_provider},
    }
    return JSONResponse(body, status_code=200 if ok else 503)


@router.get("/providers", response_model=list[ProviderOut], tags=["llm"], summary="Configured model providers and their live availability")
async def providers(request: Request, refresh: bool = False):
    if refresh:
        request.app.state.registry.invalidate()
    return [ProviderOut(**p.__dict__) for p in await request.app.state.registry.all_status()]


# ----------------------------------------------------------------------------- sessions
@router.post("/sessions", response_model=SessionOut, status_code=201, responses=ERR, tags=["sessions"])
async def create_session(body: SessionCreate, request: Request, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    cfg = get_settings()
    provider = body.provider or cfg.llm_provider
    model = cfg.ollama_model if provider == "ollama" else cfg.anthropic_model
    s = ChatSession(user_id=user.id, title=body.title or "New chat", provider=provider, model=model, meta={})
    db.add(s)
    await db.flush()
    log.info("session_created", session=s.id, provider=provider)
    return s


@router.get("/sessions", response_model=list[SessionOut], responses=ERR, tags=["sessions"])
async def list_sessions(limit: int = Query(50, ge=1, le=200), user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ChatSession).where(ChatSession.user_id == user.id).order_by(ChatSession.updated_at.desc()).limit(limit))).scalars()
    return list(rows)


async def _owned(db: AsyncSession, session_id: str, user: User) -> ChatSession:
    s = await db.get(ChatSession, session_id)
    if s is None or s.user_id != user.id:
        raise NotFound("Session not found", details={"session_id": session_id})
    return s


@router.get("/sessions/{session_id}", response_model=SessionDetail, responses=ERR, tags=["sessions"])
async def get_session(session_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    s = await _owned(db, session_id, user)
    msgs = (await db.execute(select(Message).where(Message.session_id == s.id).order_by(Message.seq))).scalars().all()
    arts = (await db.execute(select(Artifact).where(Artifact.session_id == s.id).order_by(Artifact.created_at))).scalars().all()
    return SessionDetail.model_validate({**{k: getattr(s, k) for k in ("id", "title", "provider", "model", "created_at", "updated_at")},
                                         "messages": msgs, "artifacts": arts})


@router.patch("/sessions/{session_id}", response_model=SessionOut, responses=ERR, tags=["sessions"])
async def patch_session(session_id: str, body: SessionPatch, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    s = await _owned(db, session_id, user)
    cfg = get_settings()
    if body.title:
        s.title = body.title
    if body.provider:
        s.provider = body.provider
        s.model = cfg.ollama_model if body.provider == "ollama" else cfg.anthropic_model
    await db.flush()
    return s


@router.delete("/sessions/{session_id}", status_code=204, responses=ERR, tags=["sessions"])
async def delete_session(session_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    s = await _owned(db, session_id, user)
    await db.delete(s)
    return Response(status_code=204)


# ----------------------------------------------------------------------------- chat
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _start_turn(request: Request, session_id: str, body: MessageCreate, user: User, db: AsyncSession):
    await _owned(db, session_id, user)
    st = request.app.state
    if not st.retrieval.ready:
        raise IndexNotReady("The transcript knowledge base is still loading. Try again in a moment.", details={"bootstrap": st.bootstrap})
    await db.commit()  # release this request's transaction before the long-running turn
    return st.orchestrator.run(session_id=session_id, user_id=user.id, content=body.content.strip(), mode=body.mode, provider=body.provider)


@router.post("/sessions/{session_id}/messages/stream", responses=ERR, tags=["chat"],
             summary="Send a message and stream the turn as Server-Sent Events",
             description="Events: user_message, route, status, retrieval, provider, sources, delta, plan, artifact_start, artifact_delta, "
                         "artifact, done, error. The turn keeps running server-side if the client disconnects, and is persisted.")
async def post_message_stream(session_id: str, body: MessageCreate, request: Request, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    gen = await _start_turn(request, session_id, body, user, db)
    queue: asyncio.Queue = asyncio.Queue()

    async def producer():
        try:
            async for ev in gen:
                await queue.put(ev)
        except Exception as exc:  # noqa: BLE001
            log.exception("producer_crashed")
            await queue.put({"type": "error", "code": "internal_error", "message": str(exc)[:200], "details": {}})
        finally:
            await queue.put(None)

    task = asyncio.create_task(producer())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)

    async def stream():
        yield ": stream-open\n\n"
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"  # keeps proxies from closing slow local-model turns
                continue
            if ev is None:
                break
            yield _sse(ev["type"], {k: v for k, v in ev.items() if k != "type"})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/sessions/{session_id}/messages", responses=ERR, tags=["chat"], summary="Send a message and wait for the full turn (JSON)")
async def post_message(session_id: str, body: MessageCreate, request: Request, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    gen = await _start_turn(request, session_id, body, user, db)
    events, final, artifacts, err = [], None, [], None
    async for ev in gen:
        events.append(ev["type"])
        if ev["type"] == "done":
            final = ev["message"]
        elif ev["type"] == "artifact":
            artifacts.append(ev["artifact"])
        elif ev["type"] == "error":
            err = ev
    if err:
        return JSONResponse({"error": {"code": err["code"], "message": err["message"], "details": err.get("details", {}),
                                       "request_id": getattr(request.state, "request_id", None)}}, status_code=502 if err["code"] != "provider_unavailable" else 503)
    return {"message": final, "artifacts": artifacts, "events": events}


# ----------------------------------------------------------------------------- artifacts
@router.get("/artifacts/{artifact_id}", response_model=ArtifactOut, responses=ERR, tags=["artifacts"])
async def get_artifact(artifact_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    a = await db.get(Artifact, artifact_id)
    if a is None:
        raise NotFound("Artifact not found")
    await _owned(db, a.session_id, user)
    return a


@router.get("/artifacts/{artifact_id}/download", responses=ERR, tags=["artifacts"])
async def download_artifact(artifact_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    a = await get_artifact(artifact_id, user, db)
    ext, mime = ("html", "text/html") if a.kind == "html" else ("md", "text/markdown")
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", a.title).strip("-")[:60] or "artifact"
    body = a.content
    if a.kind == "html":  # same policy the viewer applies, so a downloaded file is inert when opened directly
        from app.artifacts.sanitize import wrap_srcdoc
        body = wrap_srcdoc(a.content)
    return Response(body, media_type=f"{mime}; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{name}.{ext}"', "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"})


# ----------------------------------------------------------------------------- knowledge base
@router.get("/knowledge/stats", tags=["knowledge"])
async def kb_stats(request: Request, db: AsyncSession = Depends(get_db)):
    eps = (await db.execute(select(func.count(Episode.id)))).scalar_one()
    chunks = (await db.execute(select(func.count(Chunk.id)))).scalar_one()
    return {"episodes": eps, "chunks": chunks, "index": request.app.state.retrieval.status()}


@router.get("/knowledge/search", tags=["knowledge"], summary="Debug: raw retrieval results with scores (what the assistant would see)")
async def kb_search(request: Request, q: str = Query(min_length=2, max_length=300), k: int = Query(8, ge=1, le=20)):
    st = request.app.state
    if not st.retrieval.ready:
        raise IndexNotReady("Knowledge base is still loading", details={"bootstrap": st.bootstrap})
    r = await asyncio.to_thread(st.retrieval.search, q, k)
    return {"query": q, "strength": r.strength, "reason": r.reason, "best_coverage": r.best_coverage, "terms": r.terms, "took_ms": r.took_ms,
            "hits": [{"guest": h.guest, "title": h.title, "timestamp": h.timestamp(), "url": h.link, "score": round(h.score, 2),
                      "coverage": round(h.coverage, 2), "matched_terms": h.matched_terms, "text": h.text[:500]} for h in r.hits]}


@router.post("/admin/reingest", tags=["ops"], summary="Re-run incremental ingestion (requires X-Admin-Token = ADMIN_TOKEN)")
async def reingest(request: Request, x_admin_token: str | None = Header(default=None)):
    import os
    token = os.environ.get("ADMIN_TOKEN")
    if not token or x_admin_token != token:
        raise BadRequest("Admin endpoint disabled or token mismatch (set ADMIN_TOKEN).", details={})
    summary = await ingest(download_if_missing=False)
    await request.app.state.retrieval.reload()
    return summary
