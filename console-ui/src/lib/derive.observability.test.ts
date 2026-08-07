/** Decision logic behind the System schedule failure strip, the theme-card
 * source badge, and the ask failure hint — pure functions, no DOM. */
import { describe, expect, it } from 'vitest';
import {
  failureHintKey,
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
