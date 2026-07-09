import type {
  AskResponse,
  ChatEntry,
  ClaimDetail,
  FindHit,
  FlowData,
  GraphResponse,
  IndexModel,
  SettingsPayload,
  SourceDetail,
  ThemeCount,
} from './types';

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) {
    throw new Error(`API ${path}: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export interface GraphQuery {
  mode?: 'overview' | 'neighborhood';
  limit?: number;
  theme?: string;
  focus?: string;
  hops?: number;
}

export function fetchGraph(query: GraphQuery = {}): Promise<GraphResponse> {
  const params = new URLSearchParams();
  if (query.mode) params.set('mode', query.mode);
  if (query.limit != null) params.set('limit', String(query.limit));
  if (query.theme) params.set('theme', query.theme);
  if (query.focus) params.set('focus', query.focus);
  if (query.hops != null) params.set('hops', String(query.hops));
  const qs = params.toString();
  return fetchJson<GraphResponse>(`/api/graph${qs ? `?${qs}` : ''}`);
}

export function fetchClaim(id: string): Promise<ClaimDetail> {
  return fetchJson<ClaimDetail>(`/api/claim/${encodeURIComponent(id)}`);
}

export function fetchFlow(): Promise<FlowData> {
  return fetchJson<FlowData>('/api/flow');
}

export function fetchFind(term: string): Promise<FindHit[]> {
  return fetchJson<FindHit[]>(`/api/find?term=${encodeURIComponent(term)}`);
}

/** Hit-flagged claim subgraph for the tight search layout. */
export function fetchSearchSubgraph(q: string): Promise<GraphResponse> {
  return fetchJson<GraphResponse>(
    `/api/search?q=${encodeURIComponent(q)}&subgraph=1`,
  );
}

export function fetchThemes(): Promise<ThemeCount[]> {
  return fetchJson<ThemeCount[]>('/api/themes');
}

export function fetchModel(): Promise<IndexModel> {
  return fetchJson<IndexModel>('/api/model');
}

/** Read-only server/vault configuration for the System page (B5 v1). */
export function fetchSettings(): Promise<SettingsPayload> {
  return fetchJson<SettingsPayload>('/api/settings');
}

/** Three-layer source detail: meta + memory + citing claims + raw md. */
export function fetchSourceDetail(sha: string): Promise<SourceDetail> {
  return fetchJson<SourceDetail>(`/api/source/${encodeURIComponent(sha)}`);
}

/** Source-centric neighborhood for the KnowledgeGraph component (design §4):
 * this source → claims citing it → sibling sources. */
export function fetchSourceNeighborhood(sha: string): Promise<GraphResponse> {
  return fetchJson<GraphResponse>(
    `/api/graph?scope=neighborhood&source=${encodeURIComponent(sha)}`,
  );
}

/** Global scope for the KnowledgeGraph component: the overview/density graph
 * (claims + community metadata). Capped so the embedded view stays snappy —
 * the response flags truncation. */
export function fetchGlobalGraph(limit = 400): Promise<GraphResponse> {
  return fetchJson<GraphResponse>(`/api/graph?scope=global&limit=${limit}`);
}

/** Theme scope for the KnowledgeGraph component: the theme's claims + the
 * sources they cite. 404s on an unknown theme. */
export function fetchThemeGraph(theme: string): Promise<GraphResponse> {
  return fetchJson<GraphResponse>(
    `/api/graph?scope=theme&theme=${encodeURIComponent(theme)}`,
  );
}

/** Text search over sources / packs / claims / runs — display lines with
 * stable ids (FindHit.id) for entity links. */
export function fetchSearchHits(q: string): Promise<FindHit[]> {
  return fetchJson<FindHit[]>(`/api/search?q=${encodeURIComponent(q)}`);
}

/** Non-2xx /api/ask outcome with the HTTP status kept — the Ask page maps
 * 503 (llm not configured) and 504 (timeout) to specific guidance. */
export class AskError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** POST /api/ask — cited answer over the grounded evidence index. The
 * server saves the transcript to `.ovp/chats/` as a side effect. */
export async function postAsk(question: string): Promise<AskResponse> {
  const res = await fetch('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const data = (await res.json()) as { error?: unknown };
      if (data && typeof data.error === 'string') message = data.error;
    } catch {
      /* non-JSON error body — keep the status line */
    }
    throw new AskError(res.status, message);
  }
  return res.json() as Promise<AskResponse>;
}

/** Saved ask transcripts, newest first. */
export function fetchChats(): Promise<ChatEntry[]> {
  return fetchJson<ChatEntry[]>('/api/chats');
}

/** One saved transcript as raw markdown (rendered client-side). */
export async function fetchChatMarkdown(name: string): Promise<string> {
  const res = await fetch(`/api/chats/${encodeURIComponent(name)}`);
  if (!res.ok) {
    throw new Error(`API /api/chats/${name}: ${res.status} ${res.statusText}`);
  }
  return res.text();
}
