/** Freshness-label derivation unit tests (vitest, node env): the age helper is
 * pure — the ticking clock is injected — so buckets and the unknown/clamp rules
 * are asserted deterministically. This is the P1 "numbers lie" guard: a stale
 * projection derives a visibly-older age than a fresh one. */
import { describe, expect, it } from 'vitest';
import { ageParts } from './derive';

const BUILT = '2026-07-09T00:00:00Z';
const BUILT_MS = Date.parse(BUILT);

describe('ageParts', () => {
  it('reports "just now" under a minute', () => {
    const a = ageParts(BUILT, BUILT_MS + 30_000);
    expect(a.unknown).toBe(false);
    expect(a.unit).toBe('now');
    expect(a.value).toBe(0);
    expect(a.builtAt).toBe(BUILT);
    expect(a.seconds).toBe(30);
  });

  it('buckets minutes, hours and days', () => {
    expect(ageParts(BUILT, BUILT_MS + 5 * 60_000)).toMatchObject({
      unit: 'minute',
      value: 5,
    });
    expect(ageParts(BUILT, BUILT_MS + 3 * 3_600_000)).toMatchObject({
      unit: 'hour',
      value: 3,
    });
    expect(ageParts(BUILT, BUILT_MS + 2 * 86_400_000)).toMatchObject({
      unit: 'day',
      value: 2,
    });
  });

  it('flags unknown for absent or unparseable built_at', () => {
    expect(ageParts(null, BUILT_MS)).toMatchObject({ unknown: true, builtAt: null });
    expect(ageParts(undefined, BUILT_MS)).toMatchObject({ unknown: true });
    expect(ageParts('not-a-date', BUILT_MS)).toMatchObject({ unknown: true });
  });

  it('clamps negative age (clock skew) to zero', () => {
    // "now" is BEFORE built_at — a slight skew must not read as negative.
    const a = ageParts(BUILT, BUILT_MS - 5_000);
    expect(a.seconds).toBe(0);
    expect(a.unit).toBe('now');
  });

  it('distinguishes a stale projection from a fresh one', () => {
    const fresh = ageParts(BUILT, BUILT_MS + 10_000);
    const stale = ageParts(BUILT, BUILT_MS + 6 * 3_600_000);
    // The whole point of P1: the numbers no longer render identically.
    expect(fresh.seconds).toBeLessThan(stale.seconds);
    expect(fresh.unit).toBe('now');
    expect(stale.unit).toBe('hour');
  });
});
