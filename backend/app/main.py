import asyncio
import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, OperationalError

from app.agent.llm import ProviderRegistry
from app.agent.orchestrator import Orchestrator
from app.api.routes import router
from app.core.config import get_settings
from app.core.errors import DatabaseUnavailable, install_error_handlers
from app.core.logging import configure_logging, get_logger
from app.db.models import Chunk
from app.db.session import init_engine, session_scope, wait_for_db_and_create_schema
from app.ingest.service import ingest
from app.retrieval.service import RetrievalService

log = get_logger("main")


async def bootstrap(app: FastAPI) -> None:
    """Background start-up so the API (and /health) is reachable immediately even while Postgres is
    booting or the corpus is being downloaded/indexed. State is visible at /api/health/ready."""
    st = app.state
    cfg = get_settings()
    try:
        st.bootstrap = {"stage": "waiting_for_database"}
        await wait_for_db_and_create_schema()
        async with session_scope() as db:
            n_chunks = (await db.execute(select(func.count(Chunk.id)))).scalar_one()
        if n_chunks == 0 and cfg.auto_ingest:
            st.bootstrap = {"stage": "ingesting_transcripts"}
            await ingest()
        st.bootstrap = {"stage": "building_index"}
        await st.retrieval.reload()
        st.bootstrap = {"stage": "ready" if st.retrieval.ready else "empty_knowledge_base",
                        **({} if st.retrieval.ready else {"hint": "Run `python -m app.ingest` or set AUTO_INGEST=true"})}
    except Exception as exc:  # noqa: BLE001
        log.exception("bootstrap_failed")
        st.bootstrap = {"stage": "failed", "error": str(exc)[:300]}


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_settings()
    configure_logging(cfg.log_level, cfg.log_json)
    init_engine()
    app.state.retrieval = RetrievalService()
    app.state.registry = ProviderRegistry(cfg)
    app.state.orchestrator = Orchestrator(app.state.retrieval, app.state.registry, cfg)
    app.state.bootstrap = {"stage": "starting"}
    task = asyncio.create_task(bootstrap(app))
    log.info("startup", provider=cfg.llm_provider, ollama_model=cfg.ollama_model, fallback=cfg.llm_fallback_provider)
    yield
    task.cancel()


def create_app() -> FastAPI:
    app = FastAPI(
        title="The Lenny Growth Assistant API", version="1.0.0", lifespan=lifespan,
        description="Grounded Q&A, Ship 30 for 30 essays and renderable artifacts over Lenny's Podcast transcripts.",
    )
    cfg = get_settings()
    app.add_middleware(CORSMiddleware, allow_origins=cfg.cors_origin_list, allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["X-Request-ID"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = rid
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=rid)
        t0 = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        if request.url.path != "/api/health":
            log.info("http", method=request.method, path=request.url.path, status=response.status_code,
                     ms=int((time.perf_counter() - t0) * 1000), user=getattr(request.state, "user_id", None))
        return response

    install_error_handlers(app)

    @app.exception_handler(OperationalError)
    async def _db_down(request: Request, exc: OperationalError):
        log.error("database_error", error=str(exc)[:200])
        return await _db_error(request)

    @app.exception_handler(DBAPIError)
    async def _db_api(request: Request, exc: DBAPIError):
        log.error("database_error", error=str(exc)[:200])
        return await _db_error(request)

    async def _db_error(request: Request):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": {"code": DatabaseUnavailable.code, "message": "The database is unreachable. Try again shortly.",
                                       "details": {}, "request_id": getattr(request.state, "request_id", None)}}, status_code=503)

    app.include_router(router)
    return app


app = create_app()
