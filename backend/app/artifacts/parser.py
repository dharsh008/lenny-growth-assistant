"""Incremental parser that splits a model's streamed text into chat prose and
<artifact type="..." title="...">...</artifact> blocks, tolerant of tags that are
split across stream chunks. Also provides fallbacks for weaker local models that
ignore the tag protocol."""
import re
from dataclasses import dataclass

OPEN_RE = re.compile(r'<artifact\b([^>]*)>', re.I)
CLOSE = "</artifact>"
ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
FENCE_RE = re.compile(r"```(html|markdown|md)\s*\n(.*?)```", re.S | re.I)


@dataclass
class Event:
    kind: str  # text | artifact_start | artifact_delta | artifact_end
    text: str = ""
    artifact_type: str = "markdown"
    title: str = "Untitled"


def _norm_type(t: str | None) -> str:
    t = (t or "markdown").lower()
    return "html" if t in ("html", "htm", "text/html") else "markdown"


class ArtifactStreamParser:
    def __init__(self) -> None:
        self.buf = ""
        self.inside = False
        self.type = "markdown"
        self.title = "Untitled"
        self.artifact_text: list[str] = []
        self.prose: list[str] = []
        self.completed: list[tuple[str, str, str]] = []  # (type, title, content)

    def feed(self, chunk: str) -> list[Event]:
        self.buf += chunk
        return self._drain(final=False)

    def finish(self) -> list[Event]:
        evs = self._drain(final=True)
        if self.inside:  # model stopped before closing the tag: keep what we have
            evs.append(self._close())
        return evs

    def _close(self) -> Event:
        self.inside = False
        self.completed.append((self.type, self.title, "".join(self.artifact_text).strip("\n")))
        self.artifact_text = []
        return Event("artifact_end", artifact_type=self.type, title=self.title)

    def _drain(self, final: bool) -> list[Event]:
        out: list[Event] = []
        while self.buf:
            if not self.inside:
                m = OPEN_RE.search(self.buf)
                if m:
                    pre = self.buf[: m.start()]
                    if pre:
                        out.append(Event("text", pre)); self.prose.append(pre)
                    attrs = dict(ATTR_RE.findall(m.group(1)))
                    self.type, self.title = _norm_type(attrs.get("type")), (attrs.get("title") or "Untitled")[:200]
                    self.inside = True
                    self.buf = self.buf[m.end():].lstrip("\n")
                    out.append(Event("artifact_start", artifact_type=self.type, title=self.title))
                    continue
                # hold back a possible partial "<artifact ..." tag at the end of the buffer
                cut = self.buf.rfind("<")
                if not final and cut != -1 and "<artifact".startswith(self.buf[cut:cut + 9].lower()[: len(self.buf) - cut]):
                    safe, self.buf = self.buf[:cut], self.buf[cut:]
                else:
                    safe, self.buf = self.buf, ""
                if safe:
                    out.append(Event("text", safe)); self.prose.append(safe)
                break
            idx = self.buf.lower().find(CLOSE)
            if idx != -1:
                body = self.buf[:idx]
                if body:
                    out.append(Event("artifact_delta", body)); self.artifact_text.append(body)
                self.buf = self.buf[idx + len(CLOSE):]
                out.append(self._close())
                continue
            # no closing tag yet: flush all but a possible partial "</artifact" suffix
            keep = 0
            if not final:
                for n in range(min(len(CLOSE) - 1, len(self.buf)), 0, -1):
                    if CLOSE.startswith(self.buf[-n:].lower()):
                        keep = n
                        break
            emit, self.buf = (self.buf[: len(self.buf) - keep], self.buf[len(self.buf) - keep:]) if keep else (self.buf, "")
            if emit:
                out.append(Event("artifact_delta", emit)); self.artifact_text.append(emit)
            break
        return out


def fallback_extract(full_text: str, kind_hint: str) -> tuple[str, list[tuple[str, str, str]]]:
    """If the model never produced an <artifact> tag, salvage one: first ```html / ```markdown
    fence, otherwise treat the whole reply as a markdown (or html) document."""
    m = FENCE_RE.search(full_text)
    if m:
        kind = "html" if m.group(1).lower() == "html" else "markdown"
        prose = (full_text[: m.start()] + full_text[m.end():]).strip()
        return prose or "Here's the artifact.", [(kind, "Untitled", m.group(2).strip())]
    body = full_text.strip()
    if not body:
        return "", []
    title = "Untitled"
    h = re.search(r"^#\s+(.+)$", body, re.M)
    if h:
        title = h.group(1).strip()[:120]
    return "I've put this in the viewer.", [(kind_hint, title, body)]
