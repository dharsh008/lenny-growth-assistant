"""Retrieval service: loads chunks from Postgres into the BM25 index, runs
searches with per-episode diversification, and decides whether the evidence is
strong enough to answer (the 'grounding gate')."""
import asyncio
import time
from functools import lru_cache
from dataclasses import dataclass, field

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Chunk, Episode
from app.db.session import session_scope
from app.ingest.parser import youtube_link
from app.retrieval.bm25 import BM25Index, tokenize

log = get_logger("retrieval")


@dataclass
class Hit:
    chunk_id: int
    episode_id: str
    guest: str
    title: str
    youtube_url: str | None
    publish_date: str | None
    start_seconds: int
    end_seconds: int
    speakers: list[str]
    text: str
    score: float
    coverage: float
    matched_terms: list[str] = field(default_factory=list)

    @property
    def link(self) -> str | None:
        return youtube_link(self.youtube_url, self.start_seconds)

    def timestamp(self) -> str:
        s = self.start_seconds
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


@dataclass
class SearchResult:
    query: str
    hits: list[Hit]
    supported: bool
    best_coverage: float
    best_score: float
    terms: list[str]
    took_ms: int
    reason: str = ""
    strength: str = "none"  # none | weak | strong  (see RetrievalService.search)
    corroborating_episodes: int = 0


class RetrievalService:
    def __init__(self) -> None:
        self.index = BM25Index()
        self.loaded_at: float | None = None
        self.episode_count = 0
        self._lock = asyncio.Lock()

    @property
    def ready(self) -> bool:
        return self.index.ready

    def status(self) -> dict:
        return {
            "ready": self.ready,
            "chunks": len(self.index),
            "episodes": self.episode_count,
            "loaded_at": self.loaded_at,
        }

    async def reload(self) -> None:
        async with self._lock:
            t0 = time.perf_counter()
            async with session_scope() as db:
                eps = {e.id: e for e in (await db.execute(select(Episode))).scalars()}
                rows = (await db.execute(select(Chunk).order_by(Chunk.id))).scalars().all()
            items = []
            for c in rows:
                ep = eps.get(c.episode_id)
                if ep is None:
                    continue
                payload = {
                    "chunk_id": c.id,
                    "episode_id": ep.id,
                    "guest": ep.guest,
                    "title": ep.title,
                    "youtube_url": ep.youtube_url,
                    "publish_date": ep.publish_date.isoformat() if ep.publish_date else None,
                    "start_seconds": c.start_seconds,
                    "end_seconds": c.end_seconds,
                    "speakers": c.speakers,
                    "text": c.text,
                }
                # title + guest are prepended so topic-level queries ("Brian Chesky") hit the right episode
                items.append((c.id, f"{ep.title} {ep.guest} {c.text}", payload))
            index = BM25Index()
            await asyncio.to_thread(index.build, items)  # CPU-bound: keep the event loop free
            self.index = index
            self.episode_count = len(eps)
            self.loaded_at = time.time()
            log.info("index_loaded", chunks=len(items), episodes=len(eps), took_ms=int((time.perf_counter() - t0) * 1000))

    def search(self, query: str, k: int | None = None) -> SearchResult:
        cfg = get_settings()
        k = k or cfg.retrieval_top_k
        t0 = time.perf_counter()
        if not self.ready:
            return SearchResult(query, [], False, 0.0, 0.0, [], 0, reason="index_empty")
        scored, terms = self.index.search(query, k=k * 4)
        if not scored:
            return SearchResult(query, [], False, 0.0, 0.0, terms, int((time.perf_counter() - t0) * 1000), reason="no_matches")
        qset = set(terms)
        boosted = []
        for sd in scored:
            gt = self._guest_terms(sd.doc.payload["guest"])
            # explicit guest mention ("what did Brian Chesky say...") -> favour that episode
            boost = 1.6 if len(gt) >= 2 and gt <= qset else 1.0
            boosted.append((sd.score * boost, sd))
        boosted.sort(key=lambda x: x[0] * (0.6 + 0.4 * x[1].coverage), reverse=True)

        per_ep: dict[str, int] = {}
        hits: list[Hit] = []
        for sc, s in boosted:
            p = s.doc.payload
            if per_ep.get(p["episode_id"], 0) >= cfg.max_chunks_per_episode:
                continue
            per_ep[p["episode_id"]] = per_ep.get(p["episode_id"], 0) + 1
            hits.append(Hit(score=sc, coverage=s.coverage, matched_terms=s.matched_terms, **p))
            if len(hits) >= k:
                break
        best = max(hits, key=lambda h: h.coverage)
        best_score = max(h.score for h in hits)
        # How many *different* episodes independently contain (nearly) all the query concepts?
        # Lexical overlap alone can be a coincidence (a sourdough aside in a sales episode), so a
        # single-episode match is only 'weak' and the answering prompt must verify it.
        floor = max(0.5, best.coverage * 0.8)
        corroborating = len({h.episode_id for h in hits if h.coverage >= floor})
        if best_score < cfg.retrieval_min_score or best.coverage < cfg.retrieval_min_coverage:
            strength, reason = "none", ("weak_score" if best_score < cfg.retrieval_min_score else "low_coverage")
        elif corroborating >= 2:
            strength, reason = "strong", ""
        else:
            strength, reason = "weak", "single_source"
        return SearchResult(
            query, hits, strength != "none", best.coverage, best_score, terms,
            int((time.perf_counter() - t0) * 1000), reason, strength, corroborating,
        )

    def get_hits(self, chunk_ids: list[int]) -> list[Hit]:
        out = []
        for cid in chunk_ids:
            i = self.index.by_key.get(cid)
            if i is not None:
                out.append(Hit(score=0.0, coverage=0.0, matched_terms=[], **self.index.docs[i].payload))
        return out

    @lru_cache(maxsize=4096)
    def _guest_terms(self, guest: str) -> frozenset[str]:
        return frozenset(tokenize(guest))
