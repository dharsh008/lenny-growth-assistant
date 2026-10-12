import type { Mode, ProviderInfo, ProviderName, Session, SessionDetail, StreamEvent } from "./types";

export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number, public details: Record<string, unknown> = {}) {
    super(message);
  }
}

function makeUserId(): string {
  const rnd = Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, "0")).join("");
  return `u_${rnd}`;
}

let memoryId: string | null = null;
export function userId(): string {
  try {
    let id = localStorage.getItem("lga.user");
    if (!id) {
      id = makeUserId();
      localStorage.setItem("lga.user", id);
    }
    return id;
  } catch {
    return (memoryId ??= makeUserId()); // storage blocked (private mode): fall back to per-tab identity
  }
}

const headers = () => ({ "Content-Type": "application/json", "X-User-Id": userId() });

async function parseError(res: Response): Promise<ApiError> {
  try {
    const body = await res.json();
    const e = body.error ?? {};
    return new ApiError(e.code ?? `http_${res.status}`, e.message ?? res.statusText, res.status, e.details ?? {});
  } catch {
    return new ApiError(`http_${res.status}`, res.statusText || "Request failed", res.status);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { ...init, headers: { ...headers(), ...(init.headers ?? {}) } });
  } catch {
    throw new ApiError("network_error", "Can't reach the server. Is the backend running?", 0);
  }
  if (!res.ok) throw await parseError(res);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  providers: (refresh = false) => request<ProviderInfo[]>(`/providers${refresh ? "?refresh=true" : ""}`),
  stats: () => request<{ episodes: number; chunks: number }>("/knowledge/stats"),
  ready: async () => {
    const res = await fetch("/api/health/ready").catch(() => null);
    if (!res) return { status: "unreachable" } as any;
    return res.json();
  },
  listSessions: () => request<Session[]>("/sessions"),
  createSession: (provider?: ProviderName) => request<Session>("/sessions", { method: "POST", body: JSON.stringify({ provider }) }),
  getSession: (id: string) => request<SessionDetail>(`/sessions/${id}`),
  patchSession: (id: string, body: { title?: string; provider?: ProviderName }) =>
    request<Session>(`/sessions/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteSession: (id: string) => request<void>(`/sessions/${id}`, { method: "DELETE" }),
};

/** Parse an SSE byte stream (fetch + ReadableStream, because EventSource cannot POST). */
export async function streamMessage(
  sessionId: string,
  body: { content: string; mode: Mode; provider?: ProviderName },
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`/api/sessions/${sessionId}/messages/stream`, {
      method: "POST", headers: headers(), body: JSON.stringify(body), signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    throw new ApiError("network_error", "Can't reach the server. Is the backend running?", 0);
  }
  if (!res.ok || !res.body) throw await parseError(res);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const parsed = parseFrame(frame);
      if (parsed) onEvent(parsed);
    }
  }
}

export function parseFrame(frame: string): StreamEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith(":")) continue; // comment / keep-alive
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return null;
  try {
    return { type: event, ...JSON.parse(data.join("\n")) };
  } catch {
    return null;
  }
}
