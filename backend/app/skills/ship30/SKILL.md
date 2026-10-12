---
name: ship30-essay
description: Turn grounded knowledge (Lenny's Podcast transcript excerpts) into a ~1,250-word Ship 30 for 30-style digital essay with a strong hook, skimmable structure and a specific takeaway.
---

# Ship 30 for 30 essay skill

Source of the principles: the Ship 30 for 30 "Ultimate Guide" (linked in the assignment). The rules below are the
parts of that guide that apply to a long-form (~1,250 words) piece; each is enforced either by the prompt or by the
deterministic linter in `skill.py`.

## 1. Pick the idea (Endless Idea Generator)
Topic + Credibility + 4A path + Proven Approach.
- **Topic**: narrow until it is clear who the piece is for *and* who it is not for.
- **Credibility** (one sentence): this skill always uses *"I'm curating experts"* — the expertise comes from Lenny's guests, and the
  essay says so by naming them and the episode. Never claim personal experience that is not in the sources.
- **4A path** — choose exactly one lens: Actionable (how to), Analytical (what the evidence shows), Aspirational (proof it is possible),
  Anthropological (why people behave this way). Default to Actionable for product/growth questions.
- **Proven approach**: one consistent pattern — Steps, Lessons, or Mistakes. Do not mix patterns.

## 2. Headline (Who / What / Why)
Answer WHO it is for, WHAT it is about, WHY read it. Use a number (a clear container), name the audience, state the outcome.
Clear beats clever. Use a *curiosity gap*: give the start and end, not the middle. Deliver what the headline promises.

## 3. Skeleton before prose
Headline → introduction (hook) → main points (the pattern, N points) → conclusion (the takeaway).

## 4. Hook
Open with the outcome or a surprising claim, show the humble start, tease the middle. Max two short sentences before the first
H2. Rate of revelation: every sentence must move the idea forward — no throat-clearing, no restating the question.

## 5. Skimmability (Wheels & Spokes)
- Wheels = `##` sections (one per main point). Spokes = bullets / bold sentences inside them. Only one heading level inside the body.
- Convert any paragraph that lists things into a bullet list. Use **bold** for the one sentence per section a skimmer must not miss;
  use *italics* sparingly for emphasis or quoted phrasing.

## 6. Writing rhythm
Alternate short and long sentences. Prefer 1/3/1 (one-sentence opener, three-sentence body, one-sentence close) and 1/5/1 blocks;
stack them. Avoid bad rhythms: all one-liners (1/1/1), uniform two-sentence paragraphs (2/2/2), or walls of five-plus sentences (5/5/5).

## 7. Differentiation (Tequila test)
List the conventional answers everybody gives; then lead with the angle the transcripts support that those answers miss.

## 8. Grounding rules (specific to this product)
- Every factual claim about what a guest said/believes carries an inline citation like `[2]` that maps to a numbered source.
- Never invent numbers, quotes, companies or anecdotes. If the sources do not support a section, cut the section.
- Prefer short paraphrase over long quotation; quote at most one short phrase per source.
- End with **one specific, useful takeaway** the reader can do today (a concrete action, not "keep learning").
