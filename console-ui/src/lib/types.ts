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
  /** Keys this claim superseded (lineage). */
  supersedes?: string[];
  /** Keys that superseded this claim. */
  superseded_by?: string[];
  /** Folded ledger status when known: active | superseded | retracted. */
  status?: string;
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

export interface ThemePageSection {
  heading: string;
  body: string;
}

export interface ThemePageData {
  community_id: number;
  label: string;
  label_zh: string;
  claim_count: number;
  sections: ThemePageSection[];
}

export interface ThemePageClaimInfo {
  claim_id: string;
  claim: string;
  strength?: string;
  sources: string[];
}

export interface ThemePagesResponse {
  pages: ThemePageData[];
  claims: Record<string, ThemePageClaimInfo>;
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

/**
 * Timeline axes (do not collapse):
 * - A content: `content_date` — bookmark/published/filename day
 * - B pipeline: `captured_on` / `processed_on` / `last_run_id` / legacy `date`
 * - C subject: not stored yet
 */
export interface SourceRow {
  sha256: string;
  status: SourceStatus;
  title?: string;
  url?: string;
  /** Capture-origin facet (`"pinboard"`), URL-matched against the
   * pinboard-sync ledger at index build. Survives enrichment re-hashes and
   * lifecycle moves — unlike the note's current path. */
  origin?: string;
  rel_path?: string;
  /** Legacy B: last pipeline activity (`processed_on ?? captured_on`). */
  date?: string;
  /** A: content/capture day when known. */
  content_date?: string;
  /** B: intake ledger day. */
  captured_on?: string;
  /** B: last daily-run ledger day. */
  processed_on?: string;
  last_run_id?: string;
  pack_dir?: string;
  fail_count: number;
  last_reason?: string;
  /** Canonical content tags (normalized + alias-resolved at index build).
   * Absent on pre-tag indexes and on the redacted public model. */
  tags?: string[];
  /** Machine-inferred backfill tags (tags-suggest kNN vote). Present only
   * while the source has no operator tags; rendered visibly weaker. */
  tags_inferred?: string[];
  /** Tier-0 URL entity ids this source mentions (`github:owner/repo`,
   * `arxiv:2504.19413`). Public content — present on the published model too. */
  entities?: string[];
}

export interface PackRow {
  pack_dir: string;
  title: string;
  /** B: pipeline day the pack was written (dir prefix). */
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
  /** Stable ledger identity (ck-…) — claim_ids can collide across runs,
   * claim_keys cannot. Absent on pre-key indexes. */
  claim_key?: string;
  claim: string;
  theme?: string;
  status: ClaimStatus;
  sources: string[];
  strength?: string;
  run_id?: string;
  /** B: day embedded in run_id when present. */
  run_date?: string;
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
  /** True/false = the server's verifier ruling. Null = UNKNOWN — a saved
   * transcript replayed without re-verification must claim neither
   * (previously replay upgraded every marker, fabricated ones included,
   * to verified). Only `false` renders warnings. */
  verified: boolean | null;
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
  /** Job path for this turn: find_source | grounded_qa | explore | meta_capability. */
  intent?: string | null;
  /** Stem of the saved `.ovp/chats/<name>.md` transcript. */
  chat: string | null;
  // ---- agent-path extras (the DEFAULT path since A3d; absent on the
  // legacy pipeline reachable via the OVP_ASK_AGENT=0 rollback hatch) ----
  /** True when the answer came from the tool-loop agent. */
  agent?: boolean;
  /** Executor-computed per-layer coverage (claims/sources/body →
   * not_queried|complete|partial|unavailable|failed). Null on an
   * idempotent replay (coverage is an execution artifact). */
  coverage?: Record<string, string> | null;
  /** Compact per-call trail of what the agent actually executed. */
  tool_trace?: AskTraceEntry[];
  /** final | need_user | refusal | max_rounds | timeout | tool_error | model_error. */
  stopped_reason?: string;
  turn_id?: string;
  usage?: { input_tokens: number; output_tokens: number };
}

/** Compact involved-entity node from a tool result (process graph). */
export interface AskProgressHit {
  kind: string;
  id: string;
  label: string;
  source_id?: string | null;
}

/** One executed tool call in an agent turn's caller-facing trail. */
export interface AskTraceEntry {
  tool: string;
  summary: string;
  ok: boolean;
  /** Display narration of the call arguments ("query=… · limit=…"). */
  args?: string | null;
  /** Parsed result stats ("3 hit(s) · scanned 918/1281 · truncated"). */
  note?: string | null;
  /** Involved entities for process visualization (claims/sources/cards). */
  hits?: AskProgressHit[];
}

/** One event from GET /api/ask/progress — the live mid-turn feed. */
export interface AskProgressEvent {
  event: string;
  tool?: string;
  tool_call_id?: string;
  /** Display narration for tool_started ("query=… · limit=…"). */
  args?: string | null;
  summary?: string;
  /** Parsed result stats for tool_finished events. */
  note?: string | null;
  /** Involved entities painted into the live process graph. */
  hits?: AskProgressHit[];
  ok?: boolean;
  turn_id?: string;
  stopped_reason?: string;
}

/** One completed turn from GET /api/ask/session/<chat> — the saved-chat
 * bridge to the audit transcript (History replay with full trails). */
export interface AskSessionTurn {
  turn_id: string;
  question: string;
  answer: string;
  stopped_reason: string;
  tool_trace: AskTraceEntry[];
}

export interface AskProgress {
  events: AskProgressEvent[];
  done: boolean;
  /** False while the turn is still in admission (setup/lock phase). */
  started: boolean;
}

/** /api/chats entry — `mtime` is unix seconds; the client formats it.
 * Source-grounded sessions (chat-on-this) carry focus_* so Ask history and
 * Library can filter/label without re-parsing markdown. */
export interface ChatEntry {
  name: string;
  mtime: number;
  /** Source sha when this session was started via Chat-on-this. */
  focus_source?: string | null;
  focus_title?: string | null;
  /** First user question preview (truncated). */
  preview?: string | null;
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
/** Server build identity — package version + git hash (+dirty) + build
 * instant. The stale-build diagnosis surface. */
export interface ServerVersion {
  server: string;
  git: string;
  built: string;
}

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
  /** Server build identity (was a bare package-version string pre-#379). */
  version: ServerVersion;
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
  /** LIVE per-source activity ring (the portal's tail -f): the last ~20 source
   * outcomes, oldest→newest, while `running`. Empty on terminal records. The
   * "Run activity" panel renders the ✓/✗ feed from it. */
  recent?: RecentSource[];
  error?: string;
}

/** One per-source outcome in the live activity feed — mirrors the heartbeat
 * `recent[]`. Both success and failure appear so a run that starts failing is
 * diagnosable from the portal without SSHing in to tail the log. */
export interface RecentSource {
  /** Monotonic 1-based sequence within the run. */
  seq: number;
  /** The source that finished (its vault-relative path / title). */
  title: string;
  status: 'ok' | 'failed';
  units: number;
  cards: number;
  /** Failure reason (present only on `failed`). */
  reason?: string;
  /** UTC RFC3339 instant the source finished. */
  at: string;
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
  /** Live-server overlay: acknowledged attention items (hidden until the
   * source's status changes). Absent in static snapshots. */
  attention_acks?: { sha: string; status: string }[];
  /** Live-server overlay: the tool-loop agent serves /api/ask. The SPA
   * pre-generates a chat id and polls the progress feed from turn 1. */
  ask_agent?: boolean;
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
