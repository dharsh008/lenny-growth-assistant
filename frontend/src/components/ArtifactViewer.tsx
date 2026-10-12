import { useMemo, useState } from "react";
import { ARTIFACT_CSP, ARTIFACT_SANDBOX, htmlSrcDoc, renderMarkdown } from "../safe";
import type { Artifact } from "../types";

interface Props {
  artifacts: Artifact[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onClose: () => void;
}

const CHECK_LABEL: Record<string, string> = {
  word_count_in_range: "Length ≈ 1,250 words", has_headline: "Headline", has_3plus_sections: "3+ sections", has_bullets: "Bullets",
  has_bold_emphasis: "Bold emphasis", short_hook: "Short hook", has_takeaway_section: "Takeaway", grounded_citations: "Cited sources", no_wall_of_text: "No walls of text",
};

function slug(s: string) { return s.replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "artifact"; }

export function ArtifactViewer({ artifacts, activeId, onSelect, onClose }: Props) {
  const art = artifacts.find((a) => a.id === activeId) ?? null;
  const [tab, setTab] = useState<"preview" | "code">("preview");
  const [showPolicy, setShowPolicy] = useState(false);
  const [copied, setCopied] = useState(false);

  const mdHtml = useMemo(() => (art && art.kind === "markdown" ? renderMarkdown(art.content) : ""), [art?.id, art?.content, art?.kind]); // eslint-disable-line react-hooks/exhaustive-deps
  const srcDoc = useMemo(() => (art && art.kind === "html" && !art.streaming ? htmlSrcDoc(art.content) : ""), [art?.id, art?.content, art?.kind, art?.streaming]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!art) {
    return (
      <section className="viewer empty" aria-label="Artifact viewer">
        <div className="viewer-empty">
          <h2>Artifact viewer</h2>
          <p className="muted">Ask for a Markdown document, an HTML/CSS page, or a Ship 30 essay. It will render here, next to the chat.</p>
        </div>
      </section>
    );
  }

  const copy = async () => {
    try { await navigator.clipboard.writeText(art.content); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard blocked */ }
  };
  const download = () => {
    const isHtml = art.kind === "html";
    const blob = new Blob([isHtml ? htmlSrcDoc(art.content) : art.content], { type: isHtml ? "text/html" : "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${slug(art.title)}.${isHtml ? "html" : "md"}`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  const removed = art.report?.sanitizer?.removed as Record<string, number> | undefined;
  const lint = art.report?.skill_lint as { words: number; checks: Record<string, boolean>; passed: boolean } | undefined;
  const effectiveTab = art.streaming && art.kind === "html" ? "code" : tab;

  return (
    <section className="viewer" aria-label="Artifact viewer">
      <header className="viewer-head">
        <button className="btn small back" onClick={onClose} aria-label="Back to chat">← Chat</button>
        <div className="viewer-title">
          <span className="artifact-kind">{art.kind === "html" ? "HTML" : "MD"}</span>
          <h2 title={art.title}>{art.title}</h2>
        </div>
        {artifacts.length > 1 && (
          <select aria-label="Switch artifact" value={art.id} onChange={(e) => onSelect(e.target.value)}>
            {artifacts.map((a, i) => <option key={a.id} value={a.id}>{i + 1}. {a.title}</option>)}
          </select>
        )}
      </header>
      <div className="viewer-bar">
        <div className="tabs" role="tablist" aria-label="View">
          {(["preview", "code"] as const).map((t) => (
            <button key={t} role="tab" aria-selected={effectiveTab === t} className={effectiveTab === t ? "tab on" : "tab"} onClick={() => setTab(t)} disabled={art.streaming && art.kind === "html" && t === "preview"}>
              {t === "preview" ? "Preview" : art.kind === "html" ? "HTML" : "Markdown"}
            </button>
          ))}
        </div>
        <div className="viewer-actions">
          {art.streaming && <span className="status-line small" role="status">Writing…</span>}
          <button className="btn small" onClick={copy} disabled={art.streaming}>{copied ? "Copied" : "Copy"}</button>
          <button className="btn small" onClick={download} disabled={art.streaming}>Download</button>
          {art.kind === "html" && (
            <button className="btn small" aria-expanded={showPolicy} onClick={() => setShowPolicy((v) => !v)}>Security</button>
          )}
        </div>
      </div>

      {showPolicy && art.kind === "html" && (
        <div className="policy" role="region" aria-label="Sandbox policy">
          <p><strong>This page is untrusted and runs in a locked-down sandbox.</strong></p>
          <div className="policy-cols">
            <div>
              <h3>Allowed</h3>
              <ul><li>HTML &amp; inline CSS</li><li>Inline SVG shapes</li><li>Images embedded as <code>data:</code></li><li>In-page <code>#anchors</code></li></ul>
            </div>
            <div>
              <h3>Blocked</h3>
              <ul><li>JavaScript &amp; event handlers</li><li>Network requests, remote images/fonts/CSS</li><li>Forms, popups, top-level navigation</li><li>Access to this app's cookies/storage</li></ul>
            </div>
          </div>
          <p className="small muted">Enforced by <code>iframe sandbox="{ARTIFACT_SANDBOX}"</code> (no flags), a CSP of <code>{ARTIFACT_CSP}</code>, plus server-side allow-list sanitisation.
            {removed && Object.keys(removed).length > 0
              ? <> Removed from this artifact: {Object.entries(removed).map(([k, v]) => `${v}× ${k.replace(/_/g, " ")}`).join(", ")}.</>
              : <> Nothing needed removing from this artifact.</>}</p>
        </div>
      )}

      {lint && (
        <details className="lint">
          <summary>
            Ship 30 skill report · {lint.words.toLocaleString()} words · {Object.values(lint.checks).filter(Boolean).length}/{Object.keys(lint.checks).length} checks
          </summary>
          <ul className="check-list">
            {Object.entries(lint.checks).map(([k, ok]) => (
              <li key={k} className={ok ? "ok" : "fail"}><span aria-hidden="true">{ok ? "✓" : "✗"}</span> {CHECK_LABEL[k] ?? k}<span className="sr-only">{ok ? " passed" : " failed"}</span></li>
            ))}
          </ul>
        </details>
      )}

      <div className="viewer-body">
        {effectiveTab === "code" ? (
          <pre className="code" tabIndex={0} aria-label="Source"><code>{art.content}</code></pre>
        ) : art.kind === "html" ? (
          // Untrusted HTML: sandbox="" => unique opaque origin, no scripts, no forms, no popups, no top navigation.
          <iframe title={`Preview of ${art.title}`} className="frame" sandbox={ARTIFACT_SANDBOX} srcDoc={srcDoc} referrerPolicy="no-referrer" />
        ) : (
          // eslint-disable-next-line react/no-danger -- sanitised with DOMPurify (see safe.ts)
          <article className="prose doc" dangerouslySetInnerHTML={{ __html: mdHtml }} />
        )}
      </div>
    </section>
  );
}
