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

/** Static-publish mode: the SPA reads snapshotted `<base>/api/*.json` files
 * (produced by `ovp2 publish`) instead of a live server. Set at build time via
 * `VITE_OVP_STATIC=1`; the live build is unaffected. `BASE_URL` is Vite's
 * `--base` so the API path is correct under a sub-path host (GitHub Pages). */
export const STATIC_MODE = import.meta.env.VITE_OVP_STATIC === '1';
const API = STATIC_MODE
  ? `${import.meta.env.BASE_URL}api`.replace(/\/\/+api$/, '/api')
  : '/api';

/** The terrain projection URL — a raw static file in publish mode, the live
 * endpoint otherwise. KnowledgeTerrain fetches this directly (its own
 * not-built error UX), so it needs the base-aware URL. */
export const terrainUrl = STATIC_MODE ? `${API}/terrain.json` : '/api/terrain';

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) {
    throw new Error(`API ${path}: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

/** An empty graph — the shape the KnowledgeGraph component tolerates when a
 * scope isn't pre-baked in static mode (neighborhood/search subgraphs). */
function emptyGraph(): GraphResponse {
  return { nodes: [], edges: [], communities: [], truncated: false } as unknown as GraphResponse;
}

/** The full display-hit list (empty-term query), cached for client-side search
 * filtering in static mode. */
let searchIndexCache: Promise<FindHit[]> | null = null;
function searchIndex(): Promise<FindHit[]> {
  if (!searchIndexCache) searchIndexCache = fetchJson<FindHit[]>(`${API}/search-index.json`);
  return searchIndexCache;
}
async function filterHits(term: string): Promise<FindHit[]> {
  const needle = term.trim().toLowerCase();
  const all = await searchIndex();
  if (!needle) return all;
  // Match the display line AND the non-display fields the live run_query also
  // searches (case-id path, entity id), so static search doesn't silently miss
  // hits the server would return.
  return all.filter((h) =>
    `${h.line} ${h.path ?? ''} ${h.id ?? ''}`.toLowerCase().includes(needle),
  );
}

export interface GraphQuery {
  mode?: 'overview' | 'neighborhood';
  limit?: number;
  theme?: string;
  focus?: string;
  hops?: number;
}

export function fetchGraph(query: GraphQuery = {}): Promise<GraphResponse> {
  if (STATIC_MODE) return fetchGlobalGraph(query.limit);
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
  if (STATIC_MODE) return fetchJson<ClaimDetail>(`${API}/claim/${encodeURIComponent(id)}.json`);
  return fetchJson<ClaimDetail>(`/api/claim/${encodeURIComponent(id)}`);
}

export function fetchFlow(): Promise<FlowData> {
  return fetchJson<FlowData>(STATIC_MODE ? `${API}/flow.json` : '/api/flow');
}

export function fetchFind(term: string): Promise<FindHit[]> {
  if (STATIC_MODE) return filterHits(term);
  return fetchJson<FindHit[]>(`/api/find?term=${encodeURIComponent(term)}`);
}

/** Hit-flagged claim subgraph for the tight search layout. Not pre-baked in
 * static mode (unbounded query space) — degrades to an empty graph; the text
 * hits still render. */
export function fetchSearchSubgraph(q: string): Promise<GraphResponse> {
  if (STATIC_MODE) return Promise.resolve(emptyGraph());
  return fetchJson<GraphResponse>(
    `/api/search?q=${encodeURIComponent(q)}&subgraph=1`,
  );
}

export function fetchThemes(): Promise<ThemeCount[]> {
  return fetchJson<ThemeCount[]>(STATIC_MODE ? `${API}/themes.json` : '/api/themes');
}

export function fetchModel(): Promise<IndexModel> {
  return fetchJson<IndexModel>(STATIC_MODE ? `${API}/model.json` : '/api/model');
}

/** Read-only server/vault configuration for the System page (B5 v1). */
export function fetchSettings(): Promise<SettingsPayload> {
  return fetchJson<SettingsPayload>(STATIC_MODE ? `${API}/settings.json` : '/api/settings');
}

/** Three-layer source detail: meta + memory + citing claims + raw md. */
export function fetchSourceDetail(sha: string): Promise<SourceDetail> {
  if (STATIC_MODE) return fetchJson<SourceDetail>(`${API}/source/${encodeURIComponent(sha)}.json`);
  return fetchJson<SourceDetail>(`/api/source/${encodeURIComponent(sha)}`);
}

/** Source-centric neighborhood for the KnowledgeGraph component (design §4):
 * this source → claims citing it → sibling sources. Not pre-baked in static
 * mode — the source page falls back to its citing-claims list. */
export function fetchSourceNeighborhood(sha: string): Promise<GraphResponse> {
  if (STATIC_MODE) return Promise.resolve(emptyGraph());
  return fetchJson<GraphResponse>(
    `/api/graph?scope=neighborhood&source=${encodeURIComponent(sha)}`,
  );
}

/** Global scope for the KnowledgeGraph component: the overview/density graph
 * (claims + community metadata). Capped so the embedded view stays snappy —
 * the response flags truncation. */
export function fetchGlobalGraph(limit = 400): Promise<GraphResponse> {
  if (STATIC_MODE) return fetchJson<GraphResponse>(`${API}/graph/global.json`);
  return fetchJson<GraphResponse>(`/api/graph?scope=global&limit=${limit}`);
}

/** Theme scope for the KnowledgeGraph component: the theme's claims + the
 * sources they cite. 404s on an unknown theme. In static mode the per-theme
 * subgraphs are one keyed file, looked up client-side. */
export async function fetchThemeGraph(theme: string): Promise<GraphResponse> {
  if (STATIC_MODE) {
    const all = await fetchJson<Record<string, GraphResponse>>(`${API}/graph/themes.json`);
    return all[theme] ?? emptyGraph();
  }
  return fetchJson<GraphResponse>(
    `/api/graph?scope=theme&theme=${encodeURIComponent(theme)}`,
  );
}

/** Text search over sources / packs / claims / runs — display lines with
 * stable ids (FindHit.id) for entity links. */
export function fetchSearchHits(q: string): Promise<FindHit[]> {
  if (STATIC_MODE) return filterHits(q);
  return fetchJson<FindHit[]>(`/api/search?q=${encodeURIComponent(q)}`);
}

/** Non-2xx /api/ask outcome with the HTTP status and the server's stable
 * machine-readable `code` kept — the Ask page maps 503 llm_not_configured /
 * 503 index_unavailable / 429 ask_busy / 504 ask_timeout to specific
 * guidance. */
export class AskError extends Error {
  status: number;
  code: string | null;

  constructor(status: number, message: string, code: string | null) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

/** POST /api/ask — cited answer over the grounded evidence index. The
 * server saves the transcript to `.ovp/chats/` as a side effect. */
export async function postAsk(question: string): Promise<AskResponse> {
  if (STATIC_MODE) {
    throw new AskError(501, 'Ask is not available on the published site.', 'static_site');
  }
  const res = await fetch('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    let code: string | null = null;
    try {
      const data = (await res.json()) as { error?: unknown; code?: unknown };
      if (data && typeof data.error === 'string') message = data.error;
      if (data && typeof data.code === 'string') code = data.code;
    } catch {
      /* non-JSON error body — keep the status line */
    }
    throw new AskError(res.status, message, code);
  }
  return res.json() as Promise<AskResponse>;
}

/** Saved ask transcripts, newest first. Empty on the published site. */
export function fetchChats(): Promise<ChatEntry[]> {
  if (STATIC_MODE) return Promise.resolve([]);
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
