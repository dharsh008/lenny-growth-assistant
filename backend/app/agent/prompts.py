"""Prompt construction + source/citation helpers. Prompts carry a [[task:...]] marker which
is (a) harmless to real models and (b) lets the offline mock LLM in tests pick a response."""
import re
from dataclasses import dataclass

from app.retrieval.service import Hit

QA_SYSTEM = """[[task:qa]]
You are the Lenny Growth Assistant, an internal assistant for product and growth teams. You answer ONLY from the numbered SOURCES
(excerpts of Lenny's Podcast transcripts) supplied in the user message.

Rules:
1. Use only facts that appear in the SOURCES. Never add outside knowledge, statistics, names or quotes.
2. Cite every claim with the source number in square brackets, e.g. [2] or [1][3]. Attribute ideas to the guest by name.
3. If the SOURCES do not directly answer the question, reply with exactly: NOT_SUPPORTED
   (A topic that is merely mentioned in passing does NOT count as an answer.)
4. If sources partially answer it, answer the supported part and state clearly what the transcripts do not cover.
5. Be concise and skimmable: short paragraphs, bullets for lists, **bold** for the key idea. No preamble, no closing offers.
6. Use the CONVERSATION only to resolve what "it/that/they" refer to; do not treat it as a source of facts."""

ARTIFACT_SYSTEM = """[[task:{task}]]
You are the Lenny Growth Assistant. Create a {kind_label} artifact for the user grounded ONLY in the numbered SOURCES and the CONVERSATION.

Output protocol (strict):
- Write one or two sentences of chat reply, then the artifact wrapped exactly as:
  <artifact type="{kind}" title="SHORT TITLE">
  ...content...
  </artifact>
- Optionally one closing sentence after the closing tag. Never put the artifact in a code fence.
{kind_rules}
- Ground every claim in the SOURCES with inline [n] citations (HTML: use <sup>[n]</sup>). Do not invent facts, numbers or quotes.
- If the SOURCES cannot support the request, say so in chat and do NOT emit an artifact."""

MD_RULES = "- Markdown: a single # title, ## sections, bullets, tables where useful, and a final '## Sources' list mapping [n] to guest + episode."
HTML_RULES = (
    "- HTML: a COMPLETE self-contained snippet (no <html>/<head>/<body> wrapper needed). Inline CSS only, in a <style> block or style attributes.\n"
    "- NO scripts, NO external files, fonts, images or links (they are blocked). Use semantic tags, system fonts, good contrast, responsive layout\n"
    "  (flex/grid, max-width, relative units). Inline SVG is allowed for simple charts/icons."
)

CONDENSE_SYSTEM = """[[task:condense]]
Rewrite the user's latest message as a standalone search query (6-14 keywords, no punctuation), resolving pronouns using the conversation.
Output only the query."""


@dataclass
class SourceRef:
    n: int
    hit: Hit
    excerpt: str

    def public(self, cited: bool = True) -> dict:
        h = self.hit
        return {
            "n": self.n, "chunk_id": h.chunk_id, "episode_id": h.episode_id, "guest": h.guest, "title": h.title,
            "timestamp": h.timestamp(), "start_seconds": h.start_seconds, "url": h.link, "publish_date": h.publish_date,
            "quote": self.excerpt[:420].strip(), "score": round(h.score, 2), "cited": cited,
        }


def excerpt(text: str, terms: list[str], max_chars: int) -> str:
    """Trim a chunk to max_chars, centred on the first matched query term so the relevant lines survive."""
    if len(text) <= max_chars:
        return text
    low = text.lower()
    pos = -1
    for t in terms:
        i = low.find(t[: max(4, len(t) - 2)])  # crude stem-prefix match (terms are stemmed)
        if i != -1 and (pos == -1 or i < pos):
            pos = i
    start = max(0, (pos if pos != -1 else 0) - max_chars // 3)
    start = text.rfind(" ", 0, start) + 1 if start else 0
    end = min(len(text), start + max_chars)
    cut = text[start:end]
    return ("…" if start else "") + cut.strip() + ("…" if end < len(text) else "")


def build_sources(hits: list[Hit], terms: list[str], char_budget: int, max_sources: int) -> list[SourceRef]:
    hits = hits[:max_sources]
    if not hits:
        return []
    per = max(600, char_budget // len(hits))
    return [SourceRef(i, h, excerpt(h.text, terms, per)) for i, h in enumerate(hits, 1)]


def sources_block(refs: list[SourceRef]) -> str:
    parts = []
    for r in refs:
        h = r.hit
        parts.append(f'[{r.n}] Episode: "{h.title}" — guest: {h.guest} — {h.publish_date or "n.d."} — @{h.timestamp()}\n{r.excerpt}')
    return "\n\n".join(parts)


CITE_RE = re.compile(r"\[(\d{1,2})\]")


def strip_citations(text: str) -> str:
    return re.sub(r"\s*\[\d{1,2}\]", "", text)


def history_block(history: list[tuple[str, str]], max_messages: int, max_chars: int) -> str:
    """history: [(role, content)] oldest-first. Keeps the most recent turns within the char budget."""
    out: list[str] = []
    used = 0
    for role, content in reversed(history[-max_messages:]):
        c = strip_citations(content).strip()
        c = c if len(c) <= 900 else c[:900] + "…"
        line = f"{'User' if role == 'user' else 'Assistant'}: {c}"
        if used + len(line) > max_chars:
            break
        out.append(line)
        used += len(line)
    return "\n".join(reversed(out))


def validate_citations(text: str, n_sources: int) -> tuple[str, list[int]]:
    """Drop [n] that point at non-existent sources (model hallucination). Returns (clean_text, cited_numbers)."""
    cited: list[int] = []

    def repl(m: re.Match) -> str:
        n = int(m.group(1))
        if 1 <= n <= n_sources:
            if n not in cited:
                cited.append(n)
            return m.group(0)
        return ""

    return CITE_RE.sub(repl, text), sorted(cited)
