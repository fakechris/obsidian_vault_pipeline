/** Pure derivations over the /api/model IndexModel — everything the Today
 * and Library pages render is computed here so it stays testable and the
 * components stay dumb. */
import type {
  ClaimRow,
  IndexModel,
  LastRunModel,
  PackRow,
  RecentSource,
  RunRow,
  SourceRow,
} from './types';

// ------------------------------------------------------ run-liveness heartbeat

/** Default staleness window (ms): 26 hours — one 09:00 daily schedule interval
 * (24h) plus slack. Beyond this the unattended loop is treated as stalled and
 * the banner turns amber. Mirrors the doctor default. */
export const STALE_AFTER_MS = 26 * 60 * 60 * 1000;

export type BannerLevel = 'none' | 'ok' | 'stale' | 'failed';

/** Everything the fixed portal banner needs, derived from the heartbeat and
 * the CURRENT wall clock (passed in for testability + client-side ticking).
 * Deliberately tolerates a null model — the banner must render even when the
 * rest of the model is empty (fresh/failed vault), so a null model never hides
 * it: it just shows the muted "no runs yet" state. */
export interface LastRunBanner {
  level: BannerLevel;
  status: LastRunModel['status'] | null;
  /** Whole minutes since the run's terminal (or start) instant. null when
   * there is no run or the timestamp is unparseable. */
  ageMinutes: number | null;
  error: string | null;
  processed: number | null;
  queuedAfter: number | null;
  /** LIVE in-run progress (running only): sources finished so far. null unless
   * the heartbeat carries it. */
  processedSoFar: number | null;
  /** LIVE in-run progress: total sources this run plans to process. */
  totalPlanned: number | null;
  /** LIVE in-run progress: the source being/just processed. */
  current: string | null;
}

/** The instant the banner ages from: the terminal time if the run ended, else
 * its start (a still-running or hard-killed run ages from when it began). */
function lastRunInstantMs(lr: LastRunModel): number | null {
  const raw = lr.ended_at ?? lr.started_at;
  const ms = Date.parse(raw);
  return Number.isNaN(ms) ? null : ms;
}

export function lastRunBanner(
  model: IndexModel | null,
  nowMs: number,
  staleAfterMs: number = STALE_AFTER_MS,
): LastRunBanner {
  const lr = model?.ops?.last_run ?? null;
  if (!lr) {
    return {
      level: 'none',
      status: null,
      ageMinutes: null,
      error: null,
      processed: null,
      queuedAfter: null,
      processedSoFar: null,
      totalPlanned: null,
      current: null,
    };
  }
  const instant = lastRunInstantMs(lr);
  const ageMinutes =
    instant == null ? null : Math.max(0, Math.floor((nowMs - instant) / 60000));

  let level: BannerLevel;
  if (lr.status === 'failed' || lr.status === 'aborted') {
    level = 'failed';
  } else if (
    instant != null &&
    nowMs - instant > staleAfterMs
  ) {
    // A completed-but-old run, or a run still claiming "running" long past the
    // schedule interval (it died without the drop-guard firing), is stale.
    level = 'stale';
  } else {
    level = 'ok';
  }

  return {
    level,
    status: lr.status,
    ageMinutes,
    error: lr.error ?? null,
    processed: lr.processed ?? null,
    queuedAfter: lr.queued_after ?? null,
    processedSoFar: lr.processed_so_far ?? null,
    totalPlanned: lr.total_planned ?? null,
    current: lr.current ?? null,
  };
}

/** True when the heartbeat is a live in-progress run WITH a progress fraction —
 * the banner shows "18/90 · <current>" and polls faster. A run that hasn't
 * written its first per-source progress yet (or an older server) has no
 * fraction and falls back to the plain "running" banner. */
export function isRunningWithProgress(banner: LastRunBanner): boolean {
  return (
    banner.status === 'running' &&
    banner.processedSoFar != null &&
    banner.totalPlanned != null &&
    banner.totalPlanned > 0
  );
}

/** The live per-source activity feed — the portal's tail -f. Derived from the
 * heartbeat `recent[]` ring plus the running fraction, so the "Run activity"
 * panel (System page + expandable from the banner) can render:
 *   - a fraction + percent bar while running,
 *   - the current source,
 *   - the last ~20 ✓/✗ per-source outcomes, NEWEST FIRST (so the freshest line
 *     is at the top of the feed).
 * Tolerates a null model / absent heartbeat (returns the empty idle shape) so
 * the panel never crashes on a fresh vault. */
export interface RunActivity {
  status: LastRunModel['status'] | null;
  running: boolean;
  processedSoFar: number | null;
  totalPlanned: number | null;
  /** 0-100, null when there is no fraction to compute. */
  pct: number | null;
  current: string | null;
  /** Terminal counts (present once the run finished). */
  processed: number | null;
  failed: number | null;
  error: string | null;
  /** Last ~20 outcomes, NEWEST FIRST. */
  recent: RecentSource[];
}

export function runActivity(model: IndexModel | null): RunActivity {
  const lr = model?.ops?.last_run ?? null;
  if (!lr) {
    return {
      status: null,
      running: false,
      processedSoFar: null,
      totalPlanned: null,
      pct: null,
      current: null,
      processed: null,
      failed: null,
      error: null,
      recent: [],
    };
  }
  const processedSoFar = lr.processed_so_far ?? null;
  const totalPlanned = lr.total_planned ?? null;
  const pct =
    processedSoFar != null && totalPlanned != null && totalPlanned > 0
      ? Math.min(100, Math.round((processedSoFar / totalPlanned) * 100))
      : null;
  // Newest first for the feed; the ring is stored oldest→newest.
  const recent = lr.recent ? [...lr.recent].reverse() : [];
  return {
    status: lr.status,
    running: lr.status === 'running',
    processedSoFar,
    totalPlanned,
    pct,
    current: lr.current ?? null,
    processed: lr.processed ?? null,
    failed: lr.failed ?? null,
    error: lr.error ?? null,
    recent,
  };
}

// ---------------------------------------------------------------- status dot

export type HealthLevel = 'ok' | 'attention' | 'failed';

/** Nav status dot: red when the most recent run failed/aborted/stale (from the
 * heartbeat) OR when the most recent per-source run failed; amber when operator
 * attention is pending (blocked / needs-content sources); green otherwise.
 * `nowMs` lets the heartbeat staleness be evaluated at render time. */
export function healthLevel(
  model: IndexModel,
  nowMs: number = Date.now(),
): HealthLevel {
  const banner = lastRunBanner(model, nowMs);
  if (banner.level === 'failed' || banner.level === 'stale') return 'failed';
  const lastRun = model.runs[model.runs.length - 1];
  if (lastRun && lastRun.failed > 0) return 'failed';
  if (attentionCount(model) > 0) return 'attention';
  return 'ok';
}

export function attentionCount(model: IndexModel): number {
  return model.totals.blocked + model.totals.needs_content;
}

// -------------------------------------------------------------------- today

export interface TodayStats {
  /** Distinct run dates — the "dogfood day N" counter. */
  dogfoodDay: number;
  /** Runs whose date == model.date. */
  todayRuns: RunRow[];
  captured: number;
  capturedPinboard: number;
  read: number;
  readUnits: number;
  readCards: number;
  attention: number;
}

export function todayStats(model: IndexModel): TodayStats {
  const todayRuns = model.runs.filter((r) => r.date === model.date);
  const readSources = readToday(model);
  return {
    dogfoodDay: new Set(model.runs.map((r) => r.date)).size,
    todayRuns,
    captured: todayRuns.reduce((n, r) => n + r.ingested, 0),
    capturedPinboard: todayRuns.reduce((n, r) => n + r.pinboard_new, 0),
    read: todayRuns.reduce((n, r) => n + r.succeeded, 0),
    readUnits: readSources.reduce((n, s) => n + (s.pack?.units ?? 0), 0),
    readCards: readSources.reduce((n, s) => n + (s.pack?.cards ?? 0), 0),
    attention: attentionCount(model),
  };
}

export interface ReadSource {
  source: SourceRow;
  pack?: PackRow;
}

/** Sources processed by today's runs (last_run_id ∈ today's run ids), with
 * their pack (units/cards meta) when resolvable via pack_dir. */
export function readToday(model: IndexModel): ReadSource[] {
  const todayRunIds = new Set(
    model.runs.filter((r) => r.date === model.date).map((r) => r.run_id),
  );
  const packByDir = new Map(model.packs.map((p) => [p.pack_dir, p]));
  return model.sources
    .filter(
      (s) =>
        s.status === 'processed' &&
        s.last_run_id != null &&
        todayRunIds.has(s.last_run_id),
    )
    .map((source) => ({
      source,
      pack: source.pack_dir ? packByDir.get(source.pack_dir) : undefined,
    }))
    .sort((a, b) => (a.source.title ?? '').localeCompare(b.source.title ?? ''));
}

/** Sources needing operator attention: blocked + needs-content. */
export function attentionSources(model: IndexModel): SourceRow[] {
  return model.sources.filter(
    (s) => s.status === 'blocked' || s.status === 'needs_content',
  );
}

/** Sample of claims for the Today page — durable-first, labeled as such.
 * B2 verdict on the codex-review P2: NO date is derivable. The crystal
 * ledger (StoreEvent/DurableRecord) carries no date/written-at field,
 * `default_run_id` is a content hash with deliberately no wall-clock, and
 * review.json entries are dateless too — so "crystallized today" would be
 * an invention. Real per-day attribution needs a ledger schema change
 * (timestamped StoreEvent), tracked for a later phase. */
export function claimsSample(model: IndexModel, n: number): ClaimRow[] {
  const rank = (c: ClaimRow) => (c.status === 'durable' ? 0 : 1);
  return model.claims
    .filter((c) => c.status === 'durable' || c.status === 'caveated')
    .sort((a, b) => rank(a) - rank(b))
    .slice(0, n);
}

export interface TimelineDay {
  date: string;
  read: number;
  captured: number;
}

/** Per-day aggregation of the last `days` distinct run dates, newest first. */
export function timeline(model: IndexModel, days: number): TimelineDay[] {
  const byDate = new Map<string, TimelineDay>();
  for (const run of model.runs) {
    const day = byDate.get(run.date) ?? {
      date: run.date,
      read: 0,
      captured: 0,
    };
    day.read += run.succeeded;
    day.captured += run.ingested;
    byDate.set(run.date, day);
  }
  return [...byDate.values()]
    .sort((a, b) => b.date.localeCompare(a.date))
    .slice(0, days);
}

// ------------------------------------------------------------------ library

export type Collection = 'clippings' | 'pinboard' | 'capture';

/** Collection = where the source lives in the vault (design §3.2). */
export function collectionOf(source: SourceRow): Collection {
  const path = source.rel_path ?? '';
  if (path.includes('02-Pinboard')) return 'pinboard';
  if (path.includes('00-Capture')) return 'capture';
  return 'clippings';
}

/** YYYY-MM facet key; sources without a date group under ''. */
export function monthOf(source: SourceRow): string {
  return source.date?.slice(0, 7) ?? '';
}

export interface LibraryFilter {
  collection: Collection | null;
  month: string | null;
  status: string | null;
  tag: string | null;
}

export function filterSources(
  sources: SourceRow[],
  filter: LibraryFilter,
): SourceRow[] {
  return sources.filter(
    (s) =>
      (filter.collection === null || collectionOf(s) === filter.collection) &&
      (filter.month === null || monthOf(s) === filter.month) &&
      (filter.status === null || s.status === filter.status) &&
      (filter.tag === null ||
        (s.tags ?? []).includes(filter.tag) ||
        (s.tags_inferred ?? []).includes(filter.tag)),
  );
}

/** Tag → source count over the whole library (operator + inferred — the
 * facet filters on both), count desc then name. */
export function countTags(sources: SourceRow[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const s of sources) {
    for (const t of s.tags ?? []) {
      counts.set(t, (counts.get(t) ?? 0) + 1);
    }
    for (const t of s.tags_inferred ?? []) {
      counts.set(t, (counts.get(t) ?? 0) + 1);
    }
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

export interface MonthGroup {
  month: string;
  sources: SourceRow[];
}

/** Group by month, newest month first; rows newest-date first within. */
export function groupByMonth(sources: SourceRow[]): MonthGroup[] {
  const groups = new Map<string, SourceRow[]>();
  for (const s of sources) {
    const key = monthOf(s);
    const list = groups.get(key) ?? [];
    list.push(s);
    groups.set(key, list);
  }
  return [...groups.entries()]
    .sort((a, b) => b[0].localeCompare(a[0]))
    .map(([month, list]) => ({
      month,
      sources: list.sort((a, b) =>
        (b.date ?? '').localeCompare(a.date ?? ''),
      ),
    }));
}

// ---------------------------------------------------------------- knowledge

/** One card of the Knowledge-home theme wall. */
export interface ThemeGroup {
  theme: string;
  total: number;
  durable: number;
  caveated: number;
  /** First durable (else first caveated) claim text — the wall snippet. */
  topClaim?: string;
}

/** The synthesizer's fallback bucket — sources that matched no keyword
 * bucket land under 'misc' (key) / 'Miscellaneous' (description). The
 * portal displays it honestly as "Unclassified" — DISPLAY LAYER ONLY: keys,
 * URLs and index data keep the literal value. */
export function isMiscTheme(theme: string | null | undefined): boolean {
  // '' / nullish is the "no theme" bucket — display it as Unclassified too,
  // so graph clicks and the wall card on unthemed claims read honestly.
  return theme == null || theme === '' || theme === 'misc' || theme === 'Miscellaneous';
}

/** Route segment for the "no theme" bucket ('' theme key). A real theme is
 * never this literal, so it round-trips without colliding — and unthemed
 * claims/cards get a routable landing page instead of dead-ending on an empty
 * `/knowledge/theme/` segment (which falls through to the catch-all redirect). */
export const UNTHEMED_SEGMENT = '~none';

/** Theme key → `/knowledge/theme/...` route, encoding the empty bucket as the
 * routable sentinel above. */
export function themeRoute(theme: string | null | undefined): string {
  const key = theme ?? '';
  return `/knowledge/theme/${key === '' ? UNTHEMED_SEGMENT : encodeURIComponent(key)}`;
}

/** Inverse of {@link themeRoute} for a decoded `:theme` route param. */
export function themeFromRoute(param: string | null | undefined): string {
  return param == null || param === UNTHEMED_SEGMENT ? '' : param;
}

/** Active claims only — the knowledge surface never lists superseded or
 * retracted claims (they remain reachable through the ledger/CLI). */
export function activeClaims(claims: ClaimRow[]): ClaimRow[] {
  return claims.filter(
    (c) => c.status === 'durable' || c.status === 'caveated',
  );
}

/** Theme wall from /api/model claims + /api/themes: ledger themes keep the
 * ledger order (count desc); index-only themes append after. Claims without
 * a theme group under '' — the caller decides how to label it. */
export function themeWall(
  claims: ClaimRow[],
  ledgerThemes: { theme: string; count: number }[],
): ThemeGroup[] {
  const groups = new Map<string, ThemeGroup>();
  const ensure = (theme: string): ThemeGroup => {
    let g = groups.get(theme);
    if (!g) {
      g = { theme, total: 0, durable: 0, caveated: 0 };
      groups.set(theme, g);
    }
    return g;
  };
  for (const t of ledgerThemes) {
    ensure(t.theme);
  }
  for (const c of activeClaims(claims)) {
    const g = ensure(c.theme ?? '');
    if (c.status === 'durable') {
      // The first durable claim is the wall snippet, even when a caveated
      // one was seen first.
      if (g.durable === 0) g.topClaim = c.claim;
      g.durable += 1;
    } else {
      g.caveated += 1;
      g.topClaim ??= c.claim;
    }
  }
  for (const t of ledgerThemes) {
    const g = groups.get(t.theme);
    // Ledger and index normally agree; when they drift mid-run, show the
    // larger count rather than hiding claims.
    if (g) g.total = Math.max(t.count, g.durable + g.caveated);
  }
  for (const g of groups.values()) {
    g.total = Math.max(g.total, g.durable + g.caveated);
  }
  return [...groups.values()].sort(
    (a, b) => b.total - a.total || a.theme.localeCompare(b.theme),
  );
}

/** Theme claims for the detail page: durable first, then caveated;
 * stable claim_id order within each band. */
export function themeClaims(claims: ClaimRow[], theme: string): ClaimRow[] {
  const rank = (c: ClaimRow) => (c.status === 'durable' ? 0 : 1);
  return activeClaims(claims)
    .filter((c) => (c.theme ?? '') === theme)
    .sort((a, b) => rank(a) - rank(b) || a.claim_id.localeCompare(b.claim_id));
}

/** case id (last pack_dir segment) → source row, via the packs' sha link.
 * ClaimRow.sources hold case ids; a case whose pack lacks a source sha is a
 * legacy source with NO /library page (portal handoff note: never navigate
 * to a 404 for those). */
export function sourcesByCase(model: IndexModel): Map<string, SourceRow> {
  const bySha = new Map(model.sources.map((s) => [s.sha256, s]));
  const out = new Map<string, SourceRow>();
  for (const p of model.packs) {
    const caseId = p.pack_dir.split(/[/\\]/).filter(Boolean).pop();
    if (!caseId || !p.source_sha256) continue;
    const src = bySha.get(p.source_sha256);
    if (src) out.set(caseId, src);
  }
  return out;
}

export type PageBodyToken =
  | { kind: 'text'; text: string }
  | { kind: 'cite'; key: string };

/** Blank-line paragraphs with inline `[claim:<key>]` references tokenized for
 * the grounded topic-page renderer. Malformed/unterminated markers stay text. */
export function parsePageBody(body: string): PageBodyToken[][] {
  if (!body) return [];
  return body
    .split(/\r?\n(?:[ \t]*\r?\n)+/)
    .filter((paragraph) => paragraph.length > 0)
    .map((paragraph) => {
      const tokens: PageBodyToken[] = [];
      const marker = /\[claim:([^\]]+)\]/g;
      let cursor = 0;
      for (const match of paragraph.matchAll(marker)) {
        const index = match.index ?? 0;
        if (index > cursor) {
          tokens.push({ kind: 'text', text: paragraph.slice(cursor, index) });
        }
        // Same key semantics as the Rust extractor: `[claim: ck-b ]` is a
        // valid citation, so the key must be trimmed before lookup.
        tokens.push({ kind: 'cite', key: match[1].trim() });
        cursor = index + match[0].length;
      }
      if (cursor < paragraph.length) {
        tokens.push({ kind: 'text', text: paragraph.slice(cursor) });
      }
      return tokens;
    });
}

export function countBy<T, K>(items: T[], key: (item: T) => K): Map<K, number> {
  const counts = new Map<K, number>();
  for (const item of items) {
    const k = key(item);
    counts.set(k, (counts.get(k) ?? 0) + 1);
  }
  return counts;
}

// ------------------------------------------------------------------ freshness

/** The pieces a freshness label needs, derived client-side from a projection's
 * `built_at` and the current wall clock. `unit`/`value` name a coarse bucket
 * (seconds → just now, minutes, hours, days) so the i18n layer can render
 * "N min ago" bilingually WITHOUT owning the arithmetic. `unknown` is true when
 * `built_at` is absent (pre-P1 index) or unparseable — the label then reads
 * "unknown age" rather than fabricating a 0. */
export interface AgeParts {
  unknown: boolean;
  /** RFC3339 instant, echoed for the "as of <built_at>" prefix (null when unknown). */
  builtAt: string | null;
  /** Whole seconds since built_at, clamped at 0 (0 when unknown). */
  seconds: number;
  /** Coarse bucket for the relative phrase. */
  unit: 'now' | 'minute' | 'hour' | 'day';
  /** The count for `unit` (e.g. 5 for "5 min ago"); 0 for the 'now' bucket. */
  value: number;
}

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Derive the age of a projection built at `builtAt` as of `nowMs`. Pure: the
 * ticking clock is injected so the helper is deterministic under test. */
export function ageParts(
  builtAt: string | null | undefined,
  nowMs: number,
): AgeParts {
  if (!builtAt) {
    return { unknown: true, builtAt: null, seconds: 0, unit: 'now', value: 0 };
  }
  const builtMs = Date.parse(builtAt);
  if (Number.isNaN(builtMs)) {
    return { unknown: true, builtAt: null, seconds: 0, unit: 'now', value: 0 };
  }
  // Clamp at 0 so a small clock skew never shows a negative age.
  const seconds = Math.max(0, Math.floor((nowMs - builtMs) / 1000));
  if (seconds < MINUTE) return { unknown: false, builtAt, seconds, unit: 'now', value: 0 };
  if (seconds < HOUR)
    return { unknown: false, builtAt, seconds, unit: 'minute', value: Math.floor(seconds / MINUTE) };
  if (seconds < DAY)
    return { unknown: false, builtAt, seconds, unit: 'hour', value: Math.floor(seconds / HOUR) };
  return { unknown: false, builtAt, seconds, unit: 'day', value: Math.floor(seconds / DAY) };
}

/** VZ1 — the evidence-closure node set for a selected claim node: the claim
 * itself plus its cited sources' graph nodes (`source:<sha256>`, filtered to
 * nodes actually present). ONLY while the citation detail hasn't arrived (or
 * the fetch failed, `citations === null`) does it fall back to direct graph
 * neighbors — once citations are known, neighbors must NOT stand in for
 * evidence: in the claim-perspective overview the neighbors are merely
 * `related` claims, and lighting them up would present relatedness as
 * provenance. Pure — vitest-covered. */
export function closureNodeIds(
  selectedId: string,
  citations: { source_sha256: string }[] | null,
  hasNode: (id: string) => boolean,
  adjacency: Map<string, Set<string>>,
): Set<string> {
  const out = new Set<string>([selectedId]);
  if (citations === null) {
    for (const n of adjacency.get(selectedId) ?? []) out.add(n);
    return out;
  }
  for (const c of citations) {
    const id = `source:${c.source_sha256}`;
    if (hasNode(id)) out.add(id);
  }
  return out;
}
