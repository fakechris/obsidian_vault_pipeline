/** Unit tests for the run-liveness banner derivation (OVP2 observability P0).
 * Pure functions over the model + a passed-in wall clock — no DOM. */
import { describe, expect, it } from 'vitest';
import {
  healthLevel,
  lastRunBanner,
  STALE_AFTER_MS,
} from './derive';
import type { IndexModel, LastRunModel } from './types';

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
