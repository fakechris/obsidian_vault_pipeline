/** Regression tests for the failed-run recovery controls (OVP2 observability:
 * a failed run must stay OBSERVABLE and CONTROLLABLE).
 *
 * The bug these lock down: the retry button tracked pending state by watching
 * `banner.level !== 'failed'`. When a retry ALSO failed, the level kept the
 * same VALUE, so a level-keyed effect never re-fired — the button sat on
 * "Starting…" forever, disabled, with no error shown. The operator's only
 * recovery control was dead and the UI said nothing about it.
 *
 * Same DOM-free approach as RunBanner.progress.test.ts: the load-bearing
 * decisions are pure functions, and the bilingual templates are checked by
 * mirroring the i18n provider's `{name}` interpolation. */
import { describe, expect, it } from 'vitest';
import { en } from '../i18n/en';
import { zh } from '../i18n/zh';
import {
  RETRY_WATCHDOG_MS,
  buildRunTimeline,
  lastRunBanner,
  runSignature,
  shouldClearRetry,
} from '../lib/derive';
import type { IndexModel, LastRunModel } from '../lib/types';

const NOW = Date.parse('2026-08-04T03:30:00Z');

/** Mirror the i18n provider's interpolation (index.tsx): `{name}` → vars[name]. */
function fill(msg: string, vars: Record<string, string | number>): string {
  let out = msg;
  for (const [k, v] of Object.entries(vars)) out = out.replaceAll(`{${k}}`, String(v));
  return out;
}

function modelWith(lr: Partial<LastRunModel>): IndexModel {
  const last_run: LastRunModel = {
    run_id: 'daily-2026-08-03',
    started_at: '2026-08-04T02:25:52Z',
    status: 'failed',
    ...lr,
  };
  return {
    schema: 'ovp.index/v2',
    date: '2026-08-03',
    totals: {
      sources: 0, queued: 0, processed: 0, failed: 0, blocked: 0,
      needs_content: 0, unparseable: 0, duplicates: 0, packs: 0,
      claims_durable: 0, claims_caveated: 0, runs: 0,
    },
    sources: [], packs: [], claims: [], runs: [],
    ops: { blocked_sources: [], queue_depth: 0, last_run },
  };
}

const FAILED = lastRunBanner(
  modelWith({ ended_at: '2026-08-04T02:25:52Z', error: 'io: live web fetch requires a build' }),
  NOW,
);

describe('retry pending state', () => {
  it('does not clear when there is nothing pending', () => {
    expect(shouldClearRetry(null, FAILED, NOW)).toBe(false);
  });

  it('holds while the same failed run is still the newest one', () => {
    const pending = { from: runSignature(FAILED), atMs: NOW };
    expect(shouldClearRetry(pending, FAILED, NOW + 5_000)).toBe(false);
  });

  it('REGRESSION: clears when the retry itself fails — a second failed run', () => {
    // Same-day retries reuse run_id (`daily-2026-08-03`); only the start
    // instant moves. Level stays 'failed' both times, which is exactly what
    // used to strand the button.
    const pending = { from: runSignature(FAILED), atMs: NOW };
    const retriedAndFailedAgain = lastRunBanner(
      modelWith({
        started_at: '2026-08-04T03:27:10Z',
        ended_at: '2026-08-04T03:27:11Z',
        status: 'failed',
        error: 'io: live web fetch requires a build',
      }),
      NOW + 60_000,
    );
    expect(retriedAndFailedAgain.level).toBe('failed');
    expect(retriedAndFailedAgain.runId).toBe(FAILED.runId); // run_id did NOT move
    expect(shouldClearRetry(pending, retriedAndFailedAgain, NOW + 60_000)).toBe(true);
  });

  it('clears once the retry actually starts running', () => {
    const pending = { from: runSignature(FAILED), atMs: NOW };
    const running = lastRunBanner(
      modelWith({ started_at: '2026-08-04T03:27:10Z', status: 'running' }),
      NOW + 60_000,
    );
    expect(running.level).not.toBe('failed');
    expect(shouldClearRetry(pending, running, NOW + 60_000)).toBe(true);
  });

  it('watchdog re-arms the button when the child dies without any heartbeat', () => {
    // Nothing moves at all: same run, same level. Without the watchdog the
    // operator would be left with a permanently disabled control.
    const pending = { from: runSignature(FAILED), atMs: NOW };
    expect(shouldClearRetry(pending, FAILED, NOW + RETRY_WATCHDOG_MS - 1)).toBe(false);
    expect(shouldClearRetry(pending, FAILED, NOW + RETRY_WATCHDOG_MS)).toBe(true);
  });

  it('signature distinguishes attempts that share a run id', () => {
    const a = runSignature(FAILED);
    const b = runSignature(
      lastRunBanner(modelWith({ started_at: '2026-08-04T03:27:10Z' }), NOW),
    );
    expect(a).not.toEqual(b);
  });
});

describe('failed run states that it will not self-heal', () => {
  const failedLr: LastRunModel = {
    run_id: 'daily-2026-08-03',
    started_at: '2026-08-04T02:25:52Z',
    ended_at: '2026-08-04T02:25:52Z',
    status: 'failed',
    error: 'io: live web fetch requires a build with `--features web-fetch-live`',
  };

  it('adds the no-auto-retry step when the failure consumed the window', () => {
    // `is_due` compares last_run to the most recent occurrence and IGNORES
    // last_status, so a failure stamped after 09:00 leaves due=false until
    // tomorrow. The timeline has to say that.
    const steps = buildRunTimeline(failedLr, {
      last_run: '2026-08-03T19:25:52',
      last_status: 'error',
      next_run: '2026-08-04T09:00:00',
      due: false,
      cadence: 'daily 09:00',
    });
    const step = steps.find((s) => s.id === 'no-auto-retry');
    expect(step).toBeDefined();
    expect(step!.kind).toBe('skip');
    expect(step!.vars!.next).toBe('2026-08-04 09:00:00');
    // It must come BEFORE the "next schedule window" line it is qualifying.
    expect(steps.findIndex((s) => s.id === 'no-auto-retry')).toBeLessThan(
      steps.findIndex((s) => s.id === 'next'),
    );
  });

  it('stays quiet when the job is due again (the tick WILL pick it up)', () => {
    const steps = buildRunTimeline(failedLr, {
      last_run: '2026-08-03T08:00:00',
      last_status: 'error',
      next_run: '2026-08-04T09:00:00',
      due: true,
      cadence: 'daily 09:00',
    });
    expect(steps.find((s) => s.id === 'no-auto-retry')).toBeUndefined();
  });

  it('stays quiet for a completed run', () => {
    const steps = buildRunTimeline(
      { ...failedLr, status: 'completed', error: undefined, processed: 10 },
      { next_run: '2026-08-04T09:00:00', due: false, cadence: 'daily 09:00' },
    );
    expect(steps.find((s) => s.id === 'no-auto-retry')).toBeUndefined();
  });

  it('renders the bilingual copy with no leftover placeholders', () => {
    for (const dict of [en, zh]) {
      const text = fill(dict['timeline.noAutoRetry'], { next: '2026-08-04 09:00:00' });
      expect(text).toContain('2026-08-04 09:00:00');
      expect(text).not.toMatch(/\{[a-zA-Z]+\}/);
    }
  });

  it('surfaces a rejected start instead of swallowing it', () => {
    for (const dict of [en, zh]) {
      const text = fill(dict['banner.retryFailed'], {
        error: 'a pipeline run is already in progress',
      });
      expect(text).toContain('a pipeline run is already in progress');
      expect(text).not.toMatch(/\{[a-zA-Z]+\}/);
    }
  });
});
