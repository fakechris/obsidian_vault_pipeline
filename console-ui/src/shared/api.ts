import type { GraphData, ClaimDetail, FlowData, SearchResult } from './types';

const BASE = '';

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`API ${path}: ${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export function fetchGraph(): Promise<GraphData> {
  return fetchJson<GraphData>('/api/graph');
}

export function fetchClaim(id: string): Promise<ClaimDetail> {
  return fetchJson<ClaimDetail>(`/api/claim/${encodeURIComponent(id)}`);
}

export function fetchFlow(): Promise<FlowData> {
  return fetchJson<FlowData>('/api/flow');
}

export function fetchSearch(query: string): Promise<SearchResult[]> {
  return fetchJson<SearchResult[]>(`/api/find?q=${encodeURIComponent(query)}`);
}
