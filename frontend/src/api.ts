// API base: web dev (vite proxy or CORS) and Electron share the same backend.
// In Electron the backend runs on localhost:8000 as well.
export const API_BASE =
  (import.meta as any).env?.VITE_API_BASE ?? 'http://127.0.0.1:8000';
export const WS_BASE = API_BASE.replace('http', 'ws') + '/ws/market';

export async function apiGet<T>(path: string): Promise<T> {
  const r = await fetch(API_BASE + path);
  if (!r.ok) throw new Error('GET ' + path + ' -> ' + r.status);
  return r.json() as Promise<T>;
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(API_BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error('POST ' + path + ' -> ' + r.status);
  return data as T;
}

export interface Tick {
  symbol: string;
  bid: number | null;
  ask: number | null;
  mid: number;
  source_ts: string | null;
  recv_ts: string;
  provider: string;
  stale?: boolean;
}

export interface AgentStatus {
  key: string;
  name: string;
  skill: string;
  skill_ref: string;
  mission: string;
  deterministic: boolean;
  model: string | null;
  state: string;
  runs: number;
  last_result: any;
}

export interface FeedState {
  provider: string;
  state: string;
  symbols: string[];
  reconnects: number;
  events_received: number;
  stale_symbols: string[];
}
