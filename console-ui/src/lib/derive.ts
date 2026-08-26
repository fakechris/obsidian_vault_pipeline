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
  /** Heartbeat run id (e.g. daily-2026-08-01). */
  runId: string | null;
  /** Absolute wall times from the heartbeat (UTC RFC3339 as stored). */
  startedAt: string | null;
  endedAt: string | null;
}

/** Format a UTC RFC3339 (or local schedule) stamp for UI: local wall clock
 * `YYYY-MM-DD HH:mm` (or with seconds when `withSeconds`). Empty → ''. */
export function formatRunWhen(
  iso: string | null | undefined,
  opts: { withSeconds?: boolean } = {},
): string {
  if (!iso) return '';
  const withSeconds = opts.withSeconds === true;
  const sliceEnd = withSeconds ? 19 : 16;
  // Schedule timestamps are local naive `YYYY-MM-DDTHH:MM:SS` without Z —
  // show digits as-is so we do not shift a 09:00 schedule into the previous
  // evening via UTC parse.
  if (
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(iso) &&
    !iso.endsWith('Z') &&
    !/[+-]\d{2}:\d{2}$/.test(iso)
  ) {
    return iso.replace('T', ' ').slice(0, sliceEnd);
  }
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) {
    return iso.replace('T', ' ').replace(/Z$/, ' UTC');
  }
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, '0');
  const base = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return withSeconds ? `${base}:${pad(d.getSeconds())}` : base;
}

/** Infer which daily phase a heartbeat error points at (best-effort). */
export type FailPhase =
  | 'pinboard'
  | 'intake'
  | 'enrich'
  | 'reader'
  | 'index'
  | 'unknown';

export function failPhaseFromError(error: string | null | undefined): FailPhase {
  if (!error) return 'unknown';
  const e = error.toLowerCase();
  if (e.includes('pinboard')) return 'pinboard';
  if (e.includes('intake') || e.includes('sweep')) return 'intake';
  if (e.includes('web-fetch') || e.includes('github') || e.includes('enrich')) return 'enrich';
  if (e.includes('reader') || e.includes('unit') || e.includes('card') || e.includes('llm')) {
    return 'reader';
  }
  if (e.includes('index') || e.includes('console')) return 'index';
  return 'unknown';
}

/** One step in the operator-facing run timeline (absolute times preferred). */
export interface TimelineStep {
  id: string;
  /** Absolute local wall time, or null when not yet known. */
  when: string | null;
  /** short status: done | fail | skip | pending | running */
  kind: 'done' | 'fail' | 'skip' | 'pending' | 'running';
  /** i18n key or already-resolved label filled by the component. */
  labelKey: string;
  /** Extra vars for the label. */
  vars?: Record<string, string | number>;
}

/**
 * Build an ordered timeline from the heartbeat (+ optional schedule job).
 * Pure — components only render. Explains "why no per-source feed" when the
 * run died before the reader phase.
 */
export function buildRunTimeline(
  lr: LastRunModel | null | undefined,
  schedule?: {
    last_run?: string;
    last_status?: string;
    next_run?: string | null;
    due?: boolean;
    cadence?: string;
  } | null,
): TimelineStep[] {
  const steps: TimelineStep[] = [];
  if (!lr) {
    if (schedule?.next_run) {
      steps.push({
        id: 'next',
        when: formatRunWhen(schedule.next_run, { withSeconds: true }),
        kind: schedule.due ? 'pending' : 'pending',
        labelKey: schedule.due ? 'timeline.nextDueNow' : 'timeline.nextDue',
        vars: { cadence: schedule.cadence ?? 'daily' },
      });
    }
    return steps;
  }

  if (schedule?.last_run) {
    steps.push({
      id: 'sched-stamp',
      when: formatRunWhen(schedule.last_run, { withSeconds: true }),
      kind: schedule.last_status === 'error' ? 'fail' : 'done',
      labelKey: 'timeline.schedStamp',
      vars: { status: schedule.last_status ?? '—' },
    });
  }

  steps.push({
    id: 'start',
    when: formatRunWhen(lr.started_at, { withSeconds: true }),
    kind: lr.status === 'running' ? 'running' : 'done',
    labelKey: 'timeline.started',
    vars: { runId: lr.run_id },
  });

  const phase = failPhaseFromError(lr.error);
  if (lr.status === 'failed' || lr.status === 'aborted') {
    const phaseKey =
      phase === 'pinboard'
        ? 'timeline.failPinboard'
        : phase === 'intake'
          ? 'timeline.failIntake'
          : phase === 'enrich'
            ? 'timeline.failEnrich'
            : phase === 'reader'
              ? 'timeline.failReader'
              : phase === 'index'
                ? 'timeline.failIndex'
                : 'timeline.failUnknown';
    steps.push({
      id: 'fail',
      when: formatRunWhen(lr.ended_at ?? lr.started_at, { withSeconds: true }),
      kind: 'fail',
      labelKey: phaseKey,
      vars: { error: lr.error ?? '' },
    });
    // Explicit "what did not run" for early-phase deaths.
    if (phase === 'pinboard' || phase === 'intake' || phase === 'enrich') {
      steps.push({
        id: 'skipped-reader',
        when: null,
        kind: 'skip',
        labelKey: 'timeline.skippedReader',
      });
    }
    if ((lr.processed ?? 0) === 0 && (lr.failed ?? 0) === 0 && !(lr.recent && lr.recent.length)) {
      steps.push({
        id: 'no-sources',
        when: null,
        kind: 'skip',
        labelKey: 'timeline.noSourceFeed',
      });
    }
  } else if (lr.status === 'completed') {
    steps.push({
      id: 'end',
      when: formatRunWhen(lr.ended_at ?? lr.started_at, { withSeconds: true }),
      kind: 'done',
      labelKey: 'timeline.completed',
      vars: {
        ok: lr.processed ?? 0,
        failed: lr.failed ?? 0,
        queued: lr.queued_after ?? 0,
      },
    });
  } else if (lr.status === 'running') {
    steps.push({
      id: 'running',
      when: formatRunWhen(lr.started_at, { withSeconds: true }),
      kind: 'running',
      labelKey:
        lr.processed_so_far != null && lr.total_planned != null
          ? 'timeline.runningProgress'
          : 'timeline.running',
      vars: {
        done: lr.processed_so_far ?? 0,
        total: lr.total_planned ?? 0,
        current: lr.current ? ` · ${lr.current}` : '',
      },
    });
  }

  // A failed run does NOT re-arm the schedule: `is_due` compares last_run to the
  // most recent occurrence and ignores last_status (ovp-scheduler/src/lib.rs),
  // so this failure already consumed the current window. "Next schedule window"
  // on its own reads as "it will fix itself" — say the quiet part out loud.
  if (
    (lr.status === 'failed' || lr.status === 'aborted') &&
    schedule &&
    schedule.due === false
  ) {
    steps.push({
      id: 'no-auto-retry',
      when: null,
      kind: 'skip',
      labelKey: 'timeline.noAutoRetry',
      vars: {
        next: formatRunWhen(schedule.next_run, { withSeconds: true }) || '—',
      },
    });
  }

  if (schedule?.next_run) {
    steps.push({
      id: 'next',
      when: formatRunWhen(schedule.next_run, { withSeconds: true }),
      kind: schedule.due ? 'pending' : 'pending',
      labelKey: schedule.due ? 'timeline.nextDueNow' : 'timeline.nextDue',
      vars: { cadence: schedule.cadence ?? 'daily' },
    });
  }

  return steps;
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
      runId: null,
      startedAt: null,
      endedAt: null,
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
    runId: lr.run_id ?? null,
    startedAt: lr.started_at ?? null,
    endedAt: lr.ended_at ?? null,
  };
}

// ------------------------------------------------------------ retry pending

/** How long a pending manual retry may sit before the button re-arms itself.
 * A retry whose child dies before it writes a heartbeat would otherwise strand
 * the only recovery control the operator has. */
export const RETRY_WATCHDOG_MS = 90_000;

/** Identity of the run the banner is showing. Same-day retries REUSE `run_id`
 * (`daily-2026-08-03`), so the start instant is the part that actually moves
 * when a new attempt begins — both halves are required to spot a new run. */
export function runSignature(banner: LastRunBanner): string {
  return `${banner.runId ?? '-'}|${banner.startedAt ?? '-'}`;
}

export interface RetryPending {
  /** The run signature the operator clicked Retry from. */
  from: string;
  /** When the click happened (ms since epoch), for the watchdog. */
  atMs: number;
}

/** Should the pending "Starting…" state clear?
 *
 * Watching only `level !== 'failed'` strands the button forever when the retry
 * ALSO fails: the level keeps the same VALUE, so a level-keyed effect never
 * re-fires and the operator is left holding a dead control with no error. Clear
 * on any of: the banner left `failed`, a NEW run appeared (signature moved), or
 * the watchdog expired. */
export function shouldClearRetry(
  pending: RetryPending | null,
  banner: LastRunBanner,
  nowMs: number,
  watchdogMs: number = RETRY_WATCHDOG_MS,
): boolean {
  if (!pending) return false;
  if (banner.level !== 'failed') return true;
  if (runSignature(banner) !== pending.from) return true;
  return nowMs - pending.atMs >= watchdogMs;
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
  // Count from the rows (not the baked totals) so acknowledged items drop
  // out of the nav dot / Today counter consistently with the lists.
  return attentionSources(model).length;
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
  const acked = ackedSet(model);
  return model.sources.filter(
    (s) =>
      (s.status === 'blocked' || s.status === 'needs_content') &&
      !acked.has(`${s.sha256}\u0000${s.status}`),
  );
}

/** Acknowledged (sha,status) pairs from the live-server overlay. An ack is
 * status-scoped: a needs-content source the operator dismissed re-surfaces
 * if it later BLOCKS (different status = different problem). */
function ackedSet(model: IndexModel): Set<string> {
  return new Set(
    (model.attention_acks ?? []).map((a) => `${a.sha}\u0000${a.status}`),
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

// --------------------------------------------------------------- day browser

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

/** True when `s` is a calendar day `YYYY-MM-DD`. */
export function isIsoDay(s: string | null | undefined): s is string {
  return typeof s === 'string' && ISO_DAY.test(s);
}

/** Best pack activity day: explicit field, else first `YYYY-MM-DD` in pack_dir. */
export function packActivityDate(pack: PackRow): string | null {
  if (isIsoDay(pack.date)) return pack.date;
  const m = pack.pack_dir.match(/(20\d{2}-\d{2}-\d{2})/);
  return m?.[1] ?? null;
}

/**
 * Best-effort day a claim was produced (axis B). Prefer explicit `run_date`;
 * else parse `run_id`. Never invent.
 */
export function claimActivityDate(claim: ClaimRow): string | null {
  if (isIsoDay(claim.run_date)) return claim.run_date;
  const rid = claim.run_id ?? '';
  const dashed = rid.match(/(20\d{2}-\d{2}-\d{2})/);
  if (dashed) return dashed[1];
  const compact = rid.match(/(20\d{6})/);
  if (compact) {
    const s = compact[1];
    return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;
  }
  return null;
}

/** Axis A: content/capture day for a source (published / bookmark / filename). */
export function sourceContentDate(s: {
  content_date?: string;
  date?: string;
}): string | null {
  if (isIsoDay(s.content_date)) return s.content_date;
  return null;
}

/** Axis B: last pipeline day for a source (processed → captured → legacy date). */
export function sourcePipelineDate(s: {
  processed_on?: string;
  captured_on?: string;
  date?: string;
}): string | null {
  if (isIsoDay(s.processed_on)) return s.processed_on;
  if (isIsoDay(s.captured_on)) return s.captured_on;
  if (isIsoDay(s.date)) return s.date;
  return null;
}

/** All ISO days that have any known vault activity (for calendar dots). */
export function activityDates(model: IndexModel): Set<string> {
  const days = new Set<string>();
  for (const r of model.runs) {
    if (isIsoDay(r.date)) days.add(r.date);
  }
  for (const s of model.sources) {
    const a = sourceContentDate(s);
    if (a) days.add(a);
    const b = sourcePipelineDate(s);
    if (b) days.add(b);
  }
  for (const p of model.packs) {
    const d = packActivityDate(p);
    if (d) days.add(d);
  }
  for (const c of model.claims) {
    const d = claimActivityDate(c);
    if (d) days.add(d);
  }
  if (isIsoDay(model.date)) days.add(model.date);
  return days;
}

/** Shift an ISO day by `delta` calendar days (local arithmetic on Y-M-D). */
export function shiftIsoDay(day: string, delta: number): string {
  const [y, m, d] = day.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + delta));
  const yy = dt.getUTCFullYear();
  const mm = String(dt.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(dt.getUTCDate()).padStart(2, '0');
  return `${yy}-${mm}-${dd}`;
}

/** First day of the month containing `day` (YYYY-MM-01). */
export function monthStart(day: string): string {
  return `${day.slice(0, 7)}-01`;
}

export interface DayView {
  date: string;
  /** True when `date === model.date` (the projection's build day). */
  isProjectionDay: boolean;
  runs: RunRow[];
  captured: number;
  capturedPinboard: number;
  read: number;
  readUnits: number;
  readCards: number;
  /**
   * Axis A — sources whose **content** day is this day
   * (`content_date`: published / bookmark / filename).
   */
  sourcesDated: SourceRow[];
  /**
   * Axis B — sources **processed by a pipeline run** that ran on this day
   * (run date via `last_run_id` ∈ day's runs).
   */
  sourcesRead: ReadSource[];
  /** Reader packs whose pipeline day is this day (pack dir / pack.date = B). */
  packs: PackRow[];
  /** Claims with axis-B run day for this day. */
  claims: ClaimRow[];
  claimsDurable: number;
  claimsCaveated: number;
  /** Intensity 0–3 for calendar heat (none / light / med / heavy). */
  heat: 0 | 1 | 2 | 3;
}

function dayHeat(captured: number, read: number, claims: number): 0 | 1 | 2 | 3 {
  const n = captured + read + claims;
  if (n <= 0) return 0;
  if (n < 5) return 1;
  if (n < 20) return 2;
  return 3;
}

/** Full multi-dimension view of one calendar day from the index projection. */
export function dayView(model: IndexModel, date: string): DayView {
  const day = isIsoDay(date) ? date : model.date;
  const runs = model.runs.filter((r) => r.date === day);
  const runIds = new Set(runs.map((r) => r.run_id));
  const packByDir = new Map(model.packs.map((p) => [p.pack_dir, p]));

  // Axis A: content day. Fall back to legacy `date` only when content_date is
  // absent AND the source was never processed (legacy indexes mixed A into
  // date via intake path names). Prefer null over inventing for processed
  // rows — their legacy date is B.
  const sourcesDated = model.sources
    .filter((s) => {
      const a = sourceContentDate(s);
      if (a) return a === day;
      // Pre-axis indexes: only use legacy date when no processed_on signal.
      if (!s.processed_on && !s.captured_on && isIsoDay(s.date)) {
        return s.date === day;
      }
      return false;
    })
    .sort((a, b) => (a.title ?? '').localeCompare(b.title ?? ''));

  // Axis B: prefer explicit pipeline day; also accept last_run_id ∈ day's
  // runs (covers pre-axis indexes). Corpus backfills have processed_on from
  // the pack dir and no last_run_id.
  const sourcesRead = model.sources
    .filter((s) => {
      if (s.status !== 'processed') return false;
      if (sourcePipelineDate(s) === day) return true;
      return s.last_run_id != null && runIds.has(s.last_run_id);
    })
    .map((source) => ({
      source,
      pack: source.pack_dir ? packByDir.get(source.pack_dir) : undefined,
    }))
    .sort((a, b) => (a.source.title ?? '').localeCompare(b.source.title ?? ''));

  const packs = model.packs
    .filter((p) => packActivityDate(p) === day)
    .sort((a, b) => a.title.localeCompare(b.title));

  const claims = model.claims
    .filter((c) => claimActivityDate(c) === day)
    .filter((c) => c.status === 'durable' || c.status === 'caveated')
    .sort((a, b) => {
      const ra = a.status === 'durable' ? 0 : 1;
      const rb = b.status === 'durable' ? 0 : 1;
      return ra - rb || a.claim.localeCompare(b.claim);
    });

  const captured = runs.reduce((n, r) => n + r.ingested, 0);
  const capturedPinboard = runs.reduce((n, r) => n + r.pinboard_new, 0);
  // Prefer run counters when present; fall back to source lists so a day
  // with dated packs still shows activity without a run row.
  const readFromRuns = runs.reduce((n, r) => n + r.succeeded, 0);
  const read = readFromRuns > 0 ? readFromRuns : sourcesRead.length;

  return {
    date: day,
    isProjectionDay: day === model.date,
    runs,
    captured,
    capturedPinboard,
    read,
    readUnits: sourcesRead.reduce((n, s) => n + (s.pack?.units ?? 0), 0),
    readCards: sourcesRead.reduce((n, s) => n + (s.pack?.cards ?? 0), 0),
    sourcesDated,
    sourcesRead,
    packs,
    claims,
    claimsDurable: claims.filter((c) => c.status === 'durable').length,
    claimsCaveated: claims.filter((c) => c.status === 'caveated').length,
    heat: dayHeat(captured, read, claims.length),
  };
}

/** Heat map for every day in a calendar month (`YYYY-MM`). */
export function monthHeat(
  model: IndexModel,
  yearMonth: string,
): Map<string, 0 | 1 | 2 | 3> {
  const out = new Map<string, 0 | 1 | 2 | 3>();
  const prefix = yearMonth.slice(0, 7);
  for (const day of activityDates(model)) {
    if (!day.startsWith(prefix)) continue;
    out.set(day, dayView(model, day).heat);
  }
  return out;
}

// ------------------------------------------------------------------ library

/**
 * Operator-facing title for a source row.
 * Prefer indexed title; else filename stem from rel_path (duplicates often
 * land with null title because intake parked them without parsing frontmatter);
 * last resort short sha — never a bare 64-char wall as the only chrome label.
 */
export function sourceDisplayTitle(source: {
  title?: string | null;
  rel_path?: string | null;
  sha256: string;
}): string {
  const t = source.title?.trim();
  if (t) return t;
  const path = source.rel_path?.replace(/\\/g, '/') ?? '';
  const base = path.split('/').pop() ?? '';
  const stem = base.replace(/\.md$/i, '').trim();
  if (stem) {
    // "Author - Title" clipping names → keep full stem (still human-readable)
    return stem;
  }
  return source.sha256.length > 12
    ? `${source.sha256.slice(0, 12)}…`
    : source.sha256;
}

export type Collection = 'clippings' | 'pinboard' | 'capture';

/** Collection = capture origin when the index knows it (URL-matched against
 * the pinboard ledger at build, so enrichment re-hashes and lifecycle moves
 * can't hide a source); falls back to the vault-path heuristic for rows the
 * ledger doesn't know (design §3.2). */
export function collectionOf(source: SourceRow): Collection {
  if (source.origin === 'pinboard') return 'pinboard';
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
        (s.tags_inferred ?? []).includes(filter.tag) ||
        (s.tags_implied ?? []).includes(filter.tag)),
  );
}

/** Flattened browse order matching the Library list (month desc, date desc). */
export function libraryBrowseOrder(
  sources: SourceRow[],
  filter: LibraryFilter,
): string[] {
  return groupByMonth(filterSources(sources, filter)).flatMap((g) =>
    g.sources.map((s) => s.sha256),
  );
}

/** Parse Library filter facets from a source-detail or library URL query. */
export function libraryFilterFromSearch(
  params: URLSearchParams | { get: (k: string) => string | null },
): LibraryFilter {
  const c = params.get('c');
  const collection =
    c === 'clippings' || c === 'pinboard' || c === 'capture' ? c : null;
  return {
    collection,
    month: params.get('m'),
    status: params.get('status'),
    tag: params.get('tag'),
  };
}

/** True when any Library facet is active (enables in-filter prev/next). */
export function libraryFilterActive(f: LibraryFilter): boolean {
  return Boolean(f.collection || f.month || f.status || f.tag);
}

/** Carry Library facets onto a source detail URL for continuous browsing. */
export function librarySourcePath(
  sha: string,
  filter: LibraryFilter,
  extra?: Record<string, string | null | undefined>,
): string {
  const q = new URLSearchParams();
  if (filter.collection) q.set('c', filter.collection);
  if (filter.month) q.set('m', filter.month);
  if (filter.status) q.set('status', filter.status);
  if (filter.tag) q.set('tag', filter.tag);
  if (extra) {
    for (const [k, v] of Object.entries(extra)) {
      if (v == null || v === '') q.delete(k);
      else q.set(k, v);
    }
  }
  const qs = q.toString();
  return qs
    ? `/library/${encodeURIComponent(sha)}?${qs}`
    : `/library/${encodeURIComponent(sha)}`;
}

/** sessionStorage key: last Library browse order for prev/next when the
 * detail URL has no facet query (e.g. opened from Today then user still
 * wants sequential browse of what they last filtered). */
export const LIBRARY_NAV_STORAGE_KEY = 'ovp.libraryNav';

export interface LibraryNavSnapshot {
  order: string[];
  filter: LibraryFilter;
  /** Human-readable filter summary for the nav chrome. */
  label?: string;
}

export function saveLibraryNavSnapshot(snap: LibraryNavSnapshot): void {
  try {
    sessionStorage.setItem(LIBRARY_NAV_STORAGE_KEY, JSON.stringify(snap));
  } catch {
    /* private mode / quota */
  }
}

export function loadLibraryNavSnapshot(): LibraryNavSnapshot | null {
  try {
    const raw = sessionStorage.getItem(LIBRARY_NAV_STORAGE_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as LibraryNavSnapshot;
    if (!v || !Array.isArray(v.order)) return null;
    return v;
  } catch {
    return null;
  }
}

/** Tag → source count over the whole library (operator + inferred + implied
 * roll-up — the facet filters on all three), count desc then name. */
export function countTags(sources: SourceRow[]): [string, number][] {
  const counts = new Map<string, number>();
  const bump = (t: string) => counts.set(t, (counts.get(t) ?? 0) + 1);
  for (const s of sources) {
    for (const t of s.tags ?? []) bump(t);
    for (const t of s.tags_inferred ?? []) bump(t);
    for (const t of s.tags_implied ?? []) bump(t);
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
  /** Stable community id — the routing key (`/knowledge/theme/:id`). Null
   * only on pre-theme projections, where the wall falls back to `theme`
   * (label) routing. */
  id: number | null;
  /** Display label (mutable under `crystal-themes` relabel). */
  theme: string;
  /** Rebuildable Chinese label when the projection carries one. */
  label_zh?: string;
  total: number;
  durable: number;
  caveated: number;
  /** Distinct sources (case ids) across the theme's active claims — a theme
   * with many claims but sources=1 rests on a single document. */
  sources: number;
  /** First durable (else first caveated) claim text — the wall snippet. */
  topClaim?: string;
  /** B-axis day of the theme's earliest / latest claim (`claimDay`), from
   * the claims this projection indexes. Absent when no claim has a date —
   * the wall then shows no time line rather than a false one. */
  firstSeen?: string | null;
  lastSeen?: string | null;
}

/** The crystal themes.json Unclassified sentinel — packs the clustering left
 * unassigned. Routed as `/knowledge/theme/-1`. */
export const UNCLASSIFIED_ID = -1;

/** A parsed `/knowledge/theme/:theme` route key. The portal routes by stable
 * community id; a non-numeric param is a LEGACY label URL (pre-id, or a
 * bookmark from before the id-routing switch) and is resolved/redirected by
 * the detail page via `/api/themes`. */
export type ThemeRouteKey =
  | { kind: 'id'; id: number }
  | { kind: 'label'; label: string };

/** The synthesizer's fallback bucket — sources that matched no keyword
 * bucket land under 'misc' (key) / 'Miscellaneous' (description). The
 * portal displays it honestly as "Unclassified" — DISPLAY LAYER ONLY: keys,
 * URLs and index data keep the literal value. */
export function isMiscTheme(theme: string | null | undefined): boolean {
  // '' / nullish is the "no theme" bucket — display it as Unclassified too,
  // so graph clicks and the wall card on unthemed claims read honestly.
  return theme == null || theme === '' || theme === 'misc' || theme === 'Miscellaneous';
}

/** Route segment for the "no theme" bucket ('' theme key) on LEGACY label
 * routes. Id routes use the numeric `-1` (UNCLASSIFIED_ID) directly. */
export const UNTHEMED_SEGMENT = '~none';

/** Theme key → `/knowledge/theme/...` route. Prefers the stable community
 * `id` (survives a relabel); falls back to the label for pre-theme
 * projections (`id === null`), encoding the empty bucket as `~none`. */
export function themeRoute(key: {
  id: number | null;
  theme: string;
}): string {
  if (key.id != null) return `/knowledge/theme/${key.id}`;
  const label = key.theme ?? '';
  return `/knowledge/theme/${label === '' ? UNTHEMED_SEGMENT : encodeURIComponent(label)}`;
}

/** Inverse of {@link themeRoute} for a decoded `:theme` route param. Numeric
 * (incl. `-1`) → id key; `~none` or a non-numeric string → legacy label key. */
export function themeFromRoute(param: string | null | undefined): ThemeRouteKey {
  if (param == null || param === UNTHEMED_SEGMENT) return { kind: 'label', label: '' };
  if (/^-?\d+$/.test(param)) return { kind: 'id', id: Number(param) };
  return { kind: 'label', label: param };
}

/** Does a claim belong to the route key? Id key → `theme_id` match (so a
 * relabel that kept the community id still resolves); label key → legacy
 * exact-string match against `theme`. */
export function claimMatchesThemeKey(c: ClaimRow, key: ThemeRouteKey): boolean {
  if (key.kind === 'id') return c.theme_id === key.id;
  return (c.theme ?? '') === key.label;
}

/** Active claims only — the knowledge surface never lists superseded or
 * retracted claims (they remain reachable through the ledger/CLI). */
export function activeClaims(claims: ClaimRow[]): ClaimRow[] {
  return claims.filter(
    (c) => c.status === 'durable' || c.status === 'caveated',
  );
}

/** Stable group key for a claim / a ledger theme entry: id when present,
 * else the label. Two communities that share a display label but have
 * distinct ids stay separate (the bug that routing-by-label created). */
function themeGroupKey(id: number | null | undefined, label: string): string {
  return id != null ? `i${id}` : `t${label}`;
}

/** Theme wall from /api/model claims + /api/themes: ledger themes keep the
 * ledger order (count desc); index-only themes append after. Groups are
 * keyed by the STABLE community id when present (so a relabel doesn't split
 * a community into two cards); pre-theme entries fall back to the label. */
export function themeWall(
  claims: ClaimRow[],
  ledgerThemes: {
    id?: number | null;
    theme: string;
    count: number;
    label_zh?: string;
  }[],
  // Optional case-id → canonical-identity map (see `caseCanonicalIds`):
  // collapses re-captures of the same document so `sources` counts
  // INDEPENDENT documents, not vault copies. Absent = raw case ids.
  canonical?: Map<string, string>,
  // Optional case-id → B-axis day map (see `sourceDaysByCase`): the wall's
  // created/updated times. Absent = no time line on the cards.
  sourceDays?: Map<string, string> | null,
): ThemeGroup[] {
  const groups = new Map<string, ThemeGroup>();
  const caseSets = new Map<string, Set<string>>();
  const ensure = (id: number | null, theme: string, label_zh?: string): ThemeGroup => {
    const key = themeGroupKey(id, theme);
    let g = groups.get(key);
    if (!g) {
      g = { id: id ?? null, theme, label_zh, total: 0, durable: 0, caveated: 0, sources: 0 };
      groups.set(key, g);
      caseSets.set(key, new Set());
    }
    return g;
  };
  for (const t of ledgerThemes) {
    ensure(t.id ?? null, t.theme, t.label_zh);
  }
  for (const c of activeClaims(claims)) {
    const g = ensure(c.theme_id ?? null, c.theme ?? '');
    for (const s of c.sources) caseSets.get(themeGroupKey(g.id, g.theme))?.add(canonical?.get(s) ?? s);
    const day = claimDay(c, sourceDays ?? null);
    if (day) {
      if (g.firstSeen == null || day < g.firstSeen) g.firstSeen = day;
      if (g.lastSeen == null || day > g.lastSeen) g.lastSeen = day;
    }
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
    const g = groups.get(themeGroupKey(t.id ?? null, t.theme));
    // Ledger and index normally agree; when they drift mid-run, show the
    // larger count rather than hiding claims.
    if (g) g.total = Math.max(t.count, g.durable + g.caveated);
  }
  for (const g of groups.values()) {
    g.total = Math.max(g.total, g.durable + g.caveated);
    g.sources = caseSets.get(themeGroupKey(g.id, g.theme))?.size ?? 0;
  }
  return [...groups.values()].sort(
    (a, b) => b.total - a.total || a.theme.localeCompare(b.theme),
  );
}

/** case id → canonical source identity for DISTINCT-source counting: the
 * source URL when present (re-captures of the same article get different
 * content shas but share the URL — 58 such pairs in the live vault when this
 * shipped), else the content sha, else the case id itself. */
export function caseCanonicalIds(model: {
  sources: { sha256: string; url?: string | null }[];
  packs: { pack_dir: string; source_sha256?: string | null }[];
}): Map<string, string> {
  const bySha = new Map(model.sources.map((s) => [s.sha256, s]));
  const out = new Map<string, string>();
  for (const p of model.packs) {
    const caseId = p.pack_dir.split(/[/\\]/).filter(Boolean).pop();
    if (!caseId) continue;
    const src = p.source_sha256 ? bySha.get(p.source_sha256) : undefined;
    out.set(caseId, src?.url?.trim() || p.source_sha256 || caseId);
  }
  return out;
}

/** Theme-card source badge: null hides it. `sources` counts only INDEXED
 * claims, so 0 means "unknown" (ledger/index drift or a ledger-only theme) —
 * omit rather than show a false zero. `single` flags a multi-claim theme
 * resting on one document (deserves extra scrutiny). */
export function themeSourceBadge(group: {
  sources: number;
  durable: number;
  caveated: number;
}): { n: number; single: boolean } | null {
  if (group.sources <= 0) return null;
  const active = group.durable + group.caveated;
  return { n: group.sources, single: group.sources === 1 && active > 1 };
}

/** Failure strip for a schedule job on the System automation panel — null
 * means no strip. Legacy state (pre-counter) reports 0 everywhere; the error
 * status alone proves at least one failure, so the streak floors at 1 and
 * lifetime counts show only when actually recorded (`runs_total > 0`). */
export interface ScheduleFailureStrip {
  streak: number;
  /** 'noRetry' = enabled job waiting for its next window (is_due never
   * retries); 'disabled' = will not run again until re-enabled. */
  noteKey: 'noRetry' | 'disabled';
  counts: { fails: number; runs: number } | null;
}
export function scheduleFailureStrip(job: {
  last_status: string;
  enabled: boolean;
  consecutive_failures?: number;
  failures_total?: number;
  runs_total?: number;
}): ScheduleFailureStrip | null {
  if (job.last_status !== 'error') return null;
  return {
    streak: Math.max(1, job.consecutive_failures ?? 1),
    noteKey: job.enabled ? 'noRetry' : 'disabled',
    counts:
      (job.runs_total ?? 0) > 0
        ? { fails: job.failures_total ?? 0, runs: job.runs_total ?? 0 }
        : null,
  };
}

/** Targeted fix hint for an ask model_error, keyed by the server's
 * failure_class slug. Unactionable classes (decode/protocol/internal/…)
 * return null — the generic stop note already covers them. Keys live in the
 * i18n catalogs as `ask.fail.*`. */
export function failureHintKey(
  cls: string | null | undefined,
):
  | 'ask.fail.auth'
  | 'ask.fail.rateLimited'
  | 'ask.fail.contextExceeded'
  | 'ask.fail.budgetExhausted'
  | 'ask.fail.overloaded'
  | 'ask.fail.network'
  | null {
  switch (cls) {
    case 'auth':
      return 'ask.fail.auth';
    case 'rate_limited':
      return 'ask.fail.rateLimited';
    case 'context_exceeded':
      return 'ask.fail.contextExceeded';
    case 'budget_exhausted':
      return 'ask.fail.budgetExhausted';
    case 'overloaded':
      return 'ask.fail.overloaded';
    case 'network':
      return 'ask.fail.network';
    default:
      return null;
  }
}

/** Distinct themes the source's citing claims land in — the source page's
 * "supports this crystal knowledge" rail. Count = active citing claims in
 * that theme; theme order follows count desc then name. Each entry carries
 * the stable community `id` (when present) so the rail links by id. */
export function sourceThemes(
  citing: ClaimRow[],
): { id: number | null; theme: string; count: number }[] {
  const counts = new Map<string, { id: number | null; theme: string; count: number }>();
  for (const c of activeClaims(citing)) {
    const id = c.theme_id ?? null;
    const theme = c.theme ?? '';
    const key = themeGroupKey(id, theme);
    const entry = counts.get(key);
    if (entry) entry.count += 1;
    else counts.set(key, { id, theme, count: 1 });
  }
  return [...counts.values()].sort(
    (a, b) => b.count - a.count || a.theme.localeCompare(b.theme),
  );
}

/** Theme claims for the detail page: durable first, then caveated;
 * stable claim_id order within each band. Matches by stable id when the
 * route is an id route (survives a relabel), else by legacy label. */
export function themeClaims(claims: ClaimRow[], key: ThemeRouteKey): ClaimRow[] {
  const rank = (c: ClaimRow) => (c.status === 'durable' ? 0 : 1);
  return activeClaims(claims)
    .filter((c) => claimMatchesThemeKey(c, key))
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

/** The source's day on the B timeline (pipeline): last processed day, else
 * intake day, else the legacy date. ISO 'YYYY-MM-DD' — compares
 * lexicographically = chronologically. */
export function sourceDay(src: SourceRow): string | null {
  return src.processed_on ?? src.captured_on ?? src.date ?? null;
}

/** case id → B-axis day (`sourceDay` of the resolved source), for claims
 * whose own run_id carries no date. Claims' sources are pack case ids —
 * same resolution `sourcesByCase` uses. */
export function sourceDaysByCase(model: IndexModel): Map<string, string> {
  const out = new Map<string, string>();
  for (const [caseId, src] of sourcesByCase(model)) {
    const day = sourceDay(src);
    if (day) out.set(caseId, day);
  }
  return out;
}

/** The claim's day on the B timeline: the run that (re)wrote it to the
 * crystal ledger when `run_id` embeds one, else the NEWEST cited source's
 * pipeline day (evidence cannot post-date the claim; legacy undated runs
 * leave this the only honest signal). Null = no date anywhere. */
export function claimDay(
  claim: ClaimRow,
  sourceDays: Map<string, string> | null,
): string | null {
  if (claim.run_date) return claim.run_date;
  if (!sourceDays) return null;
  let best: string | null = null;
  for (const caseId of claim.sources) {
    const day = sourceDays.get(caseId);
    if (day && (best === null || day > best)) best = day;
  }
  return best;
}

/** UNIQUE per-row React keys for a claim list. `claim_id` can collide
 * across runs (the topic overview's ambiguous-id note) while `claim_key`
 * cannot — but legacy review rows lack claim_key, so `claim_key ?? claim_id`
 * still collides. Duplicate React keys BREAK reconciliation: when the list
 * is re-sorted the DOM order stays frozen in a stale arrangement while the
 * projected array reorders underneath (2026-08-17 "sort/filter has no
 * visible effect" on the theme detail page — filter worked, sorting looked
 * dead). Keys follow the list ORDER once: occurrence-suffixed (`id`, `id#2`
 * …), so each row object keeps a STABLE key across filter/sort (those keep
 * the same row objects, just reordered). */
export function uniqueClaimKeys(
  claims: ClaimRow[],
): WeakMap<ClaimRow, string> {
  const counts = new Map<string, number>();
  const keys = new WeakMap<ClaimRow, string>();
  for (const c of claims) {
    const base = c.claim_key ?? c.claim_id;
    const n = counts.get(base) ?? 0;
    counts.set(base, n + 1);
    keys.set(c, n === 0 ? base : `${base}#${n}`);
  }
  return keys;
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

/** Community anchors for the global knowledge graph (VZ, 2026-08-07).
 *
 * The force layout alone produced an ugly silhouette: a dense central
 * hairball (short strong links + centroid pull) with unaffiliated nodes
 * blasted to the far periphery by unopposed repulsion. Instead, every node
 * gets a HOME in an organic packed-cloud silhouette (the hand-drawn tree
 * reference the operator liked):
 *
 * - Communities are disks (radius ∝ sqrt(member count)) greedily packed on
 *   an archimedean spiral — largest at the center, no empty donut middle.
 * - Packing ORDER is affinity-greedy: after the largest, always the
 *   unplaced community most connected to the placed ones, so heavy
 *   cross-community chords stay short instead of spanning the layout.
 * - Unclustered nodes (cluster <= 0) get evenly spaced, id-sorted seats on
 *   a ring just outside the cloud — visible periphery, never strays.
 *
 * Deterministic and pure — vitest-covered; the graph component only seeds
 * positions and applies the pull force toward these anchors. */
export interface RadialAnchors {
  radius: number;
  byCluster: Map<number, { x: number; y: number }>;
  byId: Map<string, { x: number; y: number }>;
}

export function radialAnchors(
  clusterSizes: Map<number, number>,
  unclusteredIds: string[],
  affinity?: Map<number, Map<number, number>>,
  nodeRadius = 7,
): RadialAnchors {
  const byCluster = new Map<number, { x: number; y: number }>();
  const byId = new Map<string, { x: number; y: number }>();
  const entries = [...clusterSizes.entries()].filter(([, n]) => n > 0);
  // Blob footprint: disk radius grows with sqrt(member count) + padding.
  const blobR = (n: number) => nodeRadius * Math.sqrt(n) * 1.45 + 10;

  // AFFINITY-GREEDY placement order: largest first, then always the unplaced
  // community most connected (by cross-edge weight) to anything already
  // placed — the spiral packs consecutive picks adjacently, so the heavy
  // inter-community chords stay SHORT instead of spanning the silhouette.
  const remaining = new Map(entries);
  const order: [number, number][] = [];
  const first = entries.slice().sort((a, b) => b[1] - a[1] || a[0] - b[0])[0];
  if (first) {
    order.push(first);
    remaining.delete(first[0]);
    while (remaining.size > 0) {
      let best: [number, number] | null = null;
      let bestScore = -1;
      for (const [c, n] of remaining) {
        let score = 0;
        for (const [placed] of order) {
          score += affinity?.get(c)?.get(placed) ?? 0;
        }
        // Ties (incl. no affinity data at all) fall back to size desc, id asc.
        if (
          best === null ||
          score > bestScore ||
          (score === bestScore && (n > best[1] || (n === best[1] && c < best[0])))
        ) {
          best = [c, n];
          bestScore = score;
        }
      }
      order.push(best!);
      remaining.delete(best![0]);
    }
  }

  // Greedy spiral packing: largest at the origin, each next community walks
  // an archimedean spiral to the first spot clear of every placed disk —
  // compact organic cloud, no empty donut middle.
  const placed: { c: number; x: number; y: number; r: number }[] = [];
  for (const [c, n] of order) {
    const r = blobR(n);
    if (placed.length === 0) {
      placed.push({ c, x: 0, y: 0, r });
      byCluster.set(c, { x: 0, y: 0 });
      continue;
    }
    let theta = 0;
    for (;;) {
      theta += 0.22;
      const rad = 6 * theta;
      const x = rad * Math.cos(theta);
      const y = rad * Math.sin(theta);
      if (placed.every((p) => Math.hypot(x - p.x, y - p.y) >= r + p.r)) {
        placed.push({ c, x, y, r });
        byCluster.set(c, { x, y });
        break;
      }
    }
  }

  const extent = placed.reduce((m, p) => Math.max(m, Math.hypot(p.x, p.y) + p.r), 0);
  const radius = Math.max(60, extent);
  // Unclustered nodes: deterministic seats on a ring just OUTSIDE the packed
  // cloud — visible periphery, never repulsion-flung strays.
  const outer = radius + 26;
  const ids = [...unclusteredIds].sort();
  for (let i = 0; i < ids.length; i++) {
    const a = ((i + 0.5) / Math.max(1, ids.length)) * 2 * Math.PI;
    byId.set(ids[i], { x: outer * Math.cos(a), y: outer * Math.sin(a) });
  }
  return { radius, byCluster, byId };
}

/** Centroid + bounding radius of a 3D fly-to target — the camera distance
 * must come from the SPATIAL extent of the selected nodes (two far-apart
 * clusters, or one wide cluster, must both fit the viewport), never from
 * the cluster count. Pure — vitest-covered. */
export function focusBounds(
  pts: { x?: number; y?: number; z?: number }[],
): { x: number; y: number; z: number; radius: number } | null {
  if (pts.length === 0) return null;
  let cx = 0;
  let cy = 0;
  let cz = 0;
  for (const p of pts) {
    cx += p.x ?? 0;
    cy += p.y ?? 0;
    cz += p.z ?? 0;
  }
  cx /= pts.length;
  cy /= pts.length;
  cz /= pts.length;
  let radius = 0;
  for (const p of pts) {
    radius = Math.max(
      radius,
      Math.hypot((p.x ?? 0) - cx, (p.y ?? 0) - cy, (p.z ?? 0) - cz),
    );
  }
  return { x: cx, y: cy, z: cz, radius };
}

/** One knowledge-graph legend row — possibly MERGING several graph clusters.
 * Louvain clustering is finer-grained than the theme taxonomy, so distinct
 * clusters often share a dominant-theme label ('Agent Harness Architecture'
 * ×4 on the live vault); one row per label matches the reader's mental model,
 * and a click frames ALL of that label's clusters. */
export interface LegendRow {
  /** Cluster ids sharing the label, largest first — ids[0] drives the dot. */
  ids: number[];
  label: string;
  /** Summed member count across the merged clusters. */
  size: number;
}

/** Merge same-label communities into legend rows, total size desc. Pure. */
export function groupCommunities(
  communities: { id: number; label: string; size: number }[],
): LegendRow[] {
  const byLabel = new Map<string, LegendRow>();
  // Size-desc walk (stable id tie-break) ENFORCES the ids-largest-first
  // contract instead of trusting the caller's order.
  const ordered = [...communities].sort((a, b) => b.size - a.size || a.id - b.id);
  for (const c of ordered) {
    const row = byLabel.get(c.label);
    if (row) {
      row.ids.push(c.id);
      row.size += c.size;
    } else {
      byLabel.set(c.label, { ids: [c.id], label: c.label, size: c.size });
    }
  }
  return [...byLabel.values()].sort(
    (a, b) => b.size - a.size || a.label.localeCompare(b.label),
  );
}

/** Knowledge-graph legend rows: compact top-N strip by default, the full
 * list when open. `hidden` drives the "+N more" toggle (0 hides it). Pure —
 * the component only renders the returned slice. */
export function legendCommunities<T>(
  all: T[],
  open: boolean,
  collapsedCount = 8,
): { visible: T[]; hidden: number } {
  const visible = open ? all : all.slice(0, collapsedCount);
  return { visible, hidden: all.length - visible.length };
}

/** Index-store health for the stage-4 repair banner. Pure so the node-env
 * tests can pin the truth table: a REBUILD in flight outranks the error
 * (the operator already acted), and only a recorded sqlite failure — not a
 * mere 'json' serving_backend, which is legitimate right after a JSON-only
 * rebuild — raises the banner. */
export type IndexHealth = 'ok' | 'error' | 'rebuilding';

export function indexHealth(
  sqliteError: string | null | undefined,
  rebuildRunning: boolean,
): IndexHealth {
  if (rebuildRunning) return 'rebuilding';
  if (sqliteError) return 'error';
  return 'ok';
}

/** Knowledge-wall sort control: by claim count, theme name, or the theme's
 * created/updated day, either direction. Ties always break by name asc so
 * the order is deterministic under every mode; the default ('count'/'desc')
 * reproduces the wall's historical order exactly. Undated themes sort last
 * under BOTH directions. */
export type ThemeSortKey = 'count' | 'name' | 'updated' | 'created';
export type ThemeSortDir = 'asc' | 'desc';

/** B-day comparator: unknown (null) sorts last regardless of direction;
 * `dir` applies only between known days. */
function cmpDayTime(
  a: string | null | undefined,
  b: string | null | undefined,
  dir: ThemeSortDir,
): number {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  const raw = a < b ? -1 : a > b ? 1 : 0;
  return dir === 'asc' ? raw : -raw;
}

export function sortThemeWall(
  groups: ThemeGroup[],
  key: ThemeSortKey,
  dir: ThemeSortDir,
): ThemeGroup[] {
  return [...groups].sort((a, b) => {
    // Time keys apply `dir` inside the comparator (nulls stay last); the
    // count/name keys apply it here.
    let primary: number;
    switch (key) {
      case 'count':
        primary = a.total - b.total;
        break;
      case 'name':
        primary = a.theme.localeCompare(b.theme);
        break;
      case 'updated':
        primary = cmpDayTime(a.lastSeen, b.lastSeen, dir);
        break;
      case 'created':
        primary = cmpDayTime(a.firstSeen, b.firstSeen, dir);
        break;
    }
    const signed =
      key === 'count' || key === 'name' ? (dir === 'asc' ? primary : -primary) : primary;
    return signed || a.theme.localeCompare(b.theme);
  });
}

/** Theme-detail claim filter: by status. 'all' = both bands (the page's
 * historical behavior). */
export type ClaimStatusFilter = 'all' | 'durable' | 'caveated';

export function filterClaimsByStatus(
  claims: ClaimRow[],
  filter: ClaimStatusFilter,
): ClaimRow[] {
  if (filter === 'all') return claims;
  return claims.filter((c) => c.status === filter);
}

/** Theme-detail claim sort: 'default' reproduces `themeClaims` order
 * (durable before caveated, claim_id within each band); 'day' orders by the
 * claim's B-axis day, undated claims last under either direction. Ties
 * break by claim_id so the order is deterministic. */
export type ThemeClaimSortKey = 'default' | 'day';

export function sortThemeClaims(
  claims: ClaimRow[],
  key: ThemeClaimSortKey,
  dir: ThemeSortDir,
  sourceDays: Map<string, string> | null,
): ClaimRow[] {
  const sorted = [...claims];
  if (key === 'day') {
    sorted.sort((a, b) => {
      const cmp = cmpDayTime(claimDay(a, sourceDays), claimDay(b, sourceDays), dir);
      return cmp || a.claim_id.localeCompare(b.claim_id);
    });
  } else {
    const rank = (c: ClaimRow) => (c.status === 'durable' ? 0 : 1);
    sorted.sort((a, b) => rank(a) - rank(b) || a.claim_id.localeCompare(b.claim_id));
  }
  return sorted;
}

// ----------------------------------------------------- persisted sort prefs

/** localStorage key for the knowledge-family sort prefs. */
export const KNOWLEDGE_SORT_PREF_STORAGE = 'ovp.knowledgeSortPref.v1';

/** Persisted sort state shared by the knowledge pages (operator request
 * 2026-08-17: "the sort rules should be the same for this whole family of
 * pages, and stay between visits"). The TIME direction (`timeDir`) is the
 * shared axis: the wall's Updated/Created keys and the theme page's Day sort
 * all honor it, so picking "newest first" on either page carries to the
 * other. `detailSort` stays null until the operator explicitly picks a
 * claim-list sort on a theme page — null means "follow the wall: when the
 * wall sorts by time, the claims list does too". */
export interface KnowledgeSortPref {
  wallKey: ThemeSortKey;
  /** Direction for the non-time wall keys (count/name). */
  wallDir: ThemeSortDir;
  /** Shared date-sort direction (wall updated/created + detail day). */
  timeDir: ThemeSortDir;
  detailFilter: ClaimStatusFilter;
  /** Explicit claim-list sort — only 'day' (see resolveThemeClaimsSort:
   * 'default' means "follow the wall" and is stored as null). */
  detailSort: 'day' | null;
}

export const DEFAULT_KNOWLEDGE_SORT_PREF: KnowledgeSortPref = {
  wallKey: 'count',
  wallDir: 'desc',
  timeDir: 'desc',
  detailFilter: 'all',
  detailSort: null,
};

/** True for the wall's time sort keys — those share `timeDir` with the
 * theme page's Day sort. */
export function isTimeSortKey(key: ThemeSortKey): boolean {
  return key === 'updated' || key === 'created';
}

const isThemeSortKey = (v: unknown): v is ThemeSortKey =>
  v === 'count' || v === 'name' || v === 'updated' || v === 'created';
const isDir = (v: unknown): v is ThemeSortDir => v === 'asc' || v === 'desc';
const isClaimFilter = (v: unknown): v is ClaimStatusFilter =>
  v === 'all' || v === 'durable' || v === 'caveated';
const isClaimSortKey = (v: unknown): v is 'day' => v === 'day';

/** Parse the persisted prefs, tolerating absence and corruption (a stale or
 * hand-edited value falls back field-by-field to the default). `storage`
 * null (non-browser env) yields the default. */
export function readKnowledgeSortPref(
  storage: { getItem(key: string): string | null } | null,
): KnowledgeSortPref {
  if (!storage) return DEFAULT_KNOWLEDGE_SORT_PREF;
  let raw: unknown = null;
  try {
    raw = JSON.parse(storage.getItem(KNOWLEDGE_SORT_PREF_STORAGE) ?? '');
  } catch {
    return DEFAULT_KNOWLEDGE_SORT_PREF;
  }
  if (typeof raw !== 'object' || raw == null) return DEFAULT_KNOWLEDGE_SORT_PREF;
  const src = raw as Record<string, unknown>;
  return {
    wallKey: isThemeSortKey(src.wallKey) ? src.wallKey : DEFAULT_KNOWLEDGE_SORT_PREF.wallKey,
    wallDir: isDir(src.wallDir) ? src.wallDir : DEFAULT_KNOWLEDGE_SORT_PREF.wallDir,
    timeDir: isDir(src.timeDir) ? src.timeDir : DEFAULT_KNOWLEDGE_SORT_PREF.timeDir,
    detailFilter: isClaimFilter(src.detailFilter)
      ? src.detailFilter
      : DEFAULT_KNOWLEDGE_SORT_PREF.detailFilter,
    detailSort: isClaimSortKey(src.detailSort)
      ? src.detailSort
      : DEFAULT_KNOWLEDGE_SORT_PREF.detailSort,
  };
}

/** Persist the prefs. Best-effort — a quota/privacy-mode throw must not
 * crash the click that caused it. */
export function writeKnowledgeSortPref(
  pref: KnowledgeSortPref,
  storage: { setItem(key: string, value: string): void } | null,
): void {
  if (!storage) return;
  try {
    storage.setItem(KNOWLEDGE_SORT_PREF_STORAGE, JSON.stringify(pref));
  } catch {
    /* ignore */
  }
}

/** Resolve the wall's sort from a URLQuery + persisted prefs, merging any
 * explicit URL value INTO the prefs (shared links land once and become the
 * persisted rule) and reporting which params to clear (arrival-only — see
 * `resolveThemeClaimsSort` for the back-navigation reasoning). Invalid
 * values are neither used nor cleared. Pref direction per key type: time
 * keys → `timeDir`, others → `wallDir`. */
export function resolveKnowledgeWallSort(
  params: URLSearchParams,
  pref: KnowledgeSortPref,
): { key: ThemeSortKey; dir: ThemeSortDir; clear: ('sort' | 'dir')[] } {
  const urlKey = params.get('sort');
  const key = isThemeSortKey(urlKey) ? urlKey : pref.wallKey;
  const urlDir = params.get('dir');
  const dir = isDir(urlDir)
    ? urlDir
    : isTimeSortKey(key)
      ? pref.timeDir
      : pref.wallDir;
  const clear: ('sort' | 'dir')[] = [];
  if (isThemeSortKey(urlKey)) clear.push('sort');
  if (isDir(urlDir)) clear.push('dir');
  return { key, dir, clear };
}

/** Resolve the theme page's claim list (filter + sort + direction) from a
 * URLQuery + persisted prefs, merging valid explicit URL values INTO prefs
 * and reporting them for clearing (arrival-only). Explicit values win for
 * the FIRST paint and then persist — so a shared link lands exactly as
 * shared, and a back navigation carrying an old `?sort=…&dir=…` is absorbed
 * rather than overriding the operator's newer localStorage choice (the
 * "sorting is not remembered" bug). Invalid values are neither used nor
 * cleared. Then detail prefs; then the cross-page rule — when the wall sorts
 * by time and the operator has never picked a claim-list sort, the list
 * sorts by day too, in the shared `timeDir`. */
export function resolveThemeClaimsSort(
  params: URLSearchParams,
  pref: KnowledgeSortPref,
): {
  filter: ClaimStatusFilter;
  sort: ThemeClaimSortKey;
  dir: ThemeSortDir;
  clear: ('sort' | 'dir' | 'filter')[];
} {
  const urlFilter = params.get('filter');
  const filter = isClaimFilter(urlFilter) ? urlFilter : pref.detailFilter;
  // 'default' is NOT a stored preference: it means "follow the wall" —
  // storing it would freeze the list into the hardcoded order even when the
  // wall (and the shared time axis) later switches to a time sort, which the
  // operator experiences as "my sort got lost". Only 'day' is an explicit
  // claim-list choice.
  const urlSort = params.get('sort');
  let sort: ThemeClaimSortKey;
  if (urlSort === 'day') {
    sort = 'day';
  } else if (pref.detailSort === 'day') {
    sort = 'day';
  } else if (isTimeSortKey(pref.wallKey)) {
    // The wall is on a time sort and the operator has not chosen a
    // claim-list sort here — carry the time sort (and its direction).
    sort = 'day';
  } else {
    sort = 'default';
  }
  const urlDir = params.get('dir');
  const dir = isDir(urlDir) ? urlDir : sort === 'day' ? pref.timeDir : 'desc';
  const clear: ('sort' | 'dir' | 'filter')[] = [];
  if (isClaimFilter(urlFilter)) clear.push('filter');
  if (urlSort === 'day') clear.push('sort'); // 'default' is not a stored pref
  if (isDir(urlDir)) clear.push('dir');
  return { filter, sort, dir, clear };
}

// ---------------------------------------------------------------------------
// Silently failing scheduler jobs
// ---------------------------------------------------------------------------

/** A scheduled job that is failing, condensed for the always-visible banner. */
export interface FailingJob {
  id: string;
  /** Failures since the last success. 1 for a first failure. */
  streak: number;
  /** Local `YYYY-MM-DDTHH:MM:SS` of the last attempt. */
  lastRun: string;
  /** The run's final word — see [`firstErrorLine`]. */
  reason: string | null;
}

/** Longest reason the banner will render, ELLIPSIS INCLUDED. */
const REASON_MAX = 160;

/**
 * The run's final word: the LAST non-blank line of its stderr tail.
 *
 * Not the first. The tail is the last 12 lines of stderr and on a real
 * crystallize failure it OPENED with progress chatter ("embedding 1 pack(s)
 * with Xenova/…") while the cause ("error: gate: strength verdicts
 * incomplete") was the final line.
 *
 * An earlier version pattern-matched for error-looking lines, which was worse
 * in both directions: it missed causes that carry no keyword
 * (`Caused by:` / `Permission denied (os error 13)`) and it matched summaries
 * that do (`summary: failed: 0`). Taking the last line handles all of those,
 * and needs no vocabulary to keep up to date.
 */
export function firstErrorLine(tail: string | null | undefined): string | null {
  if (!tail) return null;
  const lines = tail
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.length > 0);
  const line = lines[lines.length - 1];
  if (!line) return null;
  // -1 for the ellipsis: the cap counts what is RENDERED, so slicing to the
  // cap and then appending would overshoot it.
  return line.length > REASON_MAX ? `${line.slice(0, REASON_MAX - 1)}…` : line;
}

export function failingJobs(
  jobs: readonly ScheduleJobLike[] | null | undefined,
  opts: { exclude?: readonly string[] } = {},
): FailingJob[] {
  const exclude = new Set(opts.exclude ?? ['daily']);
  return (jobs ?? [])
    .filter(
      (j) =>
        j.enabled !== false &&
        j.last_status === 'error' &&
        !exclude.has(j.id),
    )
    .map((j) => ({
      id: j.id,
      streak: Math.max(1, j.consecutive_failures ?? 1),
      lastRun: j.last_run ?? '',
      reason: firstErrorLine(j.last_error),
    }))
    .sort((a, b) => b.streak - a.streak || a.id.localeCompare(b.id));
}

/** The subset of `ScheduleJob` this module needs — keeps derive.ts free of
 *  the API module's import graph. */
export interface ScheduleJobLike {
  id: string;
  enabled?: boolean;
  last_status?: string;
  last_run?: string;
  last_error?: string | null;
  consecutive_failures?: number;
}
