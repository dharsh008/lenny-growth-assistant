# PRD — The Lenny Growth Assistant

## 1. Discovery brief
**User & problem.** Primary user: a product/growth manager or growth marketer at a product team (the engagement client). Their job: get
a defensible answer to "what do top operators actually say about X?", then turn it into something shareable (a post, a one-pager).
Pain today: 300 long transcripts (~25k+ tokens each) are impractical to search or read; generic chatbots answer from memory and cannot cite
Lenny's guests; copy-pasting into docs/slides is manual.

**Success metrics** (measurable, with how we measure):
| Metric | Target | Measured by |
|---|---|---|
| Grounded-answer rate: answers with ≥1 valid citation (hallucinated `[n]` stripped) | ≥ 95% | `messages.meta.cited_numbers` / `citation_check` |
| Retrieval hit@8 on a curated question set | ≥ 85% (currently 8/8 on the 8-question smoke set) | `scripts/eval_retrieval.py` |
| Off-topic handled safely (refused or LLM-verified, never fabricated) | 100% of eval set flagged weak/none | same script (4/4) |
| Time to first token (cloud) / local | < 3 s / < 15 s after retrieval | `latency_ms`, structured logs |
| Task completion: question → shareable artifact in ≤ 2 turns | ≥ 80% of pilot users | UI session logs (post-pilot) |

**Assumptions** (client brief was incomplete)
1. Users are internal and non-technical → no prompts/model jargon in UI; one chat box with optional output-type chips.
2. "Internal" deployment: no SSO. An anonymous per-browser `X-User-Id` isolates conversations; real auth is the first hardening step.
3. The public transcript repo is the sole knowledge source; corpus is small enough (≈11k passages) for in-memory lexical search.
4. Demo hardware is a laptop: a 7–8B local model, 4–16k context, so prompts must be compact and tolerant of weaker instruction-following.
5. "Ship 30 for 30 style" = the guide's principles (headline formula, skeleton, hook, 1/3/1 rhythm, skimmability), encoded as a skill.
6. Transcripts are personal/educational-use data → not redistributed in the repo; downloaded at first run.

**Scope — included:** grounded Q&A with citations + refusal; follow-ups with per-session context; Ship 30 skill with a linter;
Markdown/HTML artifacts in a side-by-side viewer; provider toggle (Ollama / Claude) + fallback; Postgres persistence; Docker Compose;
structured logs/health; tests.
**Intentionally excluded (and why):** auth/multi-tenant (out of scope for demo; documented path), vector embeddings (extra model dependency, BM25 hits 8/8 on eval;
interface allows swap), artifact editing/version history (viewer is read-only + copy/download; versions = separate artifacts), voice/video, Newsletter posts (only podcast data given),
migrations tooling (Alembic) — schema auto-created for the demo; adding Alembic is the first production task.

**Risks & trade-offs**
| Risk | Mitigation | Residual |
|---|---|---|
| Hallucination | Evidence gate (none → refuse, no LLM call); `NOT_SUPPORTED` sentinel; mandatory `[n]` citations validated against retrieved sources; "Limited evidence" badge for single-source matches | A small local model may still paraphrase loosely; sources are one click away |
| Local-model quality/latency | Compact prompts (char budgets per provider), deterministic routing/retrieval so the model only writes; tag-protocol fallbacks; timeouts | 7B essays may fail some lint checks — surfaced in the skill report |
| Cost (cloud) | Retrieval cuts prompt to ≤ ~32k chars; per-turn provider switch; fallback only when needed | No per-user quota yet |
| Data leakage | Local provider keeps all text on the machine; cloud sends only retrieved excerpts + conversation; no secrets in repo; logs omit message text | Cloud mode sends excerpts to Anthropic |
| Unsafe artifact rendering | Server allow-list sanitiser + `iframe sandbox=""` + CSP (no scripts/network) + download served with sandbox CSP | See architecture §Security |
| Prompt injection via transcripts | Sources are quoted data; model has no tools (cannot act); output only rendered through sanitisers | Model could be nudged to say odd things; no side effects possible |

## 2. User flows
1. **Ask** → route=qa → retrieve → (gate) → streamed answer with `[n]` chips → sources panel (guest, episode, timestamp deep-link, quote).
2. **Follow-up** ("what about pricing?") → query condensed with history → same flow; context is per session.
3. **Essay** ("Write a Ship 30 for 30 essay on activation") → status steps (plan → draft → check/revise) → essay streams into the viewer → skill report (words, checks).
4. **Artifact** ("Create an HTML one-pager of that") → artifact streams as code → renders in sandbox on completion → Copy / Download / Security panel.
5. **Switch model** → header toggle; per-conversation; failure → banner + structured error; optional auto fallback.

## 3. Acceptance criteria
- AC1 New chat creates an isolated session; messages, timestamps, session id, user id persist in PostgreSQL and reload after refresh.
- AC2 Every non-trivial answer lists sources (guest, title, timestamp, link); invalid citation numbers never reach the user.
- AC3 Off-topic/unsupported questions produce a refusal that names the closest episodes, without inventing an answer.
- AC4 Essay ≈1,250 words (±12%) with H1, ≥3 H2, bullets, bold, takeaway, citations; linter result visible.
- AC5 HTML artifact containing `<script>`, `onerror`, remote CSS/images renders without executing/loading them; the viewer states what is blocked.
- AC6 Switching `LLM_PROVIDER` (env) or the UI toggle changes the model with no code change; provider/model shown per answer.
- AC7 Ollama down / key missing / timeout / empty retrieval / DB down → user-readable message, HTTP 503/504 with structured body, no crash.
- AC8 `docker compose up --build` yields a working app; `.env.example` has no secrets.

## 4. Implementation plan (as executed)
1. Ingest + chunk + BM25 (verify on real corpus) → 2. FastAPI/DB models/health → 3. Agent runtime (Agent SDK, provider env, timeouts) →
4. Router + orchestrator (qa) → 5. Artifact protocol + sanitiser → 6. Ship 30 skill + linter → 7. Frontend (chat, citations, viewer) →
8. Tests, eval, Docker, docs. Next: Alembic, auth, embeddings re-rank, thumbs-up/down feedback to measure the success metrics in production.
