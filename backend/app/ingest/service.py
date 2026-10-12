"""Idempotent, incremental ingestion: episodes whose sha256 hasn't changed are
skipped, changed ones have their chunks replaced, removed ones are deleted.
Re-run any time with `python -m app.ingest` (or POST /api/admin/reingest)."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, func, select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Chunk, Episode, IngestRun
from app.db.session import session_scope
from app.ingest.fetch import download_transcripts, find_transcripts
from app.ingest.parser import chunk_turns, parse_episode

log = get_logger("ingest")


async def ingest(root: Path | None = None, *, download_if_missing: bool = True) -> dict:
    cfg = get_settings()
    root = Path(root or cfg.transcripts_dir)
    files = find_transcripts(root) if root.exists() else []
    if not files and download_if_missing:
        await asyncio.to_thread(download_transcripts, cfg.transcripts_repo_tarball, root)
        files = find_transcripts(root)
    if not files:
        raise FileNotFoundError(f"No transcripts found under {root}. Set TRANSCRIPTS_DIR or allow download.")

    async with session_scope() as db:
        run = IngestRun(status="running", episodes_total=len(files))
        db.add(run)
        await db.flush()
        run_id = run.id

    changed = skipped = chunks_total = 0
    try:
        async with session_scope() as db:
            existing = {e.id: e.content_hash for e in (await db.execute(select(Episode))).scalars()}
        seen: set[str] = set()
        for path in files:
            try:
                ep = await asyncio.to_thread(parse_episode, path, root)
            except Exception as exc:  # noqa: BLE001 - one bad file must not abort the run
                log.error("episode_parse_failed", path=str(path), error=str(exc))
                continue
            seen.add(ep.id)
            if existing.get(ep.id) == ep.content_hash:
                skipped += 1
                continue
            chunks = chunk_turns(ep.turns, cfg.chunk_target_words, cfg.chunk_overlap_turns)
            if not chunks:
                log.warning("episode_no_chunks", episode=ep.id)
                continue
            async with session_scope() as db:
                await db.execute(delete(Chunk).where(Chunk.episode_id == ep.id))
                await db.execute(delete(Episode).where(Episode.id == ep.id))
                db.add(
                    Episode(
                        id=ep.id, guest=ep.guest, title=ep.title, youtube_url=ep.youtube_url,
                        video_id=ep.video_id, publish_date=ep.publish_date,
                        duration_seconds=ep.duration_seconds, keywords=ep.keywords,
                        word_count=ep.word_count, source_path=ep.source_path, content_hash=ep.content_hash,
                    )
                )
                await db.flush()
                db.add_all(
                    Chunk(
                        episode_id=ep.id, ordinal=c.ordinal, start_seconds=c.start_seconds,
                        end_seconds=c.end_seconds, speakers=c.speakers, text=c.text, word_count=c.word_count,
                    )
                    for c in chunks
                )
            changed += 1
            chunks_total += len(chunks)
            if changed % 25 == 0:
                log.info("ingest_progress", changed=changed, total=len(files))
        removed = set(existing) - seen
        if removed:
            async with session_scope() as db:
                await db.execute(delete(Chunk).where(Chunk.episode_id.in_(removed)))
                await db.execute(delete(Episode).where(Episode.id.in_(removed)))
        async with session_scope() as db:
            total_chunks = (await db.execute(select(func.count(Chunk.id)))).scalar_one()
            run = await db.get(IngestRun, run_id)
            run.status, run.episodes_changed, run.chunks_total = "ok", changed, total_chunks
            run.finished_at = datetime.now(timezone.utc)
        summary = {"episodes": len(files), "changed": changed, "skipped": skipped, "removed": len(removed), "chunks": total_chunks}
        log.info("ingest_done", **summary)
        return summary
    except Exception as exc:
        async with session_scope() as db:
            run = await db.get(IngestRun, run_id)
            run.status, run.error = "failed", str(exc)[:500]
        log.error("ingest_failed", error=str(exc))
        raise
