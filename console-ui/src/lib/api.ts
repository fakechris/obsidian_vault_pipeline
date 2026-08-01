import type {
  AskProgress,
  AskResponse,
  AskSessionTurn,
  ChatEntry,
  ClaimDetail,
  ClaimRow,
  FindHit,
  FlowData,
  GraphResponse,
  IndexModel,
  SettingsPayload,
  SourceDetail,
  ThemeCount,
  ThemePagesResponse,
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

let themePagesCache: Promise<ThemePagesResponse> | null = null;
export function fetchThemePages(): Promise<ThemePagesResponse> {
  // Cache only the STATIC snapshot (immutable for the session). The live
  // server's projection changes when `crystal-theme-pages` reruns, and a
  // transient failure or empty first response must not freeze the panel
  // until a full reload.
  if (!STATIC_MODE) {
    return fetchJson<ThemePagesResponse>('/api/theme-pages');
  }
  if (!themePagesCache) {
    themePagesCache = fetchJson<ThemePagesResponse>(`${API}/theme-pages.json`);
    // A transiently failed fetch must not pin a rejected promise for the
    // whole session — drop it so the next mount retries.
    themePagesCache.catch(() => {
      themePagesCache = null;
    });
  }
  return themePagesCache;
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
/** Acknowledge one attention item — hides (sha,status) until the status
 * changes. Live server only. */
export async function ackAttention(sha: string, status: string): Promise<void> {
  const resp = await fetch('/api/attention/ack', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sha, status }),
  });
  if (!resp.ok) {
    const body = (await resp.json().catch(() => null)) as { error?: string } | null;
    throw new Error(body?.error ?? `ack failed (${resp.status})`);
  }
}

/** Manual pipeline run (`schedule run-now` under triple overlap protection). */
export interface RunNowStatus {
  running: string | null;
  heartbeat_running: boolean;
  last: Record<string, unknown> | null;
  jobs: Record<string, { last_run: string; last_status: string }>;
}
export function fetchRunNowStatus(): Promise<RunNowStatus> {
  return fetchJson<RunNowStatus>('/api/schedule/run/status');
}

/** Product-facing schedule registry (`GET /api/schedule`) for the System
 * automation explainer — jobs, cadence, last/next run, argv-derived flags. */
export interface ScheduleJobFeatures {
  pinboard_live: boolean;
  web_fetch_live: boolean;
  github_live: boolean;
  max_sources: number | null;
}
export interface ScheduleJob {
  id: string;
  cadence: string;
  enabled: boolean;
  description: string;
  argv: string[];
  last_run: string;
  last_status: string;
  next_run: string | null;
  due: boolean;
  features: ScheduleJobFeatures;
}
export interface SchedulePayload {
  present: boolean;
  registry_rel: string;
  jobs: ScheduleJob[];
}
export function fetchSchedule(): Promise<SchedulePayload> {
  return fetchJson<SchedulePayload>('/api/schedule');
}

/** Toggle daily argv features (today: pinboard_live) in `.ovp/schedule.json`. */
export async function setScheduleFeatures(opts: {
  job?: string;
  pinboard_live: boolean;
}): Promise<{ ok: boolean; features: ScheduleJobFeatures }> {
  const resp = await fetch('/api/schedule/features', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      job: opts.job ?? 'daily',
      pinboard_live: opts.pinboard_live,
    }),
  });
  if (!resp.ok) {
    const body = (await resp.json().catch(() => null)) as { error?: string } | null;
    throw new Error(body?.error ?? `schedule features failed (${resp.status})`);
  }
  return resp.json() as Promise<{ ok: boolean; features: ScheduleJobFeatures }>;
}
export async function startRunNow(job: string): Promise<void> {
  const resp = await fetch('/api/schedule/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job }),
  });
  if (!resp.ok && resp.status !== 202) {
    const body = (await resp.json().catch(() => null)) as { error?: string } | null;
    throw new Error(body?.error ?? `run failed (${resp.status})`);
  }
}

/** LLM provider config (a GUI over .ovp/providers.toml; secrets masked). */
export interface ProvidersPayload {
  env: Record<string, string>;
}
export function fetchProviders(): Promise<ProvidersPayload> {
  return fetchJson<ProvidersPayload>('/api/providers');
}
export async function saveProviders(set: Record<string, string>): Promise<void> {
  const resp = await fetch('/api/providers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ set }),
  });
  if (!resp.ok) {
    const body = (await resp.json().catch(() => null)) as { error?: string } | null;
    throw new Error(body?.error ?? `save failed (${resp.status})`);
  }
}

export function fetchSettings(): Promise<SettingsPayload> {
  return fetchJson<SettingsPayload>(STATIC_MODE ? `${API}/settings.json` : '/api/settings');
}

/** Publish job status (live server only — the published site itself has no
 * publish button). */
export interface PublishStatus {
  running: boolean;
  configured: boolean;
  last: Record<string, unknown> | null;
}
export function fetchPublishStatus(): Promise<PublishStatus> {
  return fetchJson<PublishStatus>('/api/publish/status');
}
export async function startPublish(): Promise<void> {
  const resp = await fetch('/api/publish', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!resp.ok && resp.status !== 202) {
    const body = (await resp.json().catch(() => null)) as { error?: string } | null;
    throw new Error(body?.error ?? `publish failed (${resp.status})`);
  }
}

/** Three-layer source detail: meta + memory + citing claims + raw md. */
export function fetchSourceDetail(sha: string): Promise<SourceDetail> {
  if (STATIC_MODE) return fetchJson<SourceDetail>(`${API}/source/${encodeURIComponent(sha)}.json`);
  return fetchJson<SourceDetail>(`/api/source/${encodeURIComponent(sha)}`);
}

/** Translation / summary archive status for one source. */
export interface SourceWorkPayload {
  work_rel: string;
  has_original: boolean;
  has_zh: boolean;
  has_summary: boolean;
  primarily_english: boolean;
  meta?: Record<string, unknown> | null;
  zh?: string | null;
  summary?: string | null;
}

export function fetchSourceWork(sha: string): Promise<SourceWorkPayload> {
  if (STATIC_MODE) {
    return Promise.resolve({
      work_rel: '',
      has_original: false,
      has_zh: false,
      has_summary: false,
      primarily_english: false,
    });
  }
  return fetchJson<SourceWorkPayload>(
    `/api/source/${encodeURIComponent(sha)}/work`,
  );
}

export async function postSourceTranslate(
  sha: string,
  force = false,
): Promise<SourceWorkPayload & { ok: boolean }> {
  const res = await fetch(`/api/source/${encodeURIComponent(sha)}/translate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ force }),
  });
  const data = (await res.json().catch(() => ({}))) as {
    error?: string;
    ok?: boolean;
  } & Partial<SourceWorkPayload>;
  if (!res.ok) throw new Error(data.error ?? `translate failed (${res.status})`);
  return data as SourceWorkPayload & { ok: boolean };
}

export async function postSourceSummarize(
  sha: string,
  force = false,
): Promise<SourceWorkPayload & { ok: boolean }> {
  const res = await fetch(`/api/source/${encodeURIComponent(sha)}/summarize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ force }),
  });
  const data = (await res.json().catch(() => ({}))) as {
    error?: string;
    ok?: boolean;
  } & Partial<SourceWorkPayload>;
  if (!res.ok) throw new Error(data.error ?? `summarize failed (${res.status})`);
  return data as SourceWorkPayload & { ok: boolean };
}

// ---- Source-work queue (per-article translate/summarize) ----

export type WorkTaskStatus =
  | 'queued'
  | 'running'
  | 'done'
  | 'failed'
  | 'skipped'
  | 'cancelled';

export type WorkItemStatus =
  | 'queued'
  | 'running'
  | 'done'
  | 'failed'
  | 'cancelled';

export interface WorkTaskState {
  wanted: boolean;
  force: boolean;
  status: WorkTaskStatus;
  error?: string | null;
}

export interface SourceWorkQueueItem {
  id: string;
  sha256: string;
  title?: string | null;
  translate: WorkTaskState;
  summarize: WorkTaskState;
  status: WorkItemStatus;
  created_at: number;
  started_at?: number | null;
  finished_at?: number | null;
  notify: boolean;
  notify_sent: boolean;
}

export interface SourceWorkWorkerInfo {
  /** This portal process is running the LLM worker. */
  active_here: boolean;
  /** PID holding the vault worker lock, if any. */
  owner_pid?: number | null;
  /** This portal's PID. */
  this_pid?: number;
}

export interface SourceWorkQueueSnapshot {
  schema: string;
  items: SourceWorkQueueItem[];
  /** Terminal items the client should notify about (server marks sent). */
  notify: SourceWorkQueueItem[];
  /** Cross-process worker election status. */
  worker?: SourceWorkWorkerInfo;
}

export function fetchSourceWorkQueue(): Promise<SourceWorkQueueSnapshot> {
  if (STATIC_MODE) {
    return Promise.resolve({ schema: '', items: [], notify: [] });
  }
  return fetchJson<SourceWorkQueueSnapshot>('/api/source-work/queue');
}

export async function enqueueSourceWork(opts: {
  sha256: string;
  title?: string | null;
  translate?: boolean;
  summarize?: boolean;
  force?: boolean;
  notify?: boolean;
}): Promise<SourceWorkQueueItem> {
  const res = await fetch('/api/source-work/queue', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      sha256: opts.sha256,
      title: opts.title ?? null,
      translate: !!opts.translate,
      summarize: !!opts.summarize,
      force: !!opts.force,
      notify: opts.notify !== false,
    }),
  });
  const data = (await res.json().catch(() => ({}))) as {
    error?: string;
    item?: SourceWorkQueueItem;
  };
  if (!res.ok || !data.item) {
    throw new Error(data.error ?? `enqueue failed (${res.status})`);
  }
  return data.item;
}

export async function reorderSourceWorkQueue(ids: string[]): Promise<SourceWorkQueueItem[]> {
  const res = await fetch('/api/source-work/queue/order', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  });
  const data = (await res.json().catch(() => ({}))) as {
    error?: string;
    items?: SourceWorkQueueItem[];
  };
  if (!res.ok) throw new Error(data.error ?? `reorder failed (${res.status})`);
  return data.items ?? [];
}

export async function cancelSourceWorkItem(id: string): Promise<void> {
  const res = await fetch(
    `/api/source-work/queue/${encodeURIComponent(id)}/cancel`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
  );
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(data.error ?? `cancel failed (${res.status})`);
  }
}

export async function deleteSourceWorkItem(id: string): Promise<void> {
  const res = await fetch(`/api/source-work/queue/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(data.error ?? `delete failed (${res.status})`);
  }
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
export function fetchGlobalGraph(
  limit = 400,
  persp: 'claim' | 'source' = 'claim',
): Promise<GraphResponse> {
  if (STATIC_MODE) {
    const file = persp === 'source' ? 'global-source' : 'global';
    return fetchJson<GraphResponse>(`${API}/graph/${file}.json`);
  }
  const p = persp === 'source' ? '&persp=source' : '';
  return fetchJson<GraphResponse>(`/api/graph?scope=global&limit=${limit}${p}`);
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

/** Prior completed turns for multi-turn Ask continuity (oldest first). */
export interface AskHistoryTurn {
  question: string;
  answer: string;
}

/** POST /api/ask options — chat stem, multi-turn history, optional focus. */
export interface PostAskOptions {
  /** Stem of the live session's `.ovp/chats/<chat>.md` — continue that file. */
  chat?: string | null;
  /** Prior Q/A in this conversation so the model sees follow-up context. */
  history?: AskHistoryTurn[];
  /** Pin the turn to one library source (chat-on-this). */
  focus_source?: string;
}

/** POST /api/ask — cited answer over the grounded evidence index. The
 * server saves (or appends) the transcript to `.ovp/chats/` as a side effect.
 * Pass `chat` + `history` to continue one conversation instead of opening a
 * new history row per question. */

export async function postAsk(
  question: string,
  opts: PostAskOptions = {},
): Promise<AskResponse> {
  if (STATIC_MODE) {
    throw new AskError(501, 'Ask is not available on the published site.', 'static_site');
  }
  const body: Record<string, unknown> = { question };
  if (opts.chat) body.chat = opts.chat;
  if (opts.history && opts.history.length > 0) body.history = opts.history;
  if (opts.focus_source) body.focus_source = opts.focus_source;
  const res = await fetch('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
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

/** GET /api/ask/status — agent-mode discovery. Unlike /api/model this
 * never needs an index, matching the agent path itself. */
export function fetchAskStatus(): Promise<{ agent: boolean }> {
  if (STATIC_MODE) return Promise.resolve({ agent: false });
  return fetchJson<{ agent: boolean }>('/api/ask/status');
}

/** GET /api/ask/session/<chat> — completed turns with full trails from the
 * audit transcript. Empty turns = a legacy markdown-only chat. */
export function fetchAskSession(chat: string): Promise<{ turns: AskSessionTurn[] }> {
  if (STATIC_MODE) return Promise.resolve({ turns: [] });
  return fetchJson<{ turns: AskSessionTurn[] }>(
    `/api/ask/session/${encodeURIComponent(chat)}`,
  );
}

/** GET /api/ask/progress — live mid-turn feed for an agent ask. Unknown
 * sessions answer an empty done feed, so polling is always safe. */
export function fetchAskProgress(chat: string): Promise<AskProgress> {
  return fetchJson<AskProgress>(
    `/api/ask/progress?chat=${encodeURIComponent(chat)}`,
  );
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

// ---- Tag curation (T2, live server only — docs/stage-tags-product.md §3) ----

export interface TagRow {
  tag: string;
  user: number;
  inferred: number;
  origin?: 'user' | 'community' | 'llm' | null;
}

export interface TagProposal {
  alias: string;
  alias_count: number;
  alias_titles?: string[];
  canonical: string;
  canonical_count: number;
  canonical_titles?: string[];
  /** NAME-only similarity — the score that made this a candidate. */
  cosine: number;
  /** name+titles similarity, display-only evidence (high context + low
   * name = related topics, not variants). */
  context_cosine?: number;
}

export interface TagsPayload {
  tags: TagRow[];
  banned: string[];
  proposals: TagProposal[];
}

/** GET /api/tags — vocabulary counts + pending merge proposals. */
export function fetchTags(): Promise<TagsPayload> {
  if (STATIC_MODE) {
    return Promise.resolve({ tags: [], banned: [], proposals: [] });
  }
  return fetchJson<TagsPayload>('/api/tags');
}

async function postJson<T = unknown>(path: string, payload: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const data = (await res.json()) as { error?: unknown };
      if (data && typeof data.error === 'string') message = data.error;
    } catch {
      /* keep status line */
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

/** POST /api/tags/decision — record accept/reject; the server rebuilds the
 * projection so the merge is visible on the next fetch. */
export async function postTagDecision(
  action: 'accept' | 'reject',
  alias: string,
  canonical: string,
): Promise<void> {
  await postJson('/api/tags/decision', { action, alias, canonical });
}

/** POST /api/source/:sha/tags — the one sanctioned frontmatter write
 * (accepting an inferred tag / adding a tag is a user edit via the product).
 * Editing a queued note changes its content hash; the response carries the
 * sha the source now lives under so the caller can re-route. */
export function postSourceTags(
  sha: string,
  tags: string[],
): Promise<{ ok: boolean; changed: boolean; sha?: string }> {
  return postJson(`/api/source/${encodeURIComponent(sha)}/tags`, { tags });
}

// ---- Tier-0 URL entities (docs/stage-tags-product.md §5b) ----

export interface EntityRow {
  id: string;
  kind: string;
  url: string;
  count: number;
}

export interface EntitySource {
  sha256: string;
  title?: string;
  date?: string;
}

export interface EntityDetail {
  id: string;
  kind: string | null;
  url: string | null;
  sources: EntitySource[];
  citing_claims: ClaimRow[];
}

/** GET /api/entities — the URL entity index, most-mentioned first. Static
 * mode reads the snapshotted `entities.json`. */
export function fetchEntities(): Promise<EntityRow[]> {
  const path = STATIC_MODE ? `${API}/entities.json` : '/api/entities';
  return fetchJson<{ entities: EntityRow[] }>(path).then((d) => d.entities ?? []);
}

/** GET /api/entity/:id — one entity's mentioning sources + citing claims. */
export function fetchEntity(id: string): Promise<EntityDetail> {
  if (STATIC_MODE) {
    // Published per-entity files are base64url(id) — reversible + collision-
    // free, matching the publisher's `entity_filename`. Entity ids are ascii.
    const b64 = btoa(id).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    return fetchJson<EntityDetail>(`${API}/entity/${b64}.json`);
  }
  return fetchJson<EntityDetail>(`/api/entity/${encodeURIComponent(id)}`);
}

/** Reconstruct the external URL from an entity id (client-side mirror of the
 * server's `url_for_id`, for chips that don't fetch the detail). */
export function entityUrl(id: string): string | null {
  const [kind, key] = [id.slice(0, id.indexOf(':')), id.slice(id.indexOf(':') + 1)];
  if (!key) return null;
  switch (kind) {
    case 'github':
      return `https://github.com/${key}`;
    case 'arxiv':
      return `https://arxiv.org/abs/${key}`;
    case 'doi':
      return `https://doi.org/${key}`;
    case 'npm':
      return `https://www.npmjs.com/package/${key}`;
    case 'crates':
      return `https://crates.io/crates/${key}`;
    case 'pypi':
      return `https://pypi.org/project/${key}`;
    case 'hn':
      return `https://news.ycombinator.com/item?id=${key}`;
    default:
      return null;
  }
}
