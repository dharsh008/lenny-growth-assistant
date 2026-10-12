import { useEffect, useRef, useState } from "react";
import type { Mode } from "../types";

interface Props {
  busy: boolean;
  onSend: (text: string, mode: Mode) => void;
  onStop: () => void;
  seed?: string | null;
  seedMode?: Mode | null;
}

const MODES: { id: Mode; label: string; hint: string }[] = [
  { id: "auto", label: "Auto", hint: "Routes your message automatically" },
  { id: "qa", label: "Ask", hint: "Grounded Q&A with citations" },
  { id: "essay", label: "Ship 30 essay", hint: "~1,250-word essay via the Ship 30 for 30 skill" },
  { id: "artifact_md", label: "Markdown doc", hint: "Create a Markdown artifact" },
  { id: "artifact_html", label: "HTML page", hint: "Create an HTML/CSS artifact" },
];

export function Composer({ busy, onSend, onStop, seed, seedMode }: Props) {
  const [text, setText] = useState("");
  const [mode, setMode] = useState<Mode>("auto");
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (seed) {
      setText(seed);
      if (seedMode) setMode(seedMode);
      ref.current?.focus();
    }
  }, [seed, seedMode]);

  useEffect(() => {
    const el = ref.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
    }
  }, [text]);

  const submit = () => {
    const t = text.trim();
    if (!t || busy) return;
    onSend(t, mode);
    setText("");
    setMode("auto");
  };

  return (
    <form className="composer" onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="modes" role="radiogroup" aria-label="Output type">
        {MODES.map((m) => (
          <button type="button" key={m.id} role="radio" aria-checked={mode === m.id} title={m.hint}
            className={mode === m.id ? "chip on" : "chip"} onClick={() => setMode(m.id)}>
            {m.label}
          </button>
        ))}
      </div>
      <div className="input-row">
        <label htmlFor="msg" className="sr-only">Message</label>
        <textarea
          id="msg" ref={ref} rows={1} value={text} maxLength={4000} placeholder="Ask about product, growth, pricing, careers…"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); } }}
        />
        {busy ? (
          <button type="button" className="btn" onClick={onStop}>Stop</button>
        ) : (
          <button type="submit" className="btn primary" disabled={!text.trim()}>Send</button>
        )}
      </div>
      <div className="hint small muted">Enter to send · Shift+Enter for a new line. Answers come only from the transcripts.</div>
    </form>
  );
}
