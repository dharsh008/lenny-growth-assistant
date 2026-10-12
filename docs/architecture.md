# Architecture

## Topology
```
Browser (React)  --/api (SSE)-->  nginx :3000  -->  FastAPI :8000  --> PostgreSQL :5432
                                                       |  \--> in-memory BM25 index (loaded from `chunks`)
                                                       \--> Claude Agent SDK (claude runtime subprocess)
                                                              |-- ANTHROPIC_BASE_URL=http://host:11434  -> Ollama (local, default)
                                                              \-- api.anthropic.com                     -> Claude (cloud)
```
Three containers (db, backend, frontend); Ollama runs on the host (GPU access). Transcripts live in a named volume.

## Database schema (PostgreSQL; JSONB on PG, JSON on SQLite for tests)
| Table | Key columns |
|---|---|
| `users` | `id` (anonymous X-User-Id), display_name, `meta` (user agent, first IP), created_at, last_seen_at |
| `chat_sessions` | `id` uuid, user_id→users, title, **provider**, model, meta, created_at, updated_at |
| `messages` | `id`, session_id→sessions (cascade), **seq** (unique per session), role, content, **route** (qa/essay/artifact/refusal/chitchat/error), `sources` JSONB, artifact_ids, provider, model, latency_ms, `meta` JSONB (retrieval stats, lint, errors), created_at |
| `artifacts` | `id`, session_id, message_id, kind (markdown/html), title, content (sanitised if html), sanitized, `report` JSONB (sanitiser removals, skill lint, plan), skill |
| `episodes` | `id` (folder slug), guest, title, youtube_url, publish_date, duration, word_count, source_path, **content_hash** |
| `chunks` | id, episode_id→episodes, ordinal, start_seconds, end_seconds, speakers, text, word_count |
| `ingest_runs` | started/finished, status, counts, error |
Schema is created by `create_all` at startup (demo scope). Production: add Alembic.

## API (FastAPI; OpenAPI at `/docs`)
| Endpoint | Purpose |
|---|---|
| `GET /api/health`, `GET /api/health/ready` | liveness; readiness = DB + KB + provider status (503 if degraded) |
| `GET /api/providers[?refresh=true]` | configured providers, model, live availability |
| `POST/GET/PATCH/DELETE /api/sessions[/{id}]` | session CRUD (header `X-User-Id` required), provider per session |
| `POST /api/sessions/{id}/messages/stream` | SSE turn: `user_message, route, status, retrieval, provider, sources, delta, plan, artifact_start, artifact_delta, artifact, done, error` |
| `POST /api/sessions/{id}/messages` | same turn, JSON response (for scripts/tests) |
| `GET /api/artifacts/{id}`, `/download` | artifact fetch; download served with `sandbox` CSP + `nosniff` |
| `GET /api/knowledge/stats`, `/knowledge/search?q=` | KB counts; debug retrieval with scores (explainability) |
| `POST /api/admin/reingest` | incremental re-ingest (needs `ADMIN_TOKEN`) |
All errors: `{"error": {"code","message","details","request_id"}}` — 400 bad_request, 404 not_found, 422 validation_error, 503 provider_unavailable / provider_misconfigured / index_not_ready / database_unavailable, 504 provider_timeout. Requests are validated by Pydantic (length, enums).

## Ingestion → retrieval
1. **Load:** `fetch.py` downloads the GitHub tarball, extracting only `episodes/<safe-slug>/transcript.md` (path-validated, size-capped). Or point `TRANSCRIPTS_DIR` at a local copy.
2. **Parse:** YAML frontmatter + speaker turns. Three formats occur in the corpus — `Name (HH:MM:SS):` (most), `Name (MM:SS):`, `[ts] Name: text`, and one with no timestamps; all handled (found when 32/303 episodes first parsed to zero turns).
3. **Chunk:** turn-aligned ≈320-word chunks (long turns split by sentence), 1-turn overlap; each chunk keeps speakers + start/end seconds → 11,033 chunks.
4. **Index:** chunks stored in Postgres; BM25 (k1=1.4, b=0.75, English stemming, stop-words) built in memory at startup (~6 s) with title+guest prepended.
5. **Search:** idf-weighted term **coverage** per chunk, guest-name boost, max 3 chunks per episode, top-k 8 (qa) / 10 (essay).
6. **Trace back:** every hit → episode, guest, title, `youtube_url&t=<seconds>`, timestamp, quote. **Refresh:** sha256 per file; changed episodes re-chunked, removed episodes deleted; `python -m app.ingest` or admin endpoint.
7. **Grounding gate** (`strength`): `none` (no/low-coverage hits) → refuse without calling the LLM; `weak` (single-episode match) → LLM must verify or reply `NOT_SUPPORTED`; `strong` (≥2 episodes corroborate) → answer.

### Retrieval trade-offs
BM25 over embeddings: no extra model to run locally, deterministic, explainable, 8/8 hit@8 on the smoke eval. Weakness: paraphrase/synonym recall.
Lexical coverage alone can match a stray aside ("sourdough" in a sales episode) — hence the weak tier + sentinel rather than a single threshold.

## Agent design & routing
Agent runtime = **Claude Agent SDK** (`claude_agent_sdk.query`) with built-in tools disabled, 1 turn, no settings/CLAUDE.md, our own system prompt.
We orchestrate in code instead of letting the model pick tools, because small local models are unreliable at tool-calling and grounding must not depend on it.
```
message → router (regex rules, UI `mode` override) ─┬ chitchat → canned reply (no LLM)
                                                    ├ qa       → [condense follow-up] → retrieve → gate → LLM(stream) → validate [n] → persist
                                                    ├ essay    → retrieve(10) → skill: plan(JSON) → draft(stream→viewer) → lint → revise? → sources footer
                                                    └ artifact → retrieve(8) → LLM emits <artifact type title> → stream parser → sanitise(html) → persist
```
- **Skills:** `skills/ship30/` = `SKILL.md` (principles distilled from the Ship 30 guide: Endless Idea Generator, headline Who/What/Why, skeleton, hook, Wheels & Spokes, 1/3/1 rhythm, tequila test) + `skill.py` (prompts, plan parser tolerant of bad JSON, **linter** with 9 checks, sources footer). Prompts inject SKILL.md, so editing the file changes behaviour.
- **Follow-ups:** history comes from Postgres (last 8 msgs / 6k chars, citations stripped); the query is condensed by the LLM (30 s cap, heuristic fallback). Essay/artifact with no new topic reuse the previous answer's chunks.
- **Resilience inside the loop:** model ignores `<artifact>` tags → fenced-block / whole-text fallback; invalid `[n]` removed; empty draft → error; revise result only kept if lint improves.

## Model toggle & fallback
`provider_env()` maps provider → env for the SDK subprocess (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`/`API_KEY`, model aliases pinned to the local model so no hidden "haiku" calls leave Ollama).
`ProviderRegistry` probes Ollama (`/api/tags`, model present?) and the Anthropic key (cached 8 s). Default from `LLM_PROVIDER`; per-session override from the UI. If the chosen provider is unavailable and `LLM_FALLBACK_PROVIDER` is healthy → fallback + visible note; otherwise 503 with the exact fix (`ollama pull …`).
Timeouts: total `LLM_TIMEOUT_SECONDS` and per-chunk idle `LLM_IDLE_TIMEOUT_SECONDS`.

## Security
- **Untrusted HTML artifacts** — defence in depth: (1) server `nh3` allow-list (no script/iframe/object/form/meta/link; no event handlers; hrefs only `#anchors`; img only `data:`), CSS scrubbed of `@import`, external `url()`, `expression()`, bindings; (2) frontend renders in `<iframe sandbox="">` (opaque origin: no scripts, forms, popups, top navigation, storage) with CSP `default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:` → CSS/image beacons can't exfiltrate; (3) downloads are sandbox-CSP'd and wrapped with the same CSP. The viewer's **Security** panel states what's allowed/blocked and what was removed for that artifact.
- **Markdown artifacts & chat** — `marked` → DOMPurify (forbids style/forms/iframes/svg; links `noopener`, remote images dropped).
- **Prompt injection:** model has no tools/filesystem/network; output is text only and always sanitised.
- **Other:** SQLAlchemy parameterised queries; ownership checks (other users' sessions → 404); tarball extraction validated; secrets only via env; logs exclude message bodies; CORS allow-list.
- **Not covered (documented):** authentication, rate limiting, per-user quotas.

## Observability
structlog JSON lines with `request_id` (also returned as `X-Request-ID`): `http` (method/path/status/ms/user), `retrieval` (query, strength, coverage, took_ms), `provider_fallback`, `llm_timeout`, `artifact_sanitized` (what was removed), `artifact_tag_missing_fallback_used`, `condense_failed`, `turn_failed`/`turn_crashed`, ingest progress. Per-message `meta` in Postgres stores retrieval stats, lint, errors, latency. `/api/health/ready` exposes DB, KB, bootstrap stage, provider health.

## Failure behaviour matrix
| Failure | Behaviour |
|---|---|
| Ollama down / model not pulled | banner + 503 `provider_unavailable` with fix; fallback if configured |
| Cloud key missing | 503 `provider_misconfigured` naming `ANTHROPIC_API_KEY` |
| Model hangs | 504 `provider_timeout` (total or idle) |
| Retrieval empty / weak | refusal with nearest episodes / LLM-verified answer |
| Postgres down at start | API up, `/ready` 503 `degraded`, bootstrap retries; endpoints 503 `database_unavailable` |
| Corpus missing & offline | `bootstrap.stage=failed/empty_knowledge_base` with hint |
| Client disconnects mid-turn | turn continues server-side and is persisted |
