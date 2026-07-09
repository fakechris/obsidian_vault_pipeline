// Mirrors the JSON contracts of ovp-server (crates/ovp-server/src/graph.rs).

export type NodeType = 'claim' | 'unit' | 'source';
export type EdgeType = 'cites' | 'extracted_from' | 'related';
export type GraphMode = 'overview' | 'neighborhood' | 'search';

export interface GraphNode {
  id: string;
  type: NodeType;
  /** Search mode: node matched the query (vs 1-hop context). */
  hit?: boolean;
  label: string;
  theme?: string;
  strength?: string;
  url?: string;
  degree: number;
  cluster: number;
  /** 0..1 rank signal — drives node size and label LOD. */
  importance: number;
  /** Provenance score 0..1 (claims only). */
  provenance?: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: EdgeType;
  /** For `related` edges: number of shared sources (edge thickness). */
  weight?: number;
}

export interface Community {
  id: number;
  label: string;
  size: number;
  top_claims: string[];
}

export interface GraphResponse {
  mode: GraphMode;
  nodes: GraphNode[];
  edges: GraphEdge[];
  communities: Community[];
  total_nodes: number;
  truncated: boolean;
}

export interface CitationDetail {
  unit_id: string;
  unit_text: string;
  quote: string;
  resolved_line: number | null;
  case_id: string;
  source_title: string;
  source_url: string;
  source_sha256: string;
}

export interface ClaimDetail {
  claim_id: string;
  claim: string;
  theme: string;
  strength: string;
  citations: CitationDetail[];
}

export interface FlowLink {
  from: string;
  to: string;
  value: number;
  label: string;
}

export interface FlowData {
  stages: string[];
  flows: FlowLink[];
}

/** /api/find hit — a display line, not a structured record. */
export interface FindHit {
  kind: string;
  status: string;
  line: string;
  path?: string;
}

export interface ThemeCount {
  theme: string;
  count: number;
}

export type SourceStatus =
  | 'blocked'
  | 'failed'
  | 'queued'
  | 'needs_content'
  | 'unparseable'
  | 'processed'
  | 'duplicate';

export interface SourceRow {
  sha256: string;
  status: SourceStatus;
  title?: string;
  url?: string;
  rel_path?: string;
  date?: string;
  last_run_id?: string;
  pack_dir?: string;
  fail_count: number;
  last_reason?: string;
}

export interface PackRow {
  pack_dir: string;
  title: string;
  date?: string;
  units: number;
  cards: number;
  json_repaired: boolean;
  card_titles: string[];
  source_sha256?: string;
}

export type ClaimStatus = 'durable' | 'superseded' | 'retracted' | 'caveated';

export interface ClaimRow {
  claim_id: string;
  claim: string;
  theme?: string;
  status: ClaimStatus;
  sources: string[];
  strength?: string;
  run_id?: string;
  lane?: string;
}

export interface BlockedSource {
  sha256: string;
  title?: string;
  fail_count: number;
  last_reason?: string;
  last_attempt?: string;
}

export interface RunStats {
  window_days: number;
  total_runs: number;
  succeeded: number;
  failed: number;
  success_rate_pct: number;
  avg_processed_per_run: number;
}

export interface OpsState {
  blocked_sources: BlockedSource[];
  queue_depth: number;
  run_stats?: RunStats | null;
}

export interface RunRow {
  run_id: string;
  date: string;
  report_file: string;
  succeeded: number;
  failed: number;
  skipped: number;
  blocked: number;
  ingested: number;
  pinboard_new: number;
  lifecycle_warnings: number;
}

export interface Totals {
  sources: number;
  queued: number;
  processed: number;
  failed: number;
  blocked: number;
  needs_content: number;
  unparseable: number;
  duplicates: number;
  packs: number;
  claims_durable: number;
  claims_caveated: number;
  runs: number;
}

export interface IndexModel {
  schema: string;
  date: string;
  run_id?: string;
  totals: Totals;
  sources: SourceRow[];
  packs: PackRow[];
  claims: ClaimRow[];
  runs: RunRow[];
  ops: OpsState;
}
