# The Lenny Growth Assistant

Grounded Q&A, **Ship 30 for 30** essays and renderable **Markdown / HTML artifacts** over Lenny's Podcast transcripts (303 episodes).
FastAPI · PostgreSQL · Claude Agent SDK (Ollama local model *or* Claude) · React artifact viewer with a sandboxed iframe.

> Docs: [PRD](docs/PRD.md) · [Architecture](docs/architecture.md) · [Design](docs/design.md) · [Manual UI test plan](docs/manual-test-plan.md) · [Agent logs](docs/agent-transcripts/)

## Quick start (Docker, one command)

Prerequisites: Docker Desktop, [Ollama](https://ollama.com) on the host.

```bash
ollama pull qwen3:8b                 # or any model you like; set OLLAMA_MODEL to match
OLLAMA_CONTEXT_LENGTH=16384 ollama serve   # larger context so retrieved sources aren't truncated
cp .env.example .env
docker compose up --build
```
Open **http://localhost:3000** (API docs: http://localhost:8000/docs). On first start the backend downloads the transcripts
from GitHub, chunks and indexes them (~1 min); the UI shows readiness. Check `http://localhost:8000/api/health/ready`.

## Run without Docker (VS Code friendly)

```bash
# 1. Postgres
docker run -d --name lenny-pg -e POSTGRES_USER=lenny -e POSTGRES_PASSWORD=lenny -e POSTGRES_DB=lenny -p 5432:5432 postgres:16-alpine
# 2. Backend (Python 3.11+)
cd backend && python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example .env && sed -i 's#host.docker.internal#localhost#' .env
uvicorn app.main:app --reload --port 8000
# 3. Frontend (Node 20+)
cd ../frontend && npm install && npm run dev        # http://localhost:5173 (proxies /api to :8000)
```

## Switching models (no code changes)

| Goal | Set in `.env` |
|---|---|
| Local Ollama (default, required for the demo) | `LLM_PROVIDER=ollama`, `OLLAMA_MODEL=qwen3:8b` |
| Claude (cloud) | `ANTHROPIC_API_KEY=sk-ant-…`, `LLM_PROVIDER=anthropic`, optional `ANTHROPIC_MODEL` |
| Auto-fallback | `LLM_FALLBACK_PROVIDER=anthropic` (used only if Ollama is down / model missing) |

The header toggle (Local / Cloud) changes the provider **per conversation** at runtime; the selected provider and model are
shown on every answer. *How it works:* the agent layer is the **Claude Agent SDK**. Ollama exposes an Anthropic-compatible
endpoint, so the SDK talks to either backend and only the base URL / token / model differ (see `backend/app/agent/llm.py`).
Any Anthropic-compatible gateway (e.g. LiteLLM in front of OpenAI) works through `ANTHROPIC_BASE_URL`.

## Environment variables
See [`.env.example`](.env.example) — every variable is documented there. Required: none for the default local demo.
Optional: `ANTHROPIC_API_KEY`, `LLM_FALLBACK_PROVIDER`, timeouts, `ADMIN_TOKEN`. Secrets are never committed (`.env` is git-ignored).

## Tests

```bash
cd backend && pytest -q                      # 24 tests: parsing, chunking, BM25, routing, sanitiser, skill linter, API, persistence, failures
cd backend && python -m scripts.eval_retrieval   # retrieval smoke-eval on the real corpus (needs the DB ingested)
cd frontend && npm test                      # renderer sanitisation, sandbox policy, SSE parser
```
Backend API tests run on SQLite with a fake LLM, so they need no Postgres/Ollama. A manual UI plan is in `docs/manual-test-plan.md`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Banner "Local model isn't reachable" | `ollama serve`; in Docker the backend reaches the host via `host.docker.internal` (Linux: already mapped in compose). |
| "model 'x' is not pulled" | `ollama pull <OLLAMA_MODEL>` then click the status link to re-check. |
| Answers cut off / ignore sources | Run Ollama with `OLLAMA_CONTEXT_LENGTH=16384` (default 4096 truncates the prompt) or use a bigger model. |
| `timed out after 180s` | Smaller model, or raise `LLM_TIMEOUT_SECONDS`. First request also loads the model into memory. |
| `index_not_ready` / empty KB | Wait for bootstrap (`/api/health/ready`), or `docker compose exec backend python -m app.ingest`. No internet? Put `episodes/<slug>/transcript.md` files in `TRANSCRIPTS_DIR`. |
| Database errors | The API starts anyway and retries; check `docker compose logs db`. Schema is created automatically on a fresh DB. |
| Logs | JSON, one line per event, with `request_id`: `docker compose logs -f backend` (set `LOG_JSON=false` for pretty logs). |

## Extending
- **New skill:** add `backend/app/skills/<name>/{SKILL.md,skill.py}`, a route rule in `agent/router.py`, a handler in `agent/orchestrator.py`.
- **New provider:** add a branch in `provider_env()` (`agent/llm.py`) and a probe in `ProviderRegistry`.
- **Better retrieval:** swap `retrieval/bm25.py` for embeddings behind the same `search()` interface.
- **Refresh data:** `python -m app.ingest` is incremental (sha256 per episode); `POST /api/admin/reingest` with `ADMIN_TOKEN`.

## Repo layout
```
backend/app/{api,agent,skills/ship30,retrieval,ingest,artifacts,db,core}   FastAPI service
backend/tests, backend/scripts                                               tests + retrieval eval
frontend/src/{components,api.ts,safe.ts}                                     React UI + artifact viewer
docs/                                                                        PRD, architecture, design, test plan, agent logs
```
Transcript data is downloaded at runtime from [ChatPRD/lennys-podcast-transcripts](https://github.com/ChatPRD/lennys-podcast-transcripts) (personal/educational use) and is not committed.
