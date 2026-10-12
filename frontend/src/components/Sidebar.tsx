import { useMemo, useState } from "react";
import type { Mode, Session } from "../types";

interface Props {
  sessions: Session[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  stats: { episodes: number; chunks: number } | null;
  open: boolean;
  onClose: () => void;
  onCreate: (mode: Mode, prompt: string) => void;
}

const I = (d: string) => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
);

export const CREATE: { mode: Mode; name: string; icon: JSX.Element; prompt: string }[] = [
  { mode: "qa", name: "Ask the podcast", icon: I("M21 12a8 8 0 0 1-11.5 7.2L4 20l1-4.5A8 8 0 1 1 21 12z"), prompt: "What do guests say about " },
  { mode: "essay", name: "Ship 30 essay", icon: I("M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"), prompt: "Write a Ship 30 for 30 essay on " },
  { mode: "artifact_md", name: "Markdown doc", icon: I("M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 13h6M9 17h6"), prompt: "Create a Markdown doc summarizing " },
  { mode: "artifact_html", name: "HTML page", icon: I("M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16"), prompt: "Create an HTML one-pager about " },
];

function group(d: string) {
  const t = new Date(d); const n = new Date();
  const days = Math.floor((new Date(n.getFullYear(), n.getMonth(), n.getDate()).getTime() - new Date(t.getFullYear(), t.getMonth(), t.getDate()).getTime()) / 864e5);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  return t.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function Sidebar({ sessions, activeId, onSelect, onNew, onDelete, stats, open, onClose, onCreate }: Props) {
  const [q, setQ] = useState("");
  const groups = useMemo(() => {
    const out: { label: string; items: Session[] }[] = [];
    sessions.filter((s) => s.title.toLowerCase().includes(q.toLowerCase())).forEach((s) => {
      const label = group(s.updated_at);
      const g = out.find((x) => x.label === label);
      g ? g.items.push(s) : out.push({ label, items: [s] });
    });
    return out;
  }, [sessions, q]);

  return (
    <>
      {open && <div className="scrim" onClick={onClose} aria-hidden="true" />}
      <nav className={open ? "sidebar open" : "sidebar"} aria-label="Conversations">
        <div className="brand">
          <span className="logo" aria-hidden="true">L</span>
          <div>
            <div className="brand-name">Growth Assistant</div>
            <div className="brand-sub">from Lenny's Podcast</div>
          </div>
        </div>
        <label className="search">
          {I("M21 21l-4.3-4.3M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14z")}
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search" aria-label="Search conversations" />
        </label>
        <div className="nav-actions">
          <button className="nav-item" onClick={() => { onNew(); onClose(); }}>
            <span className="nav-ico plus" aria-hidden="true">+</span><span className="nav-text">New chat</span>
          </button>
          {CREATE.map((c) => (
            <button key={c.mode} className="nav-item" onClick={() => { onCreate(c.mode, c.prompt); onClose(); }}>
              <span className="nav-ico" aria-hidden="true">{c.icon}</span><span className="nav-text">{c.name}</span>
            </button>
          ))}
        </div>
        <div className="session-scroll">
          {sessions.length === 0 && <div className="muted small pad">No conversations yet.</div>}
          {groups.map((g) => (
            <div key={g.label}>
              <div className="nav-label">{g.label}</div>
              <ul className="session-list">
                {g.items.map((s) => (
                  <li key={s.id} className={s.id === activeId ? "session active" : "session"}>
                    <button className="session-btn" onClick={() => onSelect(s.id)} aria-current={s.id === activeId ? "true" : undefined}>
                      <span className="session-title">{s.title}</span>
                    </button>
                    <button className="icon-btn" aria-label={`Delete conversation: ${s.title}`} onClick={() => onDelete(s.id)}>
                      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="kb-stats small muted">
          {stats ? `${stats.episodes} episodes · ${stats.chunks.toLocaleString()} passages indexed` : "Knowledge base loading…"}
        </div>
      </nav>
    </>
  );
}
