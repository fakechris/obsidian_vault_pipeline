/** Pure derivations over the /api/model IndexModel — everything the Today
 * and Library pages render is computed here so it stays testable and the
 * components stay dumb. */
import type {
  ClaimRow,
  IndexModel,
  PackRow,
  RunRow,
  SourceRow,
} from './types';

// ---------------------------------------------------------------- status dot

export type HealthLevel = 'ok' | 'attention' | 'failed';

/** Nav status dot: red when the most recent run failed, amber when operator
 * attention is pending (blocked / needs-content sources), green otherwise. */
export function healthLevel(model: IndexModel): HealthLevel {
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

/** Sample of claims for the Today page. ClaimRow has no date, its run_id
 * namespace (`run-*`) does not join to RunRow (`daily-*`), AND the index
 * projection sorts claims by (claim_id, claim) — so no recency order is
 * derivable in B1 (codex review P2). Present a durable-first sample and
 * label it as such; true "crystallized today" needs a date/run join key
 * on ClaimRow (B2 read-model change). */
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
}

export function filterSources(
  sources: SourceRow[],
  filter: LibraryFilter,
): SourceRow[] {
  return sources.filter(
    (s) =>
      (filter.collection === null || collectionOf(s) === filter.collection) &&
      (filter.month === null || monthOf(s) === filter.month) &&
      (filter.status === null || s.status === filter.status),
  );
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

export function countBy<T, K>(items: T[], key: (item: T) => K): Map<K, number> {
  const counts = new Map<K, number>();
  for (const item of items) {
    const k = key(item);
    counts.set(k, (counts.get(k) ?? 0) + 1);
  }
  return counts;
}
