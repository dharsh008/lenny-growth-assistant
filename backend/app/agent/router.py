"""Deterministic intent router. Rules (not an LLM) so routing is fast, free, testable and
identical on a 7B local model and on Claude. The UI can also force a route via `mode`."""
import re
from dataclasses import dataclass

ESSAY_RE = re.compile(
    r"\bship\s*-?30\b|\batomic essay\b|\b(write|draft|compose|create|generate|turn)\b.{0,40}\b(an?\s+)?(essay|article|blog\s*post|newsletter|long[- ]form post)\b"
    r"|\bessay\b.{0,30}\b(about|on|from)\b|\b1,?250[- ]word\b",
    re.I,
)
ARTIFACT_VERB = r"(create|make|generate|build|produce|draft|write|design|give me|put together|turn|convert|render|show me|prepare)"
ARTIFACT_NOUN = (
    r"(markdown|\.md\b|\bmd file\b|html|css|one[- ]?pager|cheat ?sheet|checklist|template|playbook|framework card|infographic|"
    r"landing page|web ?page|webpage|slide|table|timeline|dashboard|scorecard|canvas|worksheet|document|\bdoc\b|artifact|"
    r"summary page|brief|memo|diagram|poster|card)"
)
ARTIFACT_RE = re.compile(rf"\b{ARTIFACT_VERB}\b.{{0,60}}\b{ARTIFACT_NOUN}|\bartifact\b", re.I)
HTML_HINT_RE = re.compile(r"\bhtml\b|\bcss\b|infographic|landing page|web ?page|webpage|dashboard|scorecard|poster|\bstyled\b|\bcard\b|\bvisual", re.I)
MD_HINT_RE = re.compile(r"markdown|\.md\b|\bmd\b", re.I)
CHITCHAT_RE = re.compile(
    r"^\s*(hi|hello|hey|yo|thanks|thank you|thx|ok(ay)?|cool|great|good (morning|evening|afternoon)|who are you|what can you do|help|what do you do)\b[\s!.?]*$",
    re.I,
)
FOLLOWUP_RE = re.compile(
    r"\b(it|that|this|those|these|they|them|he|she|his|her|their|the above|same|more|else|another|also|why|elaborate|expand|example|examples)\b|^(and|but|so|what about|how about)\b",
    re.I,
)


@dataclass(frozen=True)
class RouteDecision:
    route: str  # qa | essay | artifact | chitchat
    artifact_kind: str | None  # markdown | html
    reason: str


def route_message(text: str, mode: str = "auto") -> RouteDecision:
    if mode == "qa":
        return RouteDecision("qa", None, "forced by mode")
    if mode == "essay":
        return RouteDecision("essay", None, "forced by mode")
    if mode in ("artifact_md", "artifact_html"):
        return RouteDecision("artifact", "html" if mode == "artifact_html" else "markdown", "forced by mode")
    t = text.strip()
    if CHITCHAT_RE.match(t):
        return RouteDecision("chitchat", None, "greeting / capability question")
    if ESSAY_RE.search(t):
        return RouteDecision("essay", None, "essay/Ship-30 keywords")
    if ARTIFACT_RE.search(t):
        kind = "html" if HTML_HINT_RE.search(t) and not MD_HINT_RE.search(t) else "markdown"
        return RouteDecision("artifact", kind, f"artifact request ({kind})")
    return RouteDecision("qa", None, "default: grounded Q&A")


def looks_like_followup(text: str) -> bool:
    t = text.strip()
    return len(t.split()) <= 8 or bool(FOLLOWUP_RE.search(t))
