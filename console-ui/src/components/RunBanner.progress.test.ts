/** Tests for the in-run progress banner text (OVP2 observability: live run
 * progress). The RunBanner component itself needs a DOM to render, and this
 * package's vitest runs in the `node` env (no jsdom / testing-library by
 * design). So we test the load-bearing decision + the exact bilingual template
 * interpolation the banner feeds `t(...)` — the same `{name}` replaceAll the
 * i18n provider does — proving the fraction and current-source render. */
import { describe, expect, it } from 'vitest';
import { en } from '../i18n/en';
import { zh } from '../i18n/zh';
import { isRunningWithProgress, lastRunBanner } from '../lib/derive';
import type { IndexModel, LastRunModel } from '../lib/types';

const NOW = Date.parse('2026-07-12T12:00:00Z');

/** Mirror the i18n provider's interpolation (index.tsx): `{name}` → vars[name].
 * Kept local so this stays a pure, DOM-free test. */
function fill(msg: string, vars: Record<string, string | number>): string {
  let out = msg;
  for (const [k, v] of Object.entries(vars)) out = out.replaceAll(`{${k}}`, String(v));
  return out;
}

function runningModel(lr: Partial<LastRunModel>): IndexModel {
  const last_run: LastRunModel = {
    run_id: 'r',
    started_at: '2026-07-12T11:48:00Z',
    status: 'running',
    ...lr,
  };
  return {
    schema: 'ovp.index/v2',
    date: '2026-07-12',
    totals: {
      sources: 0, queued: 0, processed: 0, failed: 0, blocked: 0,
      needs_content: 0, unparseable: 0, duplicates: 0, packs: 0,
      claims_durable: 0, claims_caveated: 0, runs: 0,
    },
    sources: [], packs: [], claims: [], runs: [],
    ops: { blocked_sources: [], queue_depth: 0, last_run },
  };
}

describe('RunBanner in-run progress text', () => {
  it('renders "18/90 (current) · started …" in English when progress is present', () => {
    const b = lastRunBanner(
      runningModel({ processed_so_far: 18, total_planned: 90, current: 'Great Article' }),
      NOW,
    );
    expect(isRunningWithProgress(b)).toBe(true);

    const text = fill(en['banner.runningProgress'], {
      done: b.processedSoFar!,
      total: b.totalPlanned!,
      current: b.current!,
      ago: '12m ago',
    });
    expect(text).toContain('18/90');
    expect(text).toContain('Great Article');
    // No unfilled placeholders leak through.
    expect(text).not.toMatch(/\{[a-z]+\}/);
  });

  it('renders the bilingual (中文) fraction too', () => {
    const b = lastRunBanner(
      runningModel({ processed_so_far: 5, total_planned: 40, current: '某篇文章' }),
      NOW,
    );
    const text = fill(zh['banner.runningProgress'], {
      done: b.processedSoFar!,
      total: b.totalPlanned!,
      current: b.current!,
      ago: '12 分钟前',
    });
    expect(text).toContain('5/40');
    expect(text).toContain('某篇文章');
    expect(text).not.toMatch(/\{[a-z]+\}/);
  });

  it('uses the no-current template when the heartbeat has a fraction but no source name', () => {
    const b = lastRunBanner(
      runningModel({ processed_so_far: 2, total_planned: 9 }),
      NOW,
    );
    expect(isRunningWithProgress(b)).toBe(true);
    expect(b.current).toBeNull();
    const text = fill(en['banner.runningProgressNoCurrent'], {
      done: b.processedSoFar!,
      total: b.totalPlanned!,
      ago: '1m ago',
    });
    expect(text).toContain('2/9');
    expect(text).not.toContain('(');
    expect(text).not.toMatch(/\{[a-z]+\}/);
  });

  it('computes a clamped percentage for the progress bar width', () => {
    const b = lastRunBanner(
      runningModel({ processed_so_far: 18, total_planned: 90 }),
      NOW,
    );
    const pct = Math.min(100, Math.round((b.processedSoFar! / b.totalPlanned!) * 100));
    expect(pct).toBe(20);
  });
});
