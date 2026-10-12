import type { Source } from "../types";

export function SourcesList({ sources, highlight, idPrefix }: { sources: Source[]; highlight: number | null; idPrefix: string }) {
  if (!sources.length) return null;
  const cited = sources.some((s) => s.cited !== false);
  return (
    <details className="sources" open={highlight !== null}>
      <summary>{cited ? `${sources.length} source${sources.length > 1 ? "s" : ""}` : "Consulted (not cited)"}</summary>
      <ol className="source-list">
        {sources.map((s, i) => (
          <li key={i} id={`${idPrefix}-src-${s.n ?? i + 1}`} className={highlight === s.n ? "source hl" : "source"} value={s.n ?? i + 1}>
            <div className="source-head">
              <span className="source-guest">{s.guest}</span>
              {s.url ? (
                <a href={s.url} target="_blank" rel="noopener noreferrer" className="source-link">
                  {s.timestamp ?? "open"} ↗<span className="sr-only"> (opens YouTube at this moment)</span>
                </a>
              ) : (
                <span className="muted small">{s.timestamp}</span>
              )}
            </div>
            <div className="source-title">{s.title}</div>
            {s.quote && <blockquote className="source-quote">{s.quote}</blockquote>}
          </li>
        ))}
      </ol>
    </details>
  );
}
