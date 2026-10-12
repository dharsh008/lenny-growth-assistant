"""Parse a Lenny's Podcast transcript.md (YAML frontmatter + timestamped turns)
and split it into retrieval chunks that stay traceable to speaker + timestamp."""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml

TURN_RE = re.compile(r"^(?P<speaker>[^\n()]{1,80}?) \((?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\):\s*(?P<rest>.*)$")
# Three speaker-line shapes occur in the corpus (see docs/architecture.md, "Data quirks"):
#   Lenny (00:12):            <- the common one
#   [00:00:28] Lenny: text    <- ryan-hoover
#   Adriel Frederick:         <- adriel-frederick (no timestamps at all)
TURN_B_RE = re.compile(r"^\[(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\]\s*(?P<speaker>[^\n:()\[\]]{1,80}?):\s*(?P<rest>.*)$")
TURN_C_RE = re.compile(r"^(?P<speaker>[A-Z][^\n:()\[\]]{0,60}):\s*$")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Turn:
    speaker: str
    seconds: int
    text: str

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass
class ParsedEpisode:
    id: str
    guest: str
    title: str
    youtube_url: str | None
    video_id: str | None
    publish_date: date | None
    duration_seconds: float | None
    keywords: list[str]
    source_path: str
    content_hash: str
    turns: list[Turn] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return sum(t.words for t in self.turns)


@dataclass
class ChunkData:
    ordinal: int
    start_seconds: int
    end_seconds: int
    speakers: list[str]
    text: str

    @property
    def word_count(self) -> int:
        return len(self.text.split())


def ts_to_seconds(ts: str) -> int:
    parts = [int(x) for x in ts.split(":")]  # supports MM:SS and HH:MM:SS
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def _split_frontmatter(raw: str) -> tuple[dict, str]:
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end == -1:
        return {}, raw
    meta = yaml.safe_load(raw[3:end]) or {}
    return (meta if isinstance(meta, dict) else {}), raw[end + 4 :]


def _match_turn(line: str, last_seconds: int) -> tuple[str, int, str] | None:
    m = TURN_RE.match(line)
    if m:
        return m["speaker"].strip(), ts_to_seconds(m["ts"]), m["rest"].strip()
    m = TURN_B_RE.match(line)
    if m:
        return m["speaker"].strip(), ts_to_seconds(m["ts"]), m["rest"].strip()
    m = TURN_C_RE.match(line)
    if m:
        return m["speaker"].strip(), last_seconds, ""
    return None


def parse_turns(body: str) -> list[Turn]:
    marker = body.find("## Transcript")
    if marker != -1:
        body = body[marker + len("## Transcript") :]
    turns: list[Turn] = []
    cur: Turn | None = None
    last = 0
    for raw_line in body.splitlines():
        line = raw_line.strip()
        hit = _match_turn(line, last) if line else None
        if hit:
            if cur and cur.text.strip():
                turns.append(cur)
            cur = Turn(hit[0], hit[1], hit[2])
            last = hit[1]
        elif cur is not None and line:
            cur.text = (cur.text + " " + line).strip()
    if cur and cur.text.strip():
        turns.append(cur)
    return turns


def parse_episode(path: Path, root: Path | None = None) -> ParsedEpisode:
    raw = path.read_text(encoding="utf-8", errors="replace")
    meta, body = _split_frontmatter(raw)
    slug = path.parent.name
    pub = meta.get("publish_date")
    if isinstance(pub, str):
        try:
            pub = datetime.strptime(pub, "%Y-%m-%d").date()
        except ValueError:
            pub = None
    elif isinstance(pub, datetime):
        pub = pub.date()
    return ParsedEpisode(
        id=slug,
        guest=str(meta.get("guest") or slug.replace("-", " ").title()),
        title=str(meta.get("title") or slug),
        youtube_url=meta.get("youtube_url"),
        video_id=meta.get("video_id"),
        publish_date=pub if isinstance(pub, date) else None,
        duration_seconds=float(meta["duration_seconds"]) if meta.get("duration_seconds") else None,
        keywords=[str(k) for k in (meta.get("keywords") or [])],
        source_path=str(path.relative_to(root)) if root else str(path),
        content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        turns=parse_turns(body),
    )


def _explode_long_turn(turn: Turn, max_words: int) -> list[Turn]:
    if turn.words <= max_words:
        return [turn]
    out: list[Turn] = []
    buf: list[str] = []
    n = 0
    for sent in SENTENCE_RE.split(turn.text):
        w = len(sent.split())
        if n + w > max_words and buf:
            out.append(Turn(turn.speaker, turn.seconds, " ".join(buf)))
            buf, n = [], 0
        buf.append(sent)
        n += w
    if buf:
        out.append(Turn(turn.speaker, turn.seconds, " ".join(buf)))
    return out


def chunk_turns(turns: list[Turn], target_words: int = 320, overlap_turns: int = 1) -> list[ChunkData]:
    """Greedy turn-aligned chunking. A chunk closes once it reaches target_words;
    the trailing turn (if short) is repeated at the start of the next chunk so
    context isn't cut mid-thought. Timestamps always point at the first turn."""
    exploded: list[Turn] = []
    for t in turns:
        exploded.extend(_explode_long_turn(t, int(target_words * 1.5)))

    chunks: list[ChunkData] = []
    cur: list[Turn] = []
    words = 0
    fresh = 0  # turns added since the last flush (excludes carried-over overlap)

    def flush() -> None:
        chunks.append(
            ChunkData(
                ordinal=len(chunks),
                start_seconds=cur[0].seconds,
                end_seconds=cur[-1].seconds,
                speakers=sorted({t.speaker for t in cur}),
                text="\n".join(f"{t.speaker}: {t.text}" for t in cur),
            )
        )

    for t in exploded:
        cur.append(t)
        words += t.words
        fresh += 1
        if words >= target_words:
            flush()
            carry = [x for x in cur[-overlap_turns:] if x.words <= target_words // 2] if overlap_turns else []
            cur, words, fresh = list(carry), sum(x.words for x in carry), 0
    if fresh:
        flush()
    return chunks


def youtube_link(url: str | None, seconds: int) -> str | None:
    if not url:
        return None
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}t={seconds}s"
