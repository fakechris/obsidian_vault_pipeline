import { describe, expect, it } from 'vitest';
import {
  computeBarHeights,
  recentIsoDayRange,
  sparklineHasActivity,
  sparklinesByThemeGroup,
} from '../../lib/derive';
import type { ClaimRow } from '../../lib/types';

describe('sparkline derive helpers', () => {
  it('computeBarHeights scales to maxHeight', () => {
    expect(computeBarHeights([0, 0], 16)).toEqual([0, 0]);
    expect(computeBarHeights([2, 4, 1], 20)).toEqual([10, 20, 5]);
  });

  it('sparklineHasActivity is false for all-zero series', () => {
    expect(sparklineHasActivity([])).toBe(false);
    expect(sparklineHasActivity([0, 0])).toBe(false);
    expect(sparklineHasActivity([0, 1])).toBe(true);
  });

  it('recentIsoDayRange returns consecutive ISO days', () => {
    expect(recentIsoDayRange(3, '2026-01-03')).toEqual([
      '2026-01-01',
      '2026-01-02',
      '2026-01-03',
    ]);
  });

  it('sparklinesByThemeGroup buckets by theme id', () => {
    const claims: ClaimRow[] = [
      {
        claim_id: 'a',
        claim: 'one',
        status: 'durable',
        sources: [],
        theme: 'T1',
        theme_id: 1,
        run_date: '2026-01-02',
      },
      {
        claim_id: 'b',
        claim: 'two',
        status: 'durable',
        sources: [],
        theme: 'T1',
        theme_id: 1,
        run_date: '2026-01-02',
      },
      {
        claim_id: 'c',
        claim: 'three',
        status: 'durable',
        sources: [],
        theme: 'T2',
        theme_id: 2,
        run_date: '2026-01-03',
      },
    ];
    const map = sparklinesByThemeGroup(claims, '2026-01-03', 3, null);
    expect(map.get('i1')).toEqual([0, 2, 0]);
    expect(map.get('i2')).toEqual([0, 0, 1]);
  });
});