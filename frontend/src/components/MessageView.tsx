import { useMemo, useState } from "react";
import { renderChat } from "../safe";
import type { Artifact, Message } from "../types";
import { SourcesList } from "./SourcesList";

interface Props {
  msg: Message;
  artifacts: Artifact[];
  activeArtifactId: string | null;
  onOpenArtifact: (id: string) => void;
  onRetry?: () => void;
}

const ROUTE_LABEL: Record<string, string> = { qa: "Grounded answer", essay: "Ship 30 essay", artifact: "Artifact", refusal: "Not in the transcripts", chitchat: "", error: "" };

export function MessageView({ msg, artifacts, activeArtifactId, onOpenArtifact, onRetry }: Props) {
  const [hl, setHl] = useState<number | null>(null);
  const nums = useMemo(() => new Set((msg.sources ?? []).map((s) => s.n).filter((n): n is number => typeof n === "number")), [msg.sources]);
  const html = useMemo(() => (msg.role === "assistant" ? renderChat(msg.content, nums) : ""), [msg.content, msg.role, nums]);

  if (msg.role === "user") {
    return (
      <div className="msg user">
        <div className="bubble user-bubble">{msg.content}</div>
      </div>
    );
  }
  const attached = artifacts.filter((a) => msg.artifact_ids?.includes(a.id) || (msg.pending && a.streaming));
  const lowConf = msg.meta?.retrieval?.strength === "weak";
  const route = msg.route ? ROUTE_LABEL[msg.route] : "";

  const onClick = (e: React.MouseEvent) => {
    const t = (e.target as HTMLElement).closest("button.cite") as HTMLElement | null;
    if (!t) return;
    const n = Number(t.dataset.n);
    setHl(n);
    requestAnimationFrame(() => document.getElementById(`${msg.id}-src-${n}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
  };

  return (
    <div className={`msg assistant ${msg.error || msg.route === "error" ? "is-error" : ""}`}>
      <div className="bubble">
        {(route || lowConf) && (
          <div className="meta-row">
            {route && <span className={`tag ${msg.route}`}>{route}</span>}
            {lowConf && msg.route === "qa" && <span className="tag warn" title="Only one episode matched strongly; treat with care">Limited evidence</span>}
          </div>
        )}
        {msg.pending && !msg.content && <div className="status-line">{msg.status ?? "Working…"}</div>}
        {/* eslint-disable-next-line react/no-danger -- sanitised in renderChat (marked -> DOMPurify) */}
        {msg.content && <div className="prose" onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />}
        {msg.pending && msg.content && <div className="status-line small">{msg.status}</div>}
        {attached.map((a) => (
          <button key={a.id} className={a.id === activeArtifactId ? "artifact-card on" : "artifact-card"} onClick={() => onOpenArtifact(a.id)}>
            <span className="artifact-kind">{a.kind === "html" ? "HTML" : "MD"}</span>
            <span className="artifact-name">{a.title}</span>
            <span className="artifact-open">{a.streaming ? "Writing…" : "Open"}</span>
          </button>
        ))}
        {msg.error && onRetry && (
          <button className="btn small" onClick={onRetry}>Retry</button>
        )}
        {!msg.pending && msg.sources?.length > 0 && <SourcesList sources={msg.sources} highlight={hl} idPrefix={msg.id} />}
        {!msg.pending && msg.provider && (
          <div className="foot small muted">
            {msg.provider === "ollama" ? "Local" : "Cloud"} · {msg.model}
            {msg.latency_ms ? ` · ${(msg.latency_ms / 1000).toFixed(1)}s` : ""}
            {msg.meta?.fallback_note ? ` · ${msg.meta.fallback_note}` : ""}
          </div>
        )}
      </div>
    </div>
  );
}
