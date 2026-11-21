// Typed client for the Forge API. Same-origin only; all writes are JSON.

export type Json = Record<string, any>;

export interface ForgeEvent {
  run_id: string;
  seq: number;
  ts: string;
  type: string;
  stage: string | null;
  agent: string | null;
  data: Json;
  prev_hash: string;
  hash: string;
}

export interface RunSummary {
  id: string;
  title: string;
  scenario_id: string | null;
  target: string;
  mode: string;
  state: string;
  created_at: string;
  updated_at: string;
  running: boolean;
  waiting: boolean;
  terminal: boolean;
  requested_by: string;
  fix_iteration: number;
  cost_usd: number;
  llm_calls: number;
}

export interface Usage {
  calls: number;
  errors: number;
  cache_hits: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  latency_ms_total: number;
  providers: string[];
  by_agent: Record<string, { calls: number; errors: number; prompt_tokens: number; completion_tokens: number;
    cost_usd: number; p50_ms: number; p95_ms: number }>;
}

export interface RunDetail extends RunSummary {
  requirement: string;
  data: Json;
  usage: Usage;
  artifacts: { name: string; version: number; hash: string; created_at: string }[];
  approvals: Json[];
}

export interface Meta {
  scenarios: { id: string; target: string; title: string; requirement: string; scripted: boolean }[];
  targets: { name: string; description: string }[];
  modes: Record<string, boolean>;
  live_providers: string[];
  pipeline: string[];
  gates: string[];
  human_wait: string[];
  budgets: Json;
}

export interface RepoCheck {
  ok: boolean;
  name: string;
  path: string;
  python_files: number;
  source_dirs: string[];
  test_dirs: string[];
  has_forge_toml: boolean;
  tests: { ok: boolean; passed: number; failed: number; errors: number; total: number; missing_modules: string[] } | null;
  problems: string[];
  warnings: string[];
}

export interface SavedRepo {
  name: string;
  kind: "upload" | "local";
  path: string;
  added_at: string;
  ok: boolean;
  summary: string;
  available: boolean;
  check?: RepoCheck;
}

export interface ChaosState {
  providers_down: string[];
  corrupt_next: number;
  fail_tests_next: number;
  plant_vuln_next: number;
  repeat_next: number;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    headers: init?.body ? { "content-type": "application/json" } : undefined,
  });
  const text = await resp.text();
  const body = text ? (() => { try { return JSON.parse(text); } catch { return text; } })() : null;
  if (!resp.ok) {
    const detail = body && typeof body === "object" ? body.detail : body;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail) || resp.statusText);
  }
  return body as T;
}

const post = <T,>(path: string, body: unknown) => req<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  meta: () => req<Meta>("/api/meta"),
  runs: () => req<RunSummary[]>("/api/runs"),
  run: (id: string) => req<RunDetail>(`/api/runs/${id}`),
  createRun: (body: Json) => post<RunDetail>("/api/runs", body),
  artifact: (id: string, name: string) => req<{ content: Json; version: number; hash: string }>(
    `/api/runs/${id}/artifacts/${name}`),
  llmCalls: (id: string) => req<Json[]>(`/api/runs/${id}/llm_calls`),
  llmCall: (id: string, callId: number) => req<Json>(`/api/runs/${id}/llm_calls/${callId}`),
  verify: (id: string) => req<{ ok: boolean; first_bad_seq: number | null; events: number; head_hash: string }>(
    `/api/runs/${id}/verify`),
  decide: (id: string, body: Json) => post<RunDetail>(`/api/runs/${id}/decision`, body),
  abort: (id: string, body: Json) => post<RunDetail>(`/api/runs/${id}/abort`, body),
  chaos: () => req<ChaosState>("/api/chaos"),
  setChaos: (body: Json) => post<ChaosState>("/api/chaos", body),
  providers: () => req<Json>("/api/providers"),
  repos: () => req<SavedRepo[]>("/api/repos"),
  addLocalRepo: (path: string) => post<SavedRepo>("/api/repos/local", { path }),
  removeRepo: (name: string) => req<{ ok: boolean }>(`/api/repos/${encodeURIComponent(name)}`, { method: "DELETE" }),
  uploadRepo: async (file: File): Promise<SavedRepo> => {
    const resp = await fetch(`/api/repos/upload?filename=${encodeURIComponent(file.name)}`, {
      method: "POST", headers: { "content-type": "application/zip" }, body: file,
    });
    const body = await resp.json().catch(() => null);
    if (!resp.ok) throw new Error(body?.detail ?? resp.statusText);
    return body as SavedRepo;
  },
};

export function subscribe(runId: string, after: number, onEvent: (e: ForgeEvent) => void): () => void {
  const es = new EventSource(`/api/runs/${runId}/stream?after=${after}`);
  es.addEventListener("forge", (msg) => onEvent(JSON.parse((msg as MessageEvent).data)));
  return () => es.close();
}
