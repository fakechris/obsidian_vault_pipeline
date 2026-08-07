/** Decision logic behind the System schedule failure strip, the theme-card
 * source badge, and the ask failure hint — pure functions, no DOM. */
import { describe, expect, it } from 'vitest';
import {
  failureHintKey,
  focusBounds,
  groupCommunities,
  legendCommunities,
  scheduleFailureStrip,
  themeSourceBadge,
} from './derive';

describe('scheduleFailureStrip', () => {
  const job = (over: Partial<Parameters<typeof scheduleFailureStrip>[0]> = {}) => ({
    last_status: 'error',
    enabled: true,
    consecutive_failures: 3,
    failures_total: 5,
    runs_total: 40,
    ...over,
  });

  it('hides for non-error statuses', () => {
    expect(scheduleFailureStrip(job({ last_status: 'ok' }))).toBeNull();
    expect(scheduleFailureStrip(job({ last_status: 'seeded' }))).toBeNull();
  });

  it('reports streak, retry note, and lifetime counts', () => {
    expect(scheduleFailureStrip(job())).toEqual({
      streak: 3,
      noteKey: 'noRetry',
      counts: { fails: 5, runs: 40 },
    });
  });

  it('legacy pre-counter state floors the streak at 1 and hides counts', () => {
    // An upgraded vault deserializes all counters as 0 — the error status
    // alone proves at least one failure.
    expect(
      scheduleFailureStrip(
        job({ consecutive_failures: 0, failures_total: 0, runs_total: 0 }),
      ),
    ).toEqual({ streak: 1, noteKey: 'noRetry', counts: null });
    expect(
      scheduleFailureStrip(
        job({
          consecutive_failures: undefined,
          failures_total: undefined,
          runs_total: undefined,
        }),
      ),
    ).toEqual({ streak: 1, noteKey: 'noRetry', counts: null });
  });

  it('disabled jobs get the disabled-specific recovery note', () => {
    expect(scheduleFailureStrip(job({ enabled: false }))?.noteKey).toBe('disabled');
  });
});

describe('themeSourceBadge', () => {
  it('hides when the count is unknown (drift / ledger-only theme)', () => {
    expect(themeSourceBadge({ sources: 0, durable: 4, caveated: 1 })).toBeNull();
  });

  it('flags a multi-claim single-source theme', () => {
    expect(themeSourceBadge({ sources: 1, durable: 3, caveated: 1 })).toEqual({
      n: 1,
      single: true,
    });
  });

  it('a one-claim one-source theme is unremarkable', () => {
    expect(themeSourceBadge({ sources: 1, durable: 1, caveated: 0 })).toEqual({
      n: 1,
      single: false,
    });
  });

  it('multi-source themes show the plain count', () => {
    expect(themeSourceBadge({ sources: 4, durable: 5, caveated: 2 })).toEqual({
      n: 4,
      single: false,
    });
  });
});

describe('groupCommunities', () => {
  it('merges same-label clusters, summing sizes, largest cluster first', () => {
    // Live-vault case: Louvain splits one theme across several clusters —
    // 'Agent Harness Architecture' appeared 4× in the legend. Input order is
    // deliberately NOT size-desc: the ids contract must not trust the caller.
    const rows = groupCommunities([
      { id: 9, label: 'Harness', size: 5 },
      { id: 5, label: 'Harness', size: 14 },
      { id: 4, label: 'Memory', size: 11 },
      { id: 2, label: 'Harness', size: 16 },
      { id: 1, label: 'Memory', size: 32 },
    ]);
    expect(rows).toEqual([
      { ids: [1, 4], label: 'Memory', size: 43 },
      { ids: [2, 5, 9], label: 'Harness', size: 35 },
    ]);
  });

  it('keeps distinct labels apart and sorts ties by name', () => {
    const rows = groupCommunities([
      { id: 1, label: 'B', size: 5 },
      { id: 2, label: 'A', size: 5 },
    ]);
    expect(rows.map((r) => r.label)).toEqual(['A', 'B']);
  });

  it('empty input stays safe', () => {
    expect(groupCommunities([])).toEqual([]);
  });
});

describe('focusBounds', () => {
  it('centroid + bounding radius cover far-apart clusters', () => {
    // Two clusters 200 apart: the radius must reach both, so the 3D camera
    // frames them (a cluster-count heuristic under-shot exactly here).
    const b = focusBounds([
      { x: -100, y: 0, z: 0 },
      { x: 100, y: 0, z: 0 },
    ])!;
    expect(b).toEqual({ x: 0, y: 0, z: 0, radius: 100 });
    const wide = focusBounds([
      { x: 0, y: 0 },
      { x: 0, y: 300 },
      { x: 0, y: 600 },
    ])!;
    expect(wide.y).toBe(300);
    expect(wide.radius).toBe(300);
  });

  it('a single point has zero radius; empty input is null', () => {
    expect(focusBounds([{ x: 5, y: -2, z: 1 }])).toEqual({ x: 5, y: -2, z: 1, radius: 0 });
    expect(focusBounds([])).toBeNull();
  });
});

describe('legendCommunities', () => {
  const ids = (n: number) => Array.from({ length: n }, (_, i) => i + 1);

  it('collapsed: top-N strip and the hidden remainder', () => {
    expect(legendCommunities([], false, 8)).toEqual({ visible: [], hidden: 0 });
    expect(legendCommunities(ids(8), false, 8)).toEqual({ visible: ids(8), hidden: 0 });
    expect(legendCommunities(ids(9), false, 8)).toEqual({
      visible: ids(8),
      hidden: 1,
    });
    const forty = legendCommunities(ids(40), false, 8);
    expect(forty.visible.length).toBe(8);
    expect(forty.hidden).toBe(32);
  });

  it('open: the full list with nothing hidden', () => {
    expect(legendCommunities(ids(40), true, 8)).toEqual({
      visible: ids(40),
      hidden: 0,
    });
  });
});

describe('failureHintKey', () => {
  it('maps the six actionable classes to hint keys', () => {
    expect(failureHintKey('auth')).toBe('ask.fail.auth');
    expect(failureHintKey('rate_limited')).toBe('ask.fail.rateLimited');
    expect(failureHintKey('context_exceeded')).toBe('ask.fail.contextExceeded');
    expect(failureHintKey('budget_exhausted')).toBe('ask.fail.budgetExhausted');
    expect(failureHintKey('overloaded')).toBe('ask.fail.overloaded');
    expect(failureHintKey('network')).toBe('ask.fail.network');
  });

  it('unactionable or absent classes fall back to the generic stop note', () => {
    for (const cls of ['decode', 'protocol', 'internal', 'cache_miss', 'provider_error', '', null, undefined]) {
      expect(failureHintKey(cls)).toBeNull();
    }
  });
});
