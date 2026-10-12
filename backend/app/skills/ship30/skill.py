"""The Ship 30 for 30 skill: a multi-step pipeline (plan -> draft -> lint -> revise),
not a single prompt. The principles live in SKILL.md and are injected into the prompts;
`lint()` checks the result deterministically so the revise step has concrete targets."""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SKILL_DIR = Path(__file__).parent
_SKILL_MD = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")


def principles() -> str:
    """SKILL.md body without the YAML frontmatter."""
    return re.sub(r"^---.*?---\s*", "", _SKILL_MD, count=1, flags=re.S)


PLAN_SYSTEM = """[[task:essay_plan]]
You are the planning step of the Ship 30 for 30 essay skill. Follow these principles:

{principles}

Using ONLY the numbered SOURCES the user provides, return a JSON object (no prose, no code fence) with keys:
  "headline": string (Who/What/Why, includes a number),
  "audience": string, "angle_4a": one of Actionable|Analytical|Aspirational|Anthropological,
  "pattern": one of Steps|Lessons|Mistakes,
  "hook": string (<= 2 short sentences, outcome-first, curiosity gap),
  "sections": array of 4-6 objects {{"heading": string, "point": string, "source": integer}} where "source" is the number of the SOURCE that supports the point,
  "takeaway": string (one specific action the reader can take today).
Only include sections that a source supports."""

DRAFT_SYSTEM = """[[task:essay_draft]]
You are the drafting step of the Ship 30 for 30 essay skill. Follow these principles exactly:

{principles}

Write the essay in Markdown, about {target} words (acceptable range {lo}-{hi}).
Structure: one `# Headline`, a hook of at most two short sentences, then one `## Heading` per planned section, then `## Takeaway`.
Use bullets and selective **bold**. Cite sources inline as [n] using ONLY the numbers in SOURCES. Do not add a sources list (it is appended automatically).
Output only the essay."""

REVISE_SYSTEM = """[[task:essay_draft]]
You are the revision step of the Ship 30 for 30 essay skill. Rewrite the DRAFT so that it passes every check listed under FIXES while keeping
the same structure, voice and citations. Output only the full revised essay in Markdown.

{principles}"""


@dataclass
class Lint:
    words: int
    checks: dict[str, bool]
    messages: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(self.checks.values())

    def as_dict(self) -> dict:
        return {"words": self.words, "checks": self.checks, "passed": self.passed}


def word_count(md: str) -> int:
    body = re.sub(r"```.*?```", " ", md, flags=re.S)
    body = re.sub(r"[#*_>`\-]+", " ", body)
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’\-]*", body))


def lint(md: str, n_sources: int, target: int = 1250, tol: float = 0.12) -> Lint:
    lo, hi = int(target * (1 - tol)), int(target * (1 + tol))
    words = word_count(md)
    lines = md.splitlines()
    h2 = [ln for ln in lines if ln.startswith("## ")]
    bullets = [ln for ln in lines if re.match(r"\s*[-*]\s+\S", ln)]
    bold = re.findall(r"\*\*[^*\n]{3,}\*\*", md)
    cites = {int(x) for x in re.findall(r"\[(\d{1,2})\]", md)}
    valid_cites = {c for c in cites if 1 <= c <= n_sources}
    # hook = first prose paragraph after the H1
    paras = [p.strip() for p in re.split(r"\n\s*\n", md) if p.strip() and not p.strip().startswith("#")]
    hook_words = len(paras[0].split()) if paras else 999
    heads_takeaway = any(re.search(r"take-?away|do this|try this|next step|bottom line", h, re.I) for h in h2)
    long_paras = [p for p in paras if len(re.findall(r"[.!?](?:\s|$)", p)) >= 5 and not p.lstrip().startswith(("-", "*"))]
    checks = {
        "word_count_in_range": lo <= words <= hi,
        "has_headline": any(ln.startswith("# ") for ln in lines),
        "has_3plus_sections": len(h2) >= 3,
        "has_bullets": len(bullets) >= 3,
        "has_bold_emphasis": len(bold) >= 3,
        "short_hook": hook_words <= 55,
        "has_takeaway_section": heads_takeaway,
        "grounded_citations": len(valid_cites) >= min(3, max(1, n_sources)) and cites <= set(range(1, n_sources + 1)),
        "no_wall_of_text": len(long_paras) <= max(1, len(paras) // 4),
    }
    msgs = []
    if not checks["word_count_in_range"]:
        msgs.append(f"Length is {words} words; target {target} (accepted {lo}-{hi}). {'Expand with concrete detail from the sources' if words < lo else 'Cut filler and repetition'}.")
    if not checks["has_3plus_sections"]: msgs.append("Use at least three `## ` sections.")
    if not checks["has_bullets"]: msgs.append("Turn list-like paragraphs into at least three bullets.")
    if not checks["has_bold_emphasis"]: msgs.append("Bold the key sentence of each section (at least three **bold** phrases).")
    if not checks["short_hook"]: msgs.append("Shorten the opening hook to at most two short sentences.")
    if not checks["has_takeaway_section"]: msgs.append("End with a `## Takeaway` section containing one specific action.")
    if not checks["grounded_citations"]: msgs.append(f"Cite at least three different sources inline as [n], using only 1-{n_sources}.")
    if not checks["no_wall_of_text"]: msgs.append("Break up paragraphs of five or more sentences (use 1/3/1 rhythm).")
    if not checks["has_headline"]: msgs.append("Start with a single `# Headline`.")
    return Lint(words, checks, msgs)


def parse_plan(raw: str, fallback_topic: str, n_sources: int) -> dict:
    """Small local models sometimes wrap JSON in prose/fences or return junk; always yield a usable plan."""
    m = re.search(r"\{.*\}", raw, re.S)
    plan: dict = {}
    if m:
        try:
            plan = json.loads(m.group(0))
        except json.JSONDecodeError:
            plan = {}
    secs = [s for s in plan.get("sections", []) if isinstance(s, dict) and s.get("heading")]
    for s in secs:
        try:
            s["source"] = min(max(int(s.get("source", 1)), 1), max(n_sources, 1))
        except (TypeError, ValueError):
            s["source"] = 1
    if len(secs) < 3:
        secs = [{"heading": f"Lesson {i}", "point": "Draw this lesson from the sources.", "source": i} for i in range(1, min(5, max(n_sources, 3)) + 1)]
    return {
        "headline": plan.get("headline") or f"5 Lessons On {fallback_topic.strip().capitalize()} From Lenny's Podcast Guests",
        "audience": plan.get("audience") or "product and growth builders",
        "angle_4a": plan.get("angle_4a") or "Actionable",
        "pattern": plan.get("pattern") or "Lessons",
        "hook": plan.get("hook") or "",
        "sections": secs[:6],
        "takeaway": plan.get("takeaway") or "Pick one lesson and apply it this week.",
    }


def sources_footer(sources: list[dict]) -> str:
    lines = ["", "---", "", "### Sources", ""]
    for i, s in enumerate(sources, 1):
        link = s.get("url")
        label = f"{s['guest']} — “{s['title']}” @ {s['timestamp']}"
        lines.append(f"{i}. [{label}]({link})" if link else f"{i}. {label}")
    return "\n".join(lines)
