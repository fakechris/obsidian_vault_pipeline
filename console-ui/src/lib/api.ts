import type {
  ClaimDetail,
  FindHit,
  FlowData,
  GraphResponse,
  IndexModel,
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
