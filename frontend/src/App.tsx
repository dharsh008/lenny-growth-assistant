import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, streamMessage } from "./api";
import { ArtifactViewer } from "./components/ArtifactViewer";
import { Composer } from "./components/Composer";
import { MessageView } from "./components/MessageView";
import { ProviderSwitch } from "./components/ProviderSwitch";
import { Sidebar } from "./components/Sidebar";
import type { Artifact, Message, Mode, ProviderInfo, ProviderName, Session, StreamEvent } from "./types";

const MAKE: { mode: Mode; name: string; cls: string; prompt: string; art: JSX.Element }[] = [
  { mode: "qa", name: "Ask", cls: "mk-ask", prompt: "What do guests say about ",
    art: <span className="art"><i className="b l" /><i className="b r" /><i className="b l s" /></span> },
  { mode: "essay", name: "Ship 30 essay", cls: "mk-essay", prompt: "Write a Ship 30 for 30 essay on ",
    art: <span className="art doc"><i className="h" /><i className="ln" /><i className="ln" /><i className="ln s" /><i className="ln" /></span> },
  { mode: "artifact_md", name: "Markdown doc", cls: "mk-md", prompt: "Create a Markdown doc summarizing ",
    art: <span className="art doc"><i className="hash">#</i><i className="ln" /><i className="ln s" /><i className="ln" /><i className="ln s" /></span> },
  { mode: "artifact_html", name: "HTML page", cls: "mk-html", prompt: "Create an HTML one-pager about ",
    art: <span className="art win"><i className="dots" /><i className="blk" /><i className="ln" /><i className="ln s" /></span> },
];

const SUGGESTIONS = [
  "How do top teams find product-market fit?",
  "What do guests say about running a pre-mortem?",
  "Write a Ship 30 for 30 essay on activation and retention",
  "Create an HTML one-pager of the pricing frameworks guests recommend",
];

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [activeArtifact, setActiveArtifact] = useState<string | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [provider, setProvider] = useState<ProviderName>("ollama");
  const [stats, setStats] = useState<{ episodes: number; chunks: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [navOpen, setNavOpen] = useState(false);
  const [mobileView, setMobileView] = useState<"chat" | "artifact">("chat");
  const [seed, setSeed] = useState<string | null>(null);
  const [seedMode, setSeedMode] = useState<Mode | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const lastSent = useRef<{ text: string; mode: Mode } | null>(null);

  const refreshSessions = useCallback(async () => {
    try { setSessions(await api.listSessions()); } catch (e) { setBanner(msgOf(e)); }
  }, []);
  const refreshProviders = useCallback(async (force = false) => {
    try {
      const p = await api.providers(force);
      setProviders(p);
    } catch (e) { setBanner(msgOf(e)); }
  }, []);

  useEffect(() => {
    refreshSessions();
    refreshProviders();
    api.stats().then(setStats).catch(() => undefined);
    // Poll readiness while the knowledge base is still loading on first start
    const t = setInterval(() => api.stats().then((s) => { setStats(s); }).catch(() => undefined), 8000);
    return () => clearInterval(t);
  }, [refreshSessions, refreshProviders]);

  useEffect(() => {
    if (providers.length && !activeId) {
      const def = providers.find((p) => p.is_default) ?? providers[0];
      setProvider(def.name);
    }
  }, [providers, activeId]);

  // Keep the chat pinned to the newest text while it streams, but never move the page itself.
  useEffect(() => {
    const el = threadRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const openSession = useCallback(async (id: string) => {
    try {
      const d = await api.getSession(id);
      setActiveId(id);
      setMessages(d.messages.map((m) => ({ ...m, error: m.route === "error" ? { code: m.meta?.error?.code ?? "error", message: m.content } : null })));
      setArtifacts(d.artifacts);
      setActiveArtifact(d.artifacts.length ? d.artifacts[d.artifacts.length - 1].id : null);
      setProvider(d.provider);
      setMobileView("chat");
      setNavOpen(false);
      setBanner(null);
    } catch (e) { setBanner(msgOf(e)); }
  }, []);

  const newChat = () => {
    abortRef.current?.abort();
    setActiveId(null); setMessages([]); setArtifacts([]); setActiveArtifact(null); setBusy(false);
    setMobileView("chat"); setNavOpen(false); setBanner(null); stick.current = true;
  };

  const deleteSession = async (id: string) => {
    if (!window.confirm("Delete this conversation and its artifacts?")) return;
    try {
      await api.deleteSession(id);
      if (id === activeId) newChat();
      refreshSessions();
    } catch (e) { setBanner(msgOf(e)); }
  };

  const changeProvider = async (p: ProviderName) => {
    setProvider(p);
    if (activeId) {
      try { await api.patchSession(activeId, { provider: p }); refreshSessions(); } catch (e) { setBanner(msgOf(e)); }
    }
  };

  const send = async (text: string, mode: Mode) => {
    setBanner(null);
    lastSent.current = { text, mode };
    let sid = activeId;
    try {
      if (!sid) {
        const s = await api.createSession(provider);
        sid = s.id;
        setActiveId(sid);
      }
    } catch (e) { setBanner(msgOf(e)); return; }

    const tempUser: Message = { id: `u-${Date.now()}`, role: "user", content: text, sources: [], artifact_ids: [] };
    const pendingId = `pending-${Date.now()}`;
    const pending: Message = { id: pendingId, role: "assistant", content: "", sources: [], artifact_ids: [], pending: true, status: "Starting…" };
    setMessages((m) => [...m.filter((x) => !x.error), tempUser, pending]);
    setBusy(true);
    const ac = new AbortController();
    abortRef.current = ac;

    const patch = (fn: (m: Message) => Message) => setMessages((ms) => ms.map((m) => (m.id === pendingId ? fn(m) : m)));
    let liveArtifactId: string | null = null;

    const onEvent = (e: StreamEvent) => {
      switch (e.type) {
        case "route": patch((m) => ({ ...m, route: e.route })); break;
        case "status": patch((m) => ({ ...m, status: e.label })); break;
        case "retrieval": patch((m) => ({ ...m, meta: { ...(m.meta ?? {}), retrieval: e } })); break;
        case "provider": patch((m) => ({ ...m, provider: e.provider, model: e.model, meta: { ...(m.meta ?? {}), fallback_note: e.fallback_note } })); break;
        case "sources": patch((m) => ({ ...m, sources: e.sources })); break;
        case "delta": patch((m) => ({ ...m, content: m.content + e.text, status: "" })); break;
        case "artifact_start": {
          liveArtifactId = `live-${Date.now()}`;
          const a: Artifact = { id: liveArtifactId, kind: e.artifact_type, title: e.title, content: "", sanitized: false, report: {}, streaming: true };
          setArtifacts((as) => [...as, a]);
          setActiveArtifact(a.id);
          setMobileView("artifact");
          break;
        }
        case "artifact_delta":
          setArtifacts((as) => as.map((a) => (a.id === liveArtifactId ? { ...a, content: a.content + e.text } : a)));
          break;
        case "artifact": {
          const final: Artifact = e.artifact;
          setArtifacts((as) => (liveArtifactId && as.some((a) => a.id === liveArtifactId) ? as.map((a) => (a.id === liveArtifactId ? final : a)) : [...as, final]));
          setActiveArtifact(final.id);
          setMobileView("artifact");
          liveArtifactId = null;
          break;
        }
        case "done": setMessages((ms) => ms.map((m) => (m.id === pendingId ? { ...e.message, error: null } : m))); break;
        case "error":
          if (liveArtifactId) setArtifacts((as) => as.filter((a) => a.id !== liveArtifactId));
          setMessages((ms) => ms.map((m) => (m.id === pendingId ? { ...m, pending: false, content: e.message, route: "error", error: { code: e.code, message: e.message } } : m)));
          break;
      }
    };

    try {
      await streamMessage(sid, { content: text, mode, provider }, onEvent, ac.signal);
    } catch (e) {
      const err = e as ApiError;
      setMessages((ms) => ms.map((m) => (m.id === pendingId ? { ...m, pending: false, content: err.message, route: "error", error: { code: err.code ?? "error", message: err.message } } : m)));
    } finally {
      setBusy(false);
      refreshSessions();
      refreshProviders();
    }
  };

  const stop = () => { abortRef.current?.abort(); setBusy(false); setMessages((ms) => ms.map((m) => (m.pending ? { ...m, pending: false, content: m.content || "Stopped. (The server finishes and saves the turn; reopen the chat to see it.)" } : m))); };
  const retry = () => { if (lastSent.current) send(lastSent.current.text, lastSent.current.mode); };

  const current = providers.find((p) => p.name === provider);
  const showViewer = artifacts.length > 0;

  return (
    <div className={`app view-${mobileView} ${showViewer ? "has-viewer" : "no-viewer"}`}>
      <a href="#msg" className="skip">Skip to message box</a>
      <Sidebar sessions={sessions} activeId={activeId} onSelect={openSession} onNew={newChat} onDelete={deleteSession} stats={stats} open={navOpen} onClose={() => setNavOpen(false)} onCreate={(m, pr) => { newChat(); setSeedMode(m); setSeed(pr); }} />
      <main className="chat" aria-label="Chat">
        <header className="topbar">
          <button className="icon-btn menu" aria-label="Open conversations" onClick={() => setNavOpen(true)}>
            <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><path d="M2 5h14M2 9h14M2 13h14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
          </button>
          <ProviderSwitch providers={providers} value={provider} onChange={changeProvider} onRefresh={() => refreshProviders(true)} disabled={busy} />
          {showViewer && <button className="btn small to-viewer" onClick={() => setMobileView("artifact")}>Artifact</button>}
        </header>

        {current && !current.available && (
          <div className="banner warn" role="alert">
            <strong>{current.kind === "local" ? "Local model isn't reachable." : "Cloud model isn't configured."}</strong> {current.detail}
          </div>
        )}
        {banner && <div className="banner err" role="alert">{banner}<button className="link-btn" onClick={() => setBanner(null)}>Dismiss</button></div>}

        <div className="thread" ref={threadRef} role="log" aria-live="polite" aria-relevant="additions"
          onScroll={(e) => { const t = e.currentTarget; stick.current = t.scrollHeight - t.scrollTop - t.clientHeight < 80; }}>
          {messages.length === 0 ? (
            <div className="empty">
              <h1>Ask what Lenny's guests actually said.</h1>
              <p className="muted">Grounded answers with episode and timestamp citations, Ship 30 for 30 essays, and Markdown/HTML artifacts rendered beside the chat.</p>
              <div className="make-label">Make something new</div>
              <div className="make-grid">
                {MAKE.map((c) => (
                  <button key={c.mode} className="make-card" onClick={() => { setSeedMode(c.mode); setSeed(c.prompt); }}>
                    <span className={`make-thumb ${c.cls}`} aria-hidden="true">{c.art}</span>
                    <span className="make-name">{c.name}<em>New</em></span>
                  </button>
                ))}
              </div>
              <div className="make-label">Or try a question</div>
              <ul className="suggestions">
                {SUGGESTIONS.map((s) => (<li key={s}><button className="suggest" onClick={() => setSeed(s + " ")}>{s}</button></li>))}
              </ul>
            </div>
          ) : (
            messages.map((m, i) => (
              <MessageView key={m.id} msg={m} artifacts={artifacts} activeArtifactId={activeArtifact}
                onOpenArtifact={(id) => { setActiveArtifact(id); setMobileView("artifact"); }}
                onRetry={m.error && i === messages.length - 1 ? retry : undefined} />
            ))
          )}
          <div ref={endRef} />
        </div>
        <Composer busy={busy} onSend={(t, m) => { setSeed(null); setSeedMode(null); send(t, m); }} onStop={stop} seed={seed} seedMode={seedMode} />
      </main>
      {showViewer && <ArtifactViewer artifacts={artifacts} activeId={activeArtifact} onSelect={setActiveArtifact} onClose={() => setMobileView("chat")} />}
    </div>
  );
}

function msgOf(e: unknown): string {
  return e instanceof ApiError ? e.message : (e as Error)?.message ?? "Unexpected error";
}
