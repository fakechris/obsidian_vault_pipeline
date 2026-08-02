/** Throughput / ETA estimates for the source-work queue.
 *
 * Pure client math over item timestamps already on each queue row
 * (`started_at` / `finished_at` as unix seconds). No extra API.
 *
 * Model: one article is one work unit (serial across articles). Duration
 * samples come from recently finished `done` items. We use a recency-weighted
 * average (newer finishes weigh more) so a long deepwiki translate does not
 * permanently drag the estimate, and short Chinese-summary-only jobs do not
 * paint an unrealistically optimistic ETA either.
 */

import type { SourceWorkQueueItem } from './api';

export interface DurationSample {
  /** Unix seconds when the article finished. */
  finishedAt: number;
  /** Wall time from start → finish (seconds). */
  durationSec: number;
}

export interface WorkQueueEta {
  /** Samples used for the weighted average. */
  sampleCount: number;
  /** Recency-weighted mean seconds per article. */
  avgSec: number;
  /** Median of the same sample set (shown as a sanity check). */
  medianSec: number;
  /** Finished articles in the last hour (by finished_at). */
  doneLastHour: number;
  /** Finished articles in the last 15 minutes. */
  doneLast15m: number;
  /** Most recent terminal finish time (unix sec), if any. */
  lastFinishedAt: number | null;
  /** Duration of that last finished article (sec). */
  lastDurationSec: number | null;
  /** Title of the last finished article (best-effort). */
  lastTitle: string | null;
  /** Status of last finished article. */
  lastStatus: 'done' | 'failed' | 'cancelled' | null;
  /** Remaining work units (queued + fractional running remainder). */
  remainingUnits: number;
  /** Estimated seconds until the queue drains (null if unknown). */
  etaSec: number | null;
  /** Absolute unix-sec ETA clock time (null if unknown). */
  etaAt: number | null;
  /** Running article elapsed seconds (null if none running). */
  runningElapsedSec: number | null;
  /** Whether we have enough data to trust the ETA (sampleCount ≥ 2). */
  reliable: boolean;
}

const MIN_SAMPLE_SEC = 8;
const MAX_SAMPLE_SEC = 3 * 60 * 60; // 3h — hard clamp outliers
/** How many recent finishes feed the weighted average. */
const SAMPLE_CAP = 24;
/** Half-life (seconds) for exponential recency weight. */
const WEIGHT_HALF_LIFE_SEC = 45 * 60; // 45 min

/** Extract a usable duration sample from one terminal item. */
export function sampleFromItem(
  item: SourceWorkQueueItem,
): DurationSample | null {
  if (item.status !== 'done' && item.status !== 'failed') return null;
  const finished = item.finished_at ?? null;
  const started = item.started_at ?? null;
  if (finished == null || started == null) return null;
  const dur = finished - started;
  if (!Number.isFinite(dur) || dur < MIN_SAMPLE_SEC || dur > MAX_SAMPLE_SEC) {
    return null;
  }
  return { finishedAt: finished, durationSec: dur };
}

/** Weighted average: newer samples weigh more (exp decay by age). */
export function weightedAverageSec(
  samples: DurationSample[],
  nowSec: number,
): number {
  if (samples.length === 0) return 0;
  let num = 0;
  let den = 0;
  for (const s of samples) {
    const age = Math.max(0, nowSec - s.finishedAt);
    const w = Math.pow(0.5, age / WEIGHT_HALF_LIFE_SEC);
    num += s.durationSec * w;
    den += w;
  }
  return den > 0 ? num / den : 0;
}

export function medianSec(samples: DurationSample[]): number {
  if (samples.length === 0) return 0;
  const xs = samples.map((s) => s.durationSec).sort((a, b) => a - b);
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 === 0 ? (xs[mid - 1] + xs[mid]) / 2 : xs[mid];
}

/**
 * Build an ETA snapshot from the current queue.
 * @param nowSec - injectable clock (unix seconds) for tests.
 */
export function computeWorkQueueEta(
  items: SourceWorkQueueItem[],
  nowSec: number = Math.floor(Date.now() / 1000),
): WorkQueueEta {
  const doneItems = items
    .filter((i) => i.status === 'done' || i.status === 'failed')
    .slice();

  // Recency-ordered samples.
  const samples: DurationSample[] = [];
  for (const item of doneItems) {
    const s = sampleFromItem(item);
    if (s) samples.push(s);
  }
  samples.sort((a, b) => b.finishedAt - a.finishedAt);
  const capped = samples.slice(0, SAMPLE_CAP);

  const avg = weightedAverageSec(capped, nowSec);
  const med = medianSec(capped);

  const doneLastHour = samples.filter(
    (s) => nowSec - s.finishedAt <= 3600,
  ).length;
  const doneLast15m = samples.filter(
    (s) => nowSec - s.finishedAt <= 15 * 60,
  ).length;

  // Last finished item (any terminal with finished_at), prefer done over failed.
  let last: SourceWorkQueueItem | null = null;
  for (const item of items) {
    if (
      (item.status === 'done' ||
        item.status === 'failed' ||
        item.status === 'cancelled') &&
      item.finished_at != null
    ) {
      if (!last || (item.finished_at ?? 0) > (last.finished_at ?? 0)) {
        last = item;
      }
    }
  }
  const lastSample = last ? sampleFromItem(last) : null;

  const queued = items.filter((i) => i.status === 'queued').length;
  const running = items.find((i) => i.status === 'running');
  let runningElapsed: number | null = null;
  let remainingUnits = queued;
  if (running) {
    const started = running.started_at ?? running.created_at;
    runningElapsed = Math.max(0, nowSec - started);
    // Fractional remainder of the current article.
    if (avg > 0) {
      const rem = Math.max(0.12 * avg, avg - runningElapsed);
      remainingUnits += rem / avg;
    } else {
      remainingUnits += 1;
    }
  }

  const reliable = capped.length >= 2 && avg >= MIN_SAMPLE_SEC;
  let etaSec: number | null = null;
  let etaAt: number | null = null;
  if (reliable && remainingUnits > 0) {
    etaSec = avg * remainingUnits;
    etaAt = nowSec + Math.round(etaSec);
  } else if (reliable && remainingUnits <= 0) {
    etaSec = 0;
    etaAt = nowSec;
  }

  return {
    sampleCount: capped.length,
    avgSec: avg,
    medianSec: med,
    doneLastHour,
    doneLast15m,
    lastFinishedAt: last?.finished_at ?? null,
    lastDurationSec: lastSample?.durationSec ?? null,
    lastTitle: last?.title?.trim() || null,
    lastStatus: last
      ? (last.status as 'done' | 'failed' | 'cancelled')
      : null,
    remainingUnits,
    etaSec,
    etaAt,
    runningElapsedSec: runningElapsed,
    reliable,
  };
}

/** Human duration: "4m 12s", "2h 05m", "18h", "3d 4h". */
export function formatDurationSec(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return '—';
  const s = Math.round(sec);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return rs > 0 ? `${m}m ${rs}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  if (h < 24) return rm > 0 ? `${h}h ${rm}m` : `${h}h`;
  const d = Math.floor(h / 24);
  const rh = h % 24;
  return rh > 0 ? `${d}d ${rh}h` : `${d}d`;
}

/** Local clock string for a unix-sec instant. */
export function formatClock(unixSec: number, locale?: string): string {
  return new Date(unixSec * 1000).toLocaleString(locale, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}
