import type { ProviderInfo, ProviderName } from "../types";

interface Props {
  providers: ProviderInfo[];
  value: ProviderName;
  disabled?: boolean;
  onChange: (p: ProviderName) => void;
  onRefresh: () => void;
}

const LABEL: Record<ProviderName, string> = { ollama: "Local", anthropic: "Cloud" };

export function ProviderSwitch({ providers, value, disabled, onChange, onRefresh }: Props) {
  const current = providers.find((p) => p.name === value);
  return (
    <div className="provider" role="group" aria-label="Model provider">
      <div className="seg" role="radiogroup" aria-label="Choose model provider">
        {providers.map((p) => (
          <button
            key={p.name}
            role="radio"
            aria-checked={p.name === value}
            className={p.name === value ? "seg-btn on" : "seg-btn"}
            disabled={disabled}
            onClick={() => onChange(p.name)}
            title={p.detail}
          >
            <span className={`dot ${p.available ? "ok" : "bad"}`} aria-hidden="true" />
            {LABEL[p.name]}
            <span className="seg-model">{p.model}</span>
          </button>
        ))}
      </div>
      {current && (
        <button className="link-btn" onClick={onRefresh} title="Re-check provider status">
          {current.available ? "Ready" : "Unavailable"}
          <span className="sr-only">. {current.detail}. Click to re-check.</span>
        </button>
      )}
    </div>
  );
}
