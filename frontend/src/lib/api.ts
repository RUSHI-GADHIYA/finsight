import type { Metrics, ReviewAction, RunStatus, SavedReport } from "./types";
import { streamSSE, type SSEEvent } from "./sse";

// The browser calls FastAPI directly (CORS), so SSE isn't buffered by a proxy.
// NEXT_PUBLIC_ values are inlined at build time; API_URL lets the server use an internal URL.
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const SERVER_API_URL = process.env.API_URL ?? API_URL;

async function getJSON<T>(path: string, base = API_URL): Promise<T | null> {
  const resp = await fetch(`${base}${path}`, { cache: "no-store" });
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`GET ${path} failed: ${resp.status}`);
  return (await resp.json()) as T;
}

export const startResearch = (
  question: string,
  onEvent: (e: SSEEvent) => void,
  signal?: AbortSignal,
  fresh = false,
) => streamSSE(`${API_URL}/research`, { question, fresh }, onEvent, signal);

export const resumeResearch = (
  threadId: string,
  action: ReviewAction,
  onEvent: (e: SSEEvent) => void,
  signal?: AbortSignal,
) =>
  streamSSE(`${API_URL}/research/${threadId}/resume`, action, onEvent, signal);

// Server components
export const getRunStatus = (threadId: string) =>
  getJSON<RunStatus>(
    `/research/${encodeURIComponent(threadId)}`,
    SERVER_API_URL,
  );
export const listReports = () =>
  getJSON<SavedReport[]>("/reports", SERVER_API_URL);
export const getReport = (id: string) =>
  getJSON<SavedReport>(`/reports/${encodeURIComponent(id)}`, SERVER_API_URL);
export const getMetrics = () => getJSON<Metrics>("/metrics", SERVER_API_URL);
