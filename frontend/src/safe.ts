/** Rendering helpers: every piece of model output passes through here before it reaches the DOM. */
import DOMPurify, { type Config } from "dompurify";
import { marked } from "marked";

marked.setOptions({ gfm: true, breaks: false });

// Links in rendered markdown open in a new tab and never leak the opener/referrer.
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    const href = node.getAttribute("href") ?? "";
    if (/^https?:\/\//i.test(href)) {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer nofollow");
    } else if (!href.startsWith("#")) {
      node.removeAttribute("href");
    }
  }
  if (node.tagName === "IMG") {
    const src = node.getAttribute("src") ?? "";
    if (!/^data:image\//i.test(src)) node.removeAttribute("src"); // no remote images (tracking/exfiltration)
  }
});

const MD_PURIFY: Config = {
  USE_PROFILES: { html: true },
  FORBID_TAGS: ["style", "form", "input", "button", "textarea", "select", "iframe", "object", "embed", "svg", "math"],
  FORBID_ATTR: ["style", "srcset"],
};

export function renderMarkdown(md: string): string {
  const html = marked.parse(md, { async: false }) as string;
  return String(DOMPurify.sanitize(html, MD_PURIFY));
}

/** Chat bubbles: markdown + clickable citation markers [n] that map to the message's sources. */
export function renderChat(md: string, sourceNums: Set<number>): string {
  const withMarkers = md.replace(/\[(\d{1,2})\](?!\()/g, (m, n) => (sourceNums.has(Number(n)) ? `<cite-ref data-n="${n}"></cite-ref>` : m));
  const html = marked.parse(withMarkers, { async: false }) as string;
  const clean = String(DOMPurify.sanitize(html, { ...MD_PURIFY, ADD_TAGS: ["cite-ref"], ADD_ATTR: ["data-n"] }));
  return clean.replace(/<cite-ref data-n="(\d+)"><\/cite-ref>/g, (_m, n) => `<button type="button" class="cite" data-n="${n}" aria-label="Show source ${n}">${n}</button>`);
}

/** The exact policy the HTML artifact viewer enforces (mirrors backend wrap_srcdoc, tested in safe.test.ts). */
export const ARTIFACT_CSP =
  "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'";

export const ARTIFACT_SANDBOX = ""; // empty = every sandbox restriction applies (no scripts, no same-origin, no forms, no popups)

export function htmlSrcDoc(cleanHtml: string): string {
  return (
    `<!doctype html><html><head><meta charset="utf-8">` +
    `<meta http-equiv="Content-Security-Policy" content="${ARTIFACT_CSP}">` +
    `<meta name="viewport" content="width=device-width,initial-scale=1">` +
    `<style>:root{color-scheme:light}body{margin:0;padding:20px;font:16px/1.55 system-ui,sans-serif;color:#111;background:#fff}</style>` +
    `</head><body>${cleanHtml}</body></html>`
  );
}
