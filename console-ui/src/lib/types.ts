// Mirrors the JSON contracts of ovp-server (crates/ovp-server/src/graph.rs).

export type NodeType = 'claim' | 'unit' | 'source' | 'card';
export type EdgeType = 'cites' | 'extracted_from' | 'related' | 'has_memory';
export type GraphMode = 'overview' | 'neighborhood' | 'search' | 'theme';

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
  /** Claims only: index claim_id for portal links — the node `id` carries
   * the ledger claim_key, which can differ. */
  claim_id?: string;
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

/** /api/find and /api/search hit — a display line plus a kind-specific
 * stable id for entity links (source → sha256, pack → pack_dir,
 * claim → claim_id, run → run_id). */
export interface FindHit {
  kind: string;
  status: string;
  line: string;
  path?: string;
  id?: string;
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

// ---- /api/source/:sha (B2 source detail) ----

export interface MemoryCard {
  title: string;
  content: string;
}

export interface MemoryUnit {
  unit_id: string;
  text: string;
  quote: string;
  line: number | null;
  attribution: string;
}

export interface SourceMemory {
  /** False when the vault has no evidence sidecar (pre-M31) — the page
   * shows a "run ovp2 index" hint instead of an empty memory layer. */
  evidence_available: boolean;
  cards: MemoryCard[];
  units: MemoryUnit[];
}

export interface SourceDocPayload {
  /** Raw markdown text (JSON data — rendered client-side, never as HTML). */
  markdown: string | null;
  /** True when the body was cut at the server's 200KB cap. */
  truncated: boolean;
  error: string | null;
}

export interface SourceDetail {
  source: SourceRow;
  memory: SourceMemory;
  citing_claims: ClaimRow[];
  doc: SourceDocPayload;
}

// ---- POST /api/ask + /api/chats (B4 Ask page) ----

export type AskCitationKind = 'claim' | 'card' | 'unit';

/** One citation the answer text actually uses, in first-appearance order —
 * the UI numbers its [1][2] markers by array position. `id` is the full
 * citation key as written in the answer (e.g. `claim:c01`). `link_target`
 * is null for legacy evidence with no portal page (sha-guard) and for
 * citations the model invented (`verified: false`). */
export interface AskCitation {
  id: string;
  kind: AskCitationKind | string;
  title: string | null;
  snippet: string | null;
  link_target: string | null;
  verified: boolean;
}

export interface AskVerification {
  cited: number;
  verified: number;
  missing: string[];
  warnings: string[];
}

export interface AskResponse {
  answer: string;
  citations: AskCitation[];
  verified: AskVerification | null;
  context_hits: number;
  /** Stem of the saved `.ovp/chats/<name>.md` transcript. */
  chat: string | null;
}

/** /api/chats entry — `mtime` is unix seconds; the client formats it. */
export interface ChatEntry {
  name: string;
  mtime: number;
}

// ---- GET /api/settings (B5 System page, read-only v1) ----

export interface SettingsCounts {
  sources: number;
  packs: number;
  claims: number;
}

export interface AskLimits {
  timeout_secs: number;
  /** Null = no server-side cap (each ask runs on its own worker). */
  max_concurrent: number | null;
}

/** Read-only server/vault configuration. Index-derived fields are null when
 * the vault has no index projection yet. */
export interface SettingsPayload {
  vault_root: string;
  schema_version: string | null;
  index_date: string | null;
  /** P1 provenance: the projection's build instant, its producer run id, and
   * the server-computed age. Null when no index is built yet. */
  built_at: string | null;
  run_id: string | null;
  age_seconds: number | null;
  counts: SettingsCounts | null;
  /** LIVE queued backlog (01-Raw walk at serve time) — the authoritative-now
   * figure. Always present (0 on an empty vault), unlike the index-derived
   * fields. */
  queued_live: number;
  /** The projection's frozen end-of-run `totals.queued`; null when no index is
   * built yet. Shown as the secondary provenance number. */
  queued_at_build: number | null;
  llm_configured: boolean;
  ask_limits: AskLimits;
  /** Run-liveness heartbeat block (OVP2 observability P0); null on a fresh
   * vault / pre-P0 index. Mirrors `model.ops.last_run`. */
  last_run: LastRunModel | null;
  version: string;
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

export type LastRunStatus = 'running' | 'completed' | 'failed' | 'aborted';

/** Run-liveness heartbeat (`.ovp/last-run.json`), surfaced into the read
 * model. Age is computed client-side from started_at/ended_at + Date.now so
 * the banner ticks without a refetch — the server ships no `minutes_since`. */
export interface LastRunModel {
  run_id: string;
  /** UTC RFC3339. */
  started_at: string;
  /** UTC RFC3339; absent while `running`. */
  ended_at?: string;
  status: LastRunStatus;
  processed?: number;
  failed?: number;
  blocked?: number;
  capped?: number;
  queued_after?: number;
  /** LIVE in-run progress (only while `running`): sources finished so far this
   * run. Pairs with `total_planned` to render "18/90". Absent on terminal
   * records. */
  processed_so_far?: number;
  /** LIVE in-run progress: total sources this run intends to process. */
  total_planned?: number;
  /** LIVE in-run progress: the source just finished (title or rel path). */
  current?: string;
  error?: string;
}

export interface OpsState {
  blocked_sources: BlockedSource[];
  queue_depth: number;
  run_stats?: RunStats | null;
  /** Null on a fresh vault (no runs yet) or a pre-P0 index. */
  last_run?: LastRunModel | null;
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
  /** Wall-clock build instant (UTC RFC3339). Absent on pre-P1 indexes — the
   * UI then shows "unknown age". */
  built_at?: string | null;
  run_id?: string;
  /** Server-computed seconds since `built_at` (spliced into /api/model). The
   * client ticks its own age from `built_at`; this is the server's reading at
   * fetch time. */
  age_seconds?: number | null;
  /** LIVE queued backlog computed at serve time (01-Raw walk), spliced into
   * /api/model. This is the authoritative-now "Queued" figure the SPA renders
   * as primary; it ticks down during a run while `totals.queued` (the frozen
   * end-of-run projection) does not. */
  queued_live?: number;
  /** The projection's `totals.queued` mirrored for a symmetric label; equals
   * `totals.queued`. Absent on pre-overlay servers. */
  queued_at_build?: number;
  totals: Totals;
  sources: SourceRow[];
  packs: PackRow[];
  claims: ClaimRow[];
  runs: RunRow[];
  ops: OpsState;
}
