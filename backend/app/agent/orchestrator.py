"""The agent orchestrator: route -> retrieve -> ground-gate -> generate -> validate -> persist.

Skills/routes:
  chitchat  canned reply, no LLM
  qa        grounded Q&A with inline [n] citations
  essay     Ship 30 for 30 skill (plan -> draft -> lint -> revise), delivered as a Markdown artifact
  artifact  Markdown / HTML artifact generation (HTML is sanitised before it is stored or sent)
  refusal   evidence gate failed or model answered NOT_SUPPORTED: no invented answer
Everything is emitted as typed events so the same engine feeds SSE streaming and the JSON endpoint."""
import asyncio
import html as html_lib
import re
import time
from collections.abc import AsyncIterator

from sqlalchemy import func, select

from app.agent import prompts as P
from app.agent.llm import LLMClient, ProviderRegistry
from app.agent.router import RouteDecision, looks_like_followup, route_message
from app.artifacts.parser import ArtifactStreamParser, fallback_extract
from app.artifacts.sanitize import sanitize_html
from app.core.config import Settings, get_settings
from app.core.errors import AppError, NotFound
from app.core.logging import get_logger
from app.db.models import Artifact, ChatSession, Message
from app.db.session import session_scope
from app.retrieval.service import Hit, RetrievalService, SearchResult
from app.skills.ship30 import skill as ship30

log = get_logger("agent")

CHITCHAT_REPLY = (
    "Hi! I'm the **Lenny Growth Assistant**. I answer product and growth questions *only* from Lenny's Podcast transcripts "
    "and cite the episode and timestamp for every claim.\n\n"
    "Try:\n- “How do top teams find product-market fit?”\n- “Write a Ship 30 for 30 essay on activation”\n"
    "- “Create an HTML one-pager of the frameworks we just discussed”"
)
COMMAND_WORDS = set(
    """write draft compose create make generate build produce design give put together turn convert render show prepare an a the
    essay article blog post newsletter ship 30 for atomic markdown html css md document doc one pager onepager one-pager cheat sheet
    cheatsheet checklist template playbook infographic landing page web webpage slide table timeline dashboard scorecard artifact
    brief memo diagram poster card please can you could me about on into from this that it above summary summarize summarise summarizing summarising of""".split()
)


def _topic_terms(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", text)
    return " ".join(w for w in words if w.lower() not in COMMAND_WORDS)


def _ev(type_: str, **data) -> dict:
    return {"type": type_, **data}


class Orchestrator:
    def __init__(self, retrieval: RetrievalService, registry: ProviderRegistry, cfg: Settings | None = None):
        self.retrieval = retrieval
        self.registry = registry
        self.cfg = cfg or get_settings()

    # ------------------------------------------------------------------ persistence helpers
    async def _load(self, session_id: str, user_id: str) -> tuple[ChatSession, list[tuple[str, str]], list[int]]:
        async with session_scope() as db:
            sess = await db.get(ChatSession, session_id)
            if sess is None or sess.user_id != user_id:
                raise NotFound("Session not found", details={"session_id": session_id})
            msgs = (await db.execute(select(Message).where(Message.session_id == session_id).order_by(Message.seq))).scalars().all()
        history = [(m.role, m.content) for m in msgs if m.route != "error"]
        prev_chunks: list[int] = []
        for m in reversed(msgs):
            if m.role == "assistant" and m.sources:
                prev_chunks = [s["chunk_id"] for s in m.sources if s.get("cited", True) and "chunk_id" in s]
                if prev_chunks:
                    break
        return sess, history, prev_chunks

    async def _add_message(self, session_id: str, role: str, content: str, **kw) -> Message:
        async with session_scope() as db:
            seq = ((await db.execute(select(func.max(Message.seq)).where(Message.session_id == session_id))).scalar() or 0) + 1
            m = Message(session_id=session_id, seq=seq, role=role, content=content, **kw)
            db.add(m)
            sess = await db.get(ChatSession, session_id)
            if sess:
                if role == "user" and sess.title == "New chat":
                    sess.title = (re.sub(r"\s+", " ", content).strip()[:60] or "New chat")
                sess.updated_at = m.created_at
            await db.flush()
            return m

    async def _save_artifact(self, session_id: str, message_id: str | None, kind: str, title: str, content: str,
                             report: dict, skill: str | None = None) -> Artifact:
        sanitized = False
        if kind == "html":
            content, rep = sanitize_html(content)
            report = {**report, "sanitizer": {"removed": rep.removed, "changed": rep.changed}}
            sanitized = True
            if rep.changed:
                log.warning("artifact_sanitized", removed=rep.removed, title=title)
        async with session_scope() as db:
            art = Artifact(session_id=session_id, message_id=message_id, kind=kind, title=title[:200] or "Untitled",
                           content=content, sanitized=sanitized, report=report, skill=skill)
            db.add(art)
            await db.flush()
            return art

    # ------------------------------------------------------------------ query building
    async def _build_query(self, llm: LLMClient | None, decision: RouteDecision, content: str,
                           history: list[tuple[str, str]]) -> str:
        prior_user = [c for r, c in history if r == "user"]
        if decision.route in ("essay", "artifact"):
            topic = _topic_terms(content)
            if len(topic.split()) >= 2:
                return topic
            # "turn that into an essay": borrow the topic words of the previous questions (minus command verbs/nouns)
            prior_topics = [t for t in (_topic_terms(c) for c in prior_user) if len(t.split()) >= 2]
            return " ".join(prior_topics[-2:] + [topic]).strip() or content
        if not prior_user or not looks_like_followup(content):
            return content
        heuristic = f"{prior_user[-1]} {content}"
        if llm is None:
            return heuristic
        try:
            hist = P.history_block(history, 4, 1800)
            q = await asyncio.wait_for(
                llm.complete(P.CONDENSE_SYSTEM, f"CONVERSATION:\n{hist}\n\nLATEST MESSAGE: {content}"), timeout=30
            )
            q = re.sub(r"[\n\"]+", " ", q).strip()
            return q if 2 <= len(q.split()) <= 30 else heuristic
        except Exception as exc:  # noqa: BLE001 - condensing is an optimisation; never fail the turn on it
            log.warning("condense_failed", error=str(exc)[:160])
            return heuristic

    # ------------------------------------------------------------------ main entry
    async def run(self, *, session_id: str, user_id: str, content: str, mode: str = "auto",
                  provider: str | None = None) -> AsyncIterator[dict]:
        t0 = time.perf_counter()
        sess, history, prev_chunks = await self._load(session_id, user_id)
        user_msg = await self._add_message(session_id, "user", content)
        yield _ev("user_message", id=user_msg.id, seq=user_msg.seq, title=sess.title if sess.title != "New chat" else content[:60])
        decision = route_message(content, mode)
        provider = provider or sess.provider
        base_meta = {"route": decision.route, "route_reason": decision.reason}
        yield _ev("route", **base_meta, artifact_kind=decision.artifact_kind)

        try:
            if decision.route == "chitchat":
                msg = await self._add_message(session_id, "assistant", CHITCHAT_REPLY, route="chitchat", meta=base_meta)
                yield _ev("delta", text=CHITCHAT_REPLY)
                yield _ev("done", message=self._public(msg))
                return

            # ---- retrieval (cheap, no LLM) --------------------------------------------------
            llm, fb_note = None, None
            needs_condense = decision.route == "qa" and bool([1 for r, _ in history if r == "user"]) and looks_like_followup(content)
            if needs_condense:
                llm, fb_note = await self.registry.resolve(provider)
            yield _ev("status", step="retrieving", label="Searching transcripts")
            query = await self._build_query(llm, decision, content, history)
            k = {"qa": self.cfg.retrieval_top_k, "essay": self.cfg.essay_source_count, "artifact": 8}[decision.route]
            result = await asyncio.to_thread(self.retrieval.search, query, k)
            hits = list(result.hits)
            if decision.route in ("essay", "artifact") and prev_chunks and len(_topic_terms(content).split()) < 2:
                # "turn that into an essay": no new topic given -> reuse the sources behind the previous answer
                carried = self.retrieval.get_hits(prev_chunks)
                seen = {h.chunk_id for h in carried}
                hits = (carried + [h for h in hits if h.chunk_id not in seen])[:k]
                if carried and result.strength == "none":
                    result.strength = "weak"
            retrieval_meta = {
                "query": query, "strength": result.strength, "reason": result.reason, "best_coverage": round(result.best_coverage, 3),
                "best_score": round(result.best_score, 2), "corroborating_episodes": result.corroborating_episodes,
                "took_ms": result.took_ms, "n_hits": len(hits),
            }
            log.info("retrieval", session=session_id, **retrieval_meta)
            yield _ev("retrieval", **retrieval_meta)

            if result.strength == "none" or not hits:
                async for e in self._refuse(session_id, result, hits, {**base_meta, "retrieval": retrieval_meta}, t0):
                    yield e
                return

            # ---- LLM ------------------------------------------------------------------------
            if llm is None:
                llm, fb_note = await self.registry.resolve(provider)
            yield _ev("provider", provider=llm.provider, model=llm.model, fallback_note=fb_note)
            budget = self.cfg.source_char_budget_local if llm.provider == "ollama" else self.cfg.source_char_budget_cloud
            refs = P.build_sources(hits, result.terms, budget, max_sources=len(hits))
            yield _ev("sources", sources=[r.public(cited=False) for r in refs])
            ctx = dict(session_id=session_id, content=content, history=history, refs=refs, result=result, llm=llm,
                       meta={**base_meta, "retrieval": retrieval_meta, "provider": llm.provider, "model": llm.model,
                             **({"fallback_note": fb_note} if fb_note else {})}, t0=t0)
            gen = {"qa": self._qa, "artifact": self._artifact, "essay": self._essay}[decision.route]
            kwargs = {"kind": decision.artifact_kind} if decision.route == "artifact" else {}
            async for e in gen(**ctx, **kwargs):
                yield e
        except AppError as exc:
            log.error("turn_failed", session=session_id, code=exc.code, message=exc.message)
            msg = await self._add_message(session_id, "assistant", f"⚠️ {exc.message}", route="error",
                                          meta={**base_meta, "error": {"code": exc.code, "details": exc.details}})
            yield _ev("error", code=exc.code, message=exc.message, details=exc.details, message_id=msg.id)
        except Exception as exc:  # noqa: BLE001
            log.exception("turn_crashed", session=session_id)
            msg = await self._add_message(session_id, "assistant", "⚠️ Something went wrong while generating the answer.",
                                          route="error", meta={**base_meta, "error": {"code": "internal_error", "details": str(exc)[:200]}})
            yield _ev("error", code="internal_error", message="Something went wrong while generating the answer.", details={}, message_id=msg.id)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _public(m: Message) -> dict:
        return {"id": m.id, "seq": m.seq, "role": m.role, "content": m.content, "route": m.route, "sources": m.sources or [],
                "artifact_ids": m.artifact_ids or [], "provider": m.provider, "model": m.model, "latency_ms": m.latency_ms,
                "meta": m.meta or {}, "created_at": m.created_at.isoformat() if m.created_at else None}

    async def _refuse(self, session_id, result: SearchResult, hits: list[Hit], meta: dict, t0: float,
                      llm_said_unsupported: bool = False) -> AsyncIterator[dict]:
        near = []
        seen: set[str] = set()
        for h in hits:
            if h.episode_id not in seen and len(near) < 3:
                seen.add(h.episode_id)
                near.append({"guest": h.guest, "title": h.title, "url": h.link, "timestamp": h.timestamp()})
        text = ("I couldn't find anything in the Lenny's Podcast transcripts that supports an answer to that, so I won't guess.")
        if near:
            text += "\n\nThe closest material I found (probably not a direct answer):\n" + "\n".join(
                f"- {n['guest']} — “{n['title']}”" for n in near)
        text += "\n\nTry rephrasing with product, growth or career terms, or ask about a specific guest or topic."
        msg = await self._add_message(
            session_id, "assistant", text, route="refusal", sources=[{**n, "cited": False} for n in near],
            latency_ms=int((time.perf_counter() - t0) * 1000),
            meta={**meta, "route": "refusal", "llm_said_unsupported": llm_said_unsupported},
        )
        yield _ev("delta", text=text)
        yield _ev("done", message=self._public(msg))

    def _cited_sources(self, refs: list[P.SourceRef], cited: list[int]) -> list[dict]:
        if cited:
            return [r.public(True) for r in refs if r.n in cited]
        return [r.public(False) for r in refs[:3]]  # model gave no citations: show what was consulted, flagged

    # ------------------------------------------------------------------ routes
    async def _qa(self, *, session_id, content, history, refs, result, llm, meta, t0) -> AsyncIterator[dict]:
        hist = P.history_block(history, self.cfg.history_max_messages, self.cfg.history_max_chars)
        caution = ""
        if result.strength == "weak":
            caution = ("\nNOTE: retrieval confidence is LOW (only one episode matched). Answer only if a source directly addresses "
                       "the question; otherwise reply NOT_SUPPORTED.")
        prompt = f"CONVERSATION:\n{hist or '(none)'}\n\nSOURCES:\n{P.sources_block(refs)}\n\nQUESTION: {content}{caution}"
        head, state, parts = "", "undecided", []
        async for chunk in llm.stream(P.QA_SYSTEM, prompt):
            parts.append(chunk)
            if state == "open":
                yield _ev("delta", text=chunk)
            elif state == "undecided":
                head += chunk
                lead = head.lstrip()
                if lead.startswith("NOT_SUPPORTED"):
                    state = "refuse"  # swallow: the user must never see the sentinel
                elif not ("NOT_SUPPORTED".startswith(lead) and len(lead) < 13):
                    state = "open"
                    yield _ev("delta", text=head)
        full = "".join(parts).strip()
        if state == "undecided" and head:
            yield _ev("delta", text=head)
        if full.startswith("NOT_SUPPORTED") or not full:
            async for e in self._refuse(session_id, result, [r.hit for r in refs], meta, t0, llm_said_unsupported=True):
                yield e
            return
        text, cited = P.validate_citations(full, len(refs))
        meta = {**meta, "cited_numbers": cited, "citation_check": "ok" if cited else "model_gave_no_citations"}
        msg = await self._add_message(
            session_id, "assistant", text, route="qa", sources=self._cited_sources(refs, cited), provider=llm.provider,
            model=llm.model, latency_ms=int((time.perf_counter() - t0) * 1000), meta=meta)
        yield _ev("done", message=self._public(msg))

    async def _artifact(self, *, session_id, content, history, refs, result, llm, meta, t0, kind) -> AsyncIterator[dict]:
        hist = P.history_block(history, self.cfg.history_max_messages, self.cfg.history_max_chars)
        task = "artifact_html" if kind == "html" else "artifact_md"
        system = P.ARTIFACT_SYSTEM.format(task=task, kind=kind, kind_label="HTML/CSS" if kind == "html" else "Markdown",
                                          kind_rules=P.HTML_RULES if kind == "html" else P.MD_RULES)
        prompt = f"CONVERSATION:\n{hist or '(none)'}\n\nSOURCES:\n{P.sources_block(refs)}\n\nREQUEST: {content}"
        parser, full = ArtifactStreamParser(), []
        yield _ev("status", step="generating", label=f"Creating {kind} artifact")
        async for chunk in llm.stream(system, prompt):
            full.append(chunk)
            for ev in parser.feed(chunk):
                out = self._parser_event(ev)
                if out:
                    yield out
        for ev in parser.finish():
            out = self._parser_event(ev)
            if out:
                yield out
        prose = "".join(parser.prose).strip()
        completed = list(parser.completed)
        raw = "".join(full)
        if not completed and (re.search(r"```(html|markdown|md)", raw, re.I) or len(raw) > 600):
            prose, completed = fallback_extract(raw, kind)
            meta = {**meta, "artifact_fallback": True}
            log.warning("artifact_tag_missing_fallback_used", session=session_id)
            yield _ev("delta", text=prose)
        ids, arts = [], []
        text_clean, cited = P.validate_citations(prose, len(refs))
        for a_kind, title, body in completed[:1]:
            body, cited_a = P.validate_citations(body, len(refs)) if a_kind == "markdown" else (body, [])
            cited = sorted(set(cited) | set(cited_a))
            if a_kind == "markdown" and not re.search(r"^#+\s*sources", body, re.I | re.M):
                body += ship30.sources_footer([self._src_dict(r) for r in refs if r.n in cited] or [self._src_dict(r) for r in refs[:5]])
            if a_kind == "html":
                body += self._html_sources(refs, cited)
            art = await self._save_artifact(session_id, None, a_kind, title, body, {"cited": cited})
            ids.append(art.id)
            arts.append(art)
            yield _ev("artifact", artifact=self._public_artifact(art))
        if not arts and not text_clean:
            text_clean = "I couldn't build that from the transcripts I found."
        msg = await self._add_message(
            session_id, "assistant", text_clean or "Done — it's in the viewer.", route="artifact", sources=self._cited_sources(refs, cited),
            artifact_ids=ids, provider=llm.provider, model=llm.model, latency_ms=int((time.perf_counter() - t0) * 1000), meta=meta)
        if arts:
            async with session_scope() as db:
                for a in arts:
                    row = await db.get(Artifact, a.id)
                    row.message_id = msg.id
        yield _ev("done", message=self._public(msg))

    async def _essay(self, *, session_id, content, history, refs, result, llm, meta, t0) -> AsyncIterator[dict]:
        cfg = self.cfg
        target = cfg.essay_target_words
        lo, hi = int(target * 0.88), int(target * 1.12)
        principles = ship30.principles()
        src = P.sources_block(refs)
        topic = result.query
        yield _ev("status", step="planning", label="Skill: choosing angle & headline")
        plan_raw = await llm.complete(ship30.PLAN_SYSTEM.format(principles=principles), f"TOPIC: {topic}\nREQUEST: {content}\n\nSOURCES:\n{src}")
        plan = ship30.parse_plan(plan_raw, topic, len(refs))
        yield _ev("plan", plan=plan)

        yield _ev("status", step="drafting", label="Skill: drafting essay")
        yield _ev("artifact_start", artifact_type="markdown", title=plan["headline"][:200])
        draft_sys = ship30.DRAFT_SYSTEM.format(principles=principles, target=target, lo=lo, hi=hi)
        draft_prompt = f"REQUEST: {content}\n\nPLAN:\n{plan}\n\nSOURCES:\n{src}"
        parts: list[str] = []
        async for chunk in llm.stream(draft_sys, draft_prompt):
            parts.append(chunk)
            yield _ev("artifact_delta", text=chunk)
        draft = "".join(parts).strip()
        if not draft:
            raise AppError("The model returned an empty draft.")
        draft, _ = P.validate_citations(draft, len(refs))
        report = ship30.lint(draft, len(refs), target)
        revised = False
        if not report.passed:
            yield _ev("status", step="revising", label=f"Skill: fixing {sum(not v for v in report.checks.values())} failed checks")
            fixes = "\n".join(f"- {m}" for m in report.messages)
            rev_raw = await llm.complete(ship30.REVISE_SYSTEM.format(principles=principles),
                                         f"FIXES:\n{fixes}\n\nSOURCES:\n{src}\n\nDRAFT:\n{draft}")
            rev, _ = P.validate_citations(rev_raw.strip(), len(refs))
            rev_report = ship30.lint(rev, len(refs), target)
            if rev and sum(rev_report.checks.values()) >= sum(report.checks.values()):
                draft, report, revised = rev, rev_report, True
        cited = sorted({int(n) for n in re.findall(r"\[(\d{1,2})\]", draft)})
        body = draft + ship30.sources_footer([self._src_dict(r) for r in refs if r.n in cited] or [self._src_dict(r) for r in refs[:5]])
        title = (re.search(r"^#\s+(.+)$", draft, re.M) or [None, plan["headline"]])[1].strip()
        art = await self._save_artifact(session_id, None, "markdown", title, body,
                                        {"cited": cited, "skill_lint": report.as_dict(), "plan": plan, "revised": revised}, skill="ship30")
        yield _ev("artifact", artifact=self._public_artifact(art))
        passed = sum(report.checks.values())
        text = (f"I wrote a **{report.words:,}-word** Ship 30 for 30 essay, “{title}”, grounded in {len(cited) or len(refs)} transcript excerpts. "
                f"Skill checks passed: **{passed}/{len(report.checks)}**" + ("" if report.passed else f" (open items: {'; '.join(report.messages[:2])})")
                + ". It's open in the viewer — ask me to tighten the hook, change the angle, or turn it into an HTML card.")
        msg = await self._add_message(
            session_id, "assistant", text, route="essay", sources=self._cited_sources(refs, cited), artifact_ids=[art.id],
            provider=llm.provider, model=llm.model, latency_ms=int((time.perf_counter() - t0) * 1000),
            meta={**meta, "skill": "ship30", "lint": report.as_dict(), "revised": revised})
        async with session_scope() as db:
            (await db.get(Artifact, art.id)).message_id = msg.id
        yield _ev("done", message=self._public(msg))

    # ------------------------------------------------------------------ small utils
    @staticmethod
    def _parser_event(ev) -> dict | None:
        if ev.kind == "text":
            return _ev("delta", text=ev.text)
        if ev.kind == "artifact_start":
            return _ev("artifact_start", artifact_type=ev.artifact_type, title=ev.title)
        if ev.kind == "artifact_delta":
            return _ev("artifact_delta", text=ev.text)
        return None  # artifact_end is implied by the final `artifact` event

    @staticmethod
    def _src_dict(r: P.SourceRef) -> dict:
        return {"guest": r.hit.guest, "title": r.hit.title, "timestamp": r.hit.timestamp(), "url": r.hit.link}

    @staticmethod
    def _html_sources(refs: list[P.SourceRef], cited: list[int]) -> str:
        chosen = [r for r in refs if r.n in cited] or refs[:5]
        items = "".join(
            f"<li>[{r.n}] {html_lib.escape(r.hit.guest)} — {html_lib.escape(r.hit.title)} @ {r.hit.timestamp()}</li>" for r in chosen
        )
        return f'<footer style="margin-top:2rem;font-size:.8rem;color:#555"><strong>Sources</strong><ol style="margin:.4rem 0 0 1.2rem">{items}</ol></footer>'

    @staticmethod
    def _public_artifact(a: Artifact) -> dict:
        return {"id": a.id, "kind": a.kind, "title": a.title, "content": a.content, "sanitized": a.sanitized,
                "report": a.report or {}, "skill": a.skill, "message_id": a.message_id,
                "created_at": a.created_at.isoformat() if a.created_at else None}
