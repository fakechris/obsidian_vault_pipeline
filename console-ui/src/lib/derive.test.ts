/** Unit tests for the run-liveness banner derivation (OVP2 observability P0).
 * Pure functions over the model + a passed-in wall clock — no DOM. */
import { describe, expect, it } from 'vitest';
import {
  healthLevel,
  isRunningWithProgress,
  lastRunBanner,
  runActivity,
  STALE_AFTER_MS,
} from './derive';
import type { IndexModel, LastRunModel, RecentSource } from './types';

const NOW = Date.parse('2026-07-12T12:00:00Z');

function model(lastRun: LastRunModel | null, overrides: Partial<IndexModel> = {}): IndexModel {
  return {
    schema: 'ovp.index/v2',
    date: '2026-07-12',
    totals: {
      sources: 0,
      queued: 0,
      processed: 0,
      failed: 0,
      blocked: 0,
      needs_content: 0,
      unparseable: 0,
      duplicates: 0,
      packs: 0,
      claims_durable: 0,
      claims_caveated: 0,
      runs: 0,
    },
    sources: [],
    packs: [],
    claims: [],
    runs: [],
    ops: { blocked_sources: [], queue_depth: 0, last_run: lastRun },
    ...overrides,
  };
}

describe('lastRunBanner', () => {
  it('is "none" for a null model — the banner never hides behind an empty model', () => {
    const b = lastRunBanner(null, NOW);
    expect(b.level).toBe('none');
    expect(b.status).toBeNull();
    expect(b.ageMinutes).toBeNull();
  });

  it('is "none" when the model has no heartbeat', () => {
    expect(lastRunBanner(model(null), NOW).level).toBe('none');
  });

  it('is green + ages for a recent completed run, with counts', () => {
    const b = lastRunBanner(
      model({
        run_id: 'r',
        started_at: '2026-07-12T09:50:00Z',
        ended_at: '2026-07-12T10:00:00Z',
        status: 'completed',
        processed: 8,
        queued_after: 180,
      }),
      NOW,
    );
    expect(b.level).toBe('ok');
    expect(b.ageMinutes).toBe(120); // 10:00 → 12:00
    expect(b.processed).toBe(8);
    expect(b.queuedAfter).toBe(180);
  });

  it('is red for a failed run regardless of age, carrying the error', () => {
    const b = lastRunBanner(
      model({
        run_id: 'r',
        started_at: '2026-07-12T11:00:00Z',
        ended_at: '2026-07-12T11:05:00Z',
        status: 'failed',
        error: 'ANTHROPIC_API_KEY expired',
      }),
      NOW,
    );
    expect(b.level).toBe('failed');
    expect(b.status).toBe('failed');
    expect(b.error).toBe('ANTHROPIC_API_KEY expired');
  });

  it('is red for an aborted run', () => {
    const b = lastRunBanner(
      model({
        run_id: 'r',
        started_at: '2026-07-12T11:00:00Z',
        ended_at: '2026-07-12T11:30:00Z',
        status: 'aborted',
        error: 'panic',
      }),
      NOW,
    );
    expect(b.level).toBe('failed');
    expect(b.status).toBe('aborted');
  });

  it('turns amber (stale) when a completed run is older than the schedule interval', () => {
    const old = new Date(NOW - STALE_AFTER_MS - 60_000).toISOString();
    const b = lastRunBanner(
      model({ run_id: 'r', started_at: old, ended_at: old, status: 'completed' }),
      NOW,
    );
    expect(b.level).toBe('stale');
  });

  it('treats a long-"running" run past the interval as stale (drop-guard never fired)', () => {
    const old = new Date(NOW - STALE_AFTER_MS - 60_000).toISOString();
    const b = lastRunBanner(
      model({ run_id: 'r', started_at: old, status: 'running' }),
      NOW,
    );
    expect(b.level).toBe('stale');
  });

  it('carries live in-run progress fields off a running heartbeat', () => {
    const b = lastRunBanner(
      model({
        run_id: 'r',
        started_at: '2026-07-12T11:48:00Z',
        status: 'running',
        processed_so_far: 18,
        total_planned: 90,
        current: 'Some Article Title',
      }),
      NOW,
    );
    expect(b.status).toBe('running');
    expect(b.processedSoFar).toBe(18);
    expect(b.totalPlanned).toBe(90);
    expect(b.current).toBe('Some Article Title');
  });
});

describe('isRunningWithProgress', () => {
  it('is true for a running heartbeat that has written a progress fraction', () => {
    const b = lastRunBanner(
      model({
        run_id: 'r',
        started_at: '2026-07-12T11:50:00Z',
        status: 'running',
        processed_so_far: 3,
        total_planned: 12,
        current: 'x',
      }),
      NOW,
    );
    expect(isRunningWithProgress(b)).toBe(true);
  });

  it('is false for a running run that has no progress yet (older server / pre-first-source)', () => {
    const b = lastRunBanner(
      model({ run_id: 'r', started_at: '2026-07-12T11:59:00Z', status: 'running' }),
      NOW,
    );
    expect(isRunningWithProgress(b)).toBe(false);
  });

  it('is false when the run is not running even if fields are present', () => {
    const b = lastRunBanner(
      model({
        run_id: 'r',
        started_at: '2026-07-12T09:00:00Z',
        ended_at: '2026-07-12T10:00:00Z',
        status: 'completed',
        processed_so_far: 90,
        total_planned: 90,
      }),
      NOW,
    );
    expect(isRunningWithProgress(b)).toBe(false);
  });

  it('is false when total_planned is 0 (no divide-by-zero fraction)', () => {
    const b = lastRunBanner(
      model({
        run_id: 'r',
        started_at: '2026-07-12T11:59:00Z',
        status: 'running',
        processed_so_far: 0,
        total_planned: 0,
      }),
      NOW,
    );
    expect(isRunningWithProgress(b)).toBe(false);
  });
});

describe('healthLevel reconciliation', () => {
  it('goes red when the heartbeat failed even if no per-source run failed', () => {
    const m = model({
      run_id: 'r',
      started_at: '2026-07-12T11:00:00Z',
      ended_at: '2026-07-12T11:05:00Z',
      status: 'failed',
      error: 'boom',
    });
    expect(healthLevel(m, NOW)).toBe('failed');
  });

  it('goes red on a stale heartbeat', () => {
    const old = new Date(NOW - STALE_AFTER_MS - 60_000).toISOString();
    const m = model({ run_id: 'r', started_at: old, ended_at: old, status: 'completed' });
    expect(healthLevel(m, NOW)).toBe('failed');
  });

  it('is green when the heartbeat is fresh + completed and nothing needs attention', () => {
    const m = model({
      run_id: 'r',
      started_at: '2026-07-12T11:50:00Z',
      ended_at: '2026-07-12T11:55:00Z',
      status: 'completed',
    });
    expect(healthLevel(m, NOW)).toBe('ok');
  });
});

describe('runActivity (live per-source feed)', () => {
  const rec = (seq: number, status: 'ok' | 'failed'): RecentSource => ({
    seq,
    title: `Source ${seq}`,
    status,
    units: status === 'ok' ? 10 + seq : 0,
    cards: status === 'ok' ? seq : 0,
    reason: status === 'failed' ? 'no cassette' : undefined,
    at: '2026-07-12T11:59:00Z',
  });

  it('returns the empty idle shape for a null model — panel never crashes', () => {
    const a = runActivity(null);
    expect(a.status).toBeNull();
    expect(a.running).toBe(false);
    expect(a.recent).toEqual([]);
  });

  it('exposes the running fraction + percent and the feed NEWEST FIRST', () => {
    const m = model({
      run_id: 'r',
      started_at: '2026-07-12T11:50:00Z',
      status: 'running',
      processed_so_far: 3,
      total_planned: 4,
      current: 'Source 3',
      recent: [rec(1, 'ok'), rec(2, 'failed'), rec(3, 'ok')],
    });
    const a = runActivity(m);
    expect(a.running).toBe(true);
    expect(a.processedSoFar).toBe(3);
    expect(a.totalPlanned).toBe(4);
    expect(a.pct).toBe(75);
    expect(a.current).toBe('Source 3');
    // Newest first: seq 3 leads, seq 1 trails.
    expect(a.recent.map((r) => r.seq)).toEqual([3, 2, 1]);
    // Both success AND failure surface.
    expect(a.recent.find((r) => r.status === 'failed')?.reason).toBe('no cassette');
  });

  it('keeps a finished run’s feed + terminal counts for post-run diagnosis', () => {
    const m = model({
      run_id: 'r',
      started_at: '2026-07-12T11:50:00Z',
      ended_at: '2026-07-12T11:58:00Z',
      status: 'completed',
      processed: 8,
      failed: 1,
      recent: [rec(1, 'ok')],
    });
    const a = runActivity(m);
    expect(a.running).toBe(false);
    expect(a.processed).toBe(8);
    expect(a.failed).toBe(1);
    expect(a.recent).toHaveLength(1);
  });

  it('null pct when the heartbeat carries no fraction yet', () => {
    const m = model({
      run_id: 'r',
      started_at: '2026-07-12T11:50:00Z',
      status: 'running',
    });
    expect(runActivity(m).pct).toBeNull();
  });
});
