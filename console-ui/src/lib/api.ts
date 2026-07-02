import type {
  ClaimDetail,
  FlowData,
  GraphResponse,
  IndexModel,
  SearchResult,
} from './types';

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) {
    throw new Error(`API ${path}: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export interface GraphQuery {
  mode?: 'overview' | 'neighborhood' | 'full';
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

export function fetchFind(term: string): Promise<SearchResult[]> {
  return fetchJson<SearchResult[]>(
    `/api/find?term=${encodeURIComponent(term)}`,
  );
}

export function fetchModel(): Promise<IndexModel> {
  return fetchJson<IndexModel>('/api/model');
}
