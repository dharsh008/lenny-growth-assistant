# Build log (curated) — what was tried, what failed, how it was corrected

| # | Attempt / decision | What went wrong (evidence) | Correction |
|---|---|---|---|
| 1 | Chose the Claude Agent SDK for the agent layer; needed one path for Ollama + Claude | Unclear that the SDK can target Ollama | Verified Ollama's Anthropic-compatible endpoint; wrote a mock server (`tests/mock_llm_server.py`) and confirmed the **real SDK subprocess** streams through a custom `ANTHROPIC_BASE_URL`. Pinned all model aliases to the local model |
| 2 | First transcript parser assumed `Name (HH:MM:SS):` | 30/303 episodes parsed to **0 turns** (they use `MM:SS`), then 2 more (`[ts] Name:` and no timestamps) | Extended parser to 4 shapes; re-ran: all 303 parse, 11,033 chunks |
| 3 | Chunker's final-chunk logic (carry-over overlap) | Convoluted, risked emitting an overlap-only chunk | Rewrote with a `fresh` counter |
| 4 | Single retrieval threshold to detect off-topic questions | "how to bake sourdough bread" and "capital of France" scored 1.0 coverage (stray mentions in unrelated episodes) | Replaced with 3 tiers (none/weak/strong using cross-episode corroboration) + `NOT_SUPPORTED` sentinel the model must emit; eval shows 4/4 off-topic flagged |
| 5 | Guest-name queries ("Brian Chesky founder mode") | Right guest ranked 3rd | Added guest-name boost |
| 6 | Renamed `artifacts.sanitizer_report`→`report` after dev DB existed | `create_all` doesn't migrate → `UndefinedColumn` → every essay/artifact turn crashed with `internal_error` (visible in logs) | Dropped the dev table; documented that Alembic is the production path |
| 7 | Follow-up artifact requests ("summarise this") | Query was polluted with earlier command words ("Write a Ship 30 essay…") → retrieval `none` | Query builder strips command words from prior user messages |
| 8 | My dev script waited for `"ready"` | Matched `"ready":false` and fired requests early (empty output) | Fixed the wait condition |
| 9 | API tests on tiny fixture corpus | All grounded-answer tests refused (BM25 scores tiny with 3 docs) | `RETRIEVAL_MIN_SCORE=0.1` in test env; also fixed a race (ingest before schema bootstrap) |
| 10 | Frontend `DOMPurify` config typed `as const` | `tsc` error on readonly arrays | Typed as `Config` |

Verification performed: 24 backend tests + 6 frontend tests pass; real-Postgres ingest idempotency (303 changed → 0 on rerun); retrieval eval 8/8 hit@8, 4/4 off-topic flagged;
end-to-end SSE turns for qa / essay / HTML / Markdown / refusal / greeting against the real Agent-SDK path with a **mock model**.
**Not verified in the build environment** (no Docker daemon, no Ollama access, no API key): `docker compose up`, real Ollama output quality, real Claude output, browser visual QA.
