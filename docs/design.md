# Design

## Principles
1. **Trust is the product.** Every answer shows where it came from: citation chips `[n]` open the exact source (guest, episode, timestamp deep link, quote). Refusals are first-class, not errors.
2. **One box, no jargon.** Users type; the router decides. Output-type chips (Ask / Ship 30 essay / Markdown / HTML) are optional overrides, not required.
3. **Calm and restrained.** Cool neutral surfaces, one accent (burnt orange, used for primary action + citations), one radius scale (6/10/14), 4-px spacing scale, no gradients/nested cards/decorative loops. (Informed by the Impeccable anti-pattern list.)
4. **Show state honestly.** Retrieving → generating → revising are named; provider/model/latency are visible on every answer.

## Information architecture
Sidebar (conversations, new chat, KB stats) · Chat column (provider toggle, banners, thread, composer) · Artifact viewer (title/switcher, Preview|Code tabs, Copy/Download/Security, skill report).
Artifacts open automatically beside the chat when created; each assistant message that produced one carries a card to reopen it.

## Key interaction states
| Area | States |
|---|---|
| Empty thread | headline + 4 starter prompts (click fills composer) |
| Sending | optimistic user bubble; assistant placeholder with named status; **Stop** replaces Send |
| Streaming answer | text appears incrementally; citation chips become clickable on completion |
| Essay/Artifact | card shows "Writing…"; viewer streams Markdown live (HTML shown as code until the sandbox render is safe) |
| Refusal | tagged "Not in the transcripts", closest episodes listed |
| Weak evidence | "Limited evidence" tag |
| Errors | inline red message + Retry; banner for provider problems (with the fix command in text) |
| Provider | segmented control with status dot **and** text (not colour alone); disabled while busy |
| Viewer | empty / streaming / ready / HTML security panel / skill-report disclosure |

## Responsive behaviour
≥1180 px: three columns. 820–1180 px: sidebar becomes a drawer (menu button), chat + viewer side by side. <820 px: single column; viewer is a full-screen view with "← Chat" and an "Artifact" button in the top bar.

## Accessibility
Landmarks (`nav`, `main`, `section`), skip link, visible focus ring, `role=log aria-live=polite` thread, radio-group semantics for provider and mode chips, tabs with `aria-selected`, labelled controls (`sr-only` labels, icon buttons have names), citation buttons have `aria-label`, iframe has a `title`, status conveyed by text + icon not colour only, AA-contrast tokens in light and dark (`prefers-color-scheme`), `prefers-reduced-motion` honoured, keyboard: Enter/Shift+Enter, all controls reachable.

## Decisions
- Reading font for essays/docs is a serif stack; UI is system sans — no web-font downloads (works offline, no tracking).
- Markdown artifacts render in-page through DOMPurify (so links work); HTML artifacts render only inside the sandbox.
- The mode chips reset to Auto after each send to avoid surprising sticky behaviour.
- Read-only viewer (copy/download) to keep scope tight; edits are done by asking the assistant.
