export type ProviderName = "ollama" | "anthropic";
export type Mode = "auto" | "qa" | "essay" | "artifact_md" | "artifact_html";

export interface Source {
  n?: number;
  guest: string;
  title: string;
  url?: string | null;
  timestamp?: string | null;
  quote?: string | null;
  cited?: boolean;
  chunk_id?: number;
}

export interface Message {
  id: string;
  seq?: number;
  role: "user" | "assistant";
  content: string;
  route?: string | null;
  sources: Source[];
  artifact_ids: string[];
  provider?: string | null;
  model?: string | null;
  latency_ms?: number | null;
  meta?: Record<string, any>;
  created_at?: string;
  // client-only streaming state
  pending?: boolean;
  status?: string;
  error?: { code: string; message: string } | null;
}

export interface Artifact {
  id: string;
  kind: "markdown" | "html";
  title: string;
  content: string;
  sanitized: boolean;
  report: Record<string, any>;
  skill?: string | null;
  message_id?: string | null;
  created_at?: string;
  streaming?: boolean;
}

export interface Session {
  id: string;
  title: string;
  provider: ProviderName;
  model?: string | null;
  created_at: string;
  updated_at: string;
}

export interface SessionDetail extends Session {
  messages: Message[];
  artifacts: Artifact[];
}

export interface ProviderInfo {
  name: ProviderName;
  kind: "local" | "cloud";
  model: string;
  configured: boolean;
  available: boolean;
  detail: string;
  is_default: boolean;
}

export interface StreamEvent {
  type: string;
  [k: string]: any;
}
