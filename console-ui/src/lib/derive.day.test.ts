import { describe, expect, it } from 'vitest';
import {
  activityDates,
  claimActivityDate,
  dayView,
  packActivityDate,
  shiftIsoDay,
} from './derive';
import type { IndexModel } from './types';

function baseModel(overrides: Partial<IndexModel> = {}): IndexModel {
  return {
    schema: 'ovp.index/v2',
    date: '2026-07-26',
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
    ops: { blocked_sources: [], queue_depth: 0 },
    ...overrides,
  };
}

describe('claimActivityDate / packActivityDate', () => {
  it('parses daily and compact crystal run ids', () => {
    expect(claimActivityDate({ claim_id: 'a', claim: 'x', status: 'durable', sources: [], run_id: 'daily-2026-07-26' })).toBe(
      '2026-07-26',
    );
    expect(
      claimActivityDate({
        claim_id: 'b',
        claim: 'y',
        status: 'durable',
        sources: [],
        run_id: 'crystal-full-20260709',
      }),
    ).toBe('2026-07-09');
    expect(
      claimActivityDate({
        claim_id: 'c',
        claim: 'z',
        status: 'durable',
        sources: [],
        run_id: 'run-8b0f5d4e',
      }),
    ).toBeNull();
  });

  it('pulls pack day from pack_dir when date field missing', () => {
    expect(
      packActivityDate({
        pack_dir: '40-Resources/Reader/2026-06-15_Title-abc',
        title: 'T',
        units: 1,
        cards: 1,
        json_repaired: false,
        card_titles: [],
      }),
    ).toBe('2026-06-15');
  });
});

describe('dayView', () => {
  it('aggregates runs, reads, packs, and date-linked claims for a day', () => {
    const model = baseModel({
      runs: [
        {
          run_id: 'daily-2026-07-26',
          date: '2026-07-26',
          report_file: 'r.json',
          succeeded: 2,
          failed: 0,
          skipped: 0,
          blocked: 0,
          ingested: 1,
          pinboard_new: 0,
          lifecycle_warnings: 0,
        },
      ],
      sources: [
        {
          sha256: 's1',
          status: 'processed',
          title: 'Read A',
          date: '2026-07-26',
          last_run_id: 'daily-2026-07-26',
          pack_dir: '40-Resources/Reader/2026-07-26_A-s1',
          fail_count: 0,
        },
        {
          sha256: 's2',
          status: 'processed',
          title: 'Old',
          date: '2026-07-01',
          last_run_id: 'daily-2026-07-01',
          fail_count: 0,
        },
      ],
      packs: [
        {
          pack_dir: '40-Resources/Reader/2026-07-26_A-s1',
          title: 'Pack A',
          units: 10,
          cards: 3,
          json_repaired: false,
          card_titles: [],
          source_sha256: 's1',
        },
      ],
      claims: [
        {
          claim_id: 'c1',
          claim: 'A durable claim',
          status: 'durable',
          sources: [],
          run_id: 'daily-2026-07-26',
        },
        {
          claim_id: 'c2',
          claim: 'Undated',
          status: 'durable',
          sources: [],
          run_id: 'run-hash',
        },
      ],
    });

    const v = dayView(model, '2026-07-26');
    expect(v.captured).toBe(1);
    expect(v.read).toBe(2);
    expect(v.sourcesRead).toHaveLength(1);
    expect(v.packs).toHaveLength(1);
    expect(v.claims).toHaveLength(1);
    expect(v.claims[0].claim_id).toBe('c1');
    expect(v.heat).toBeGreaterThan(0);

    const dates = activityDates(model);
    expect(dates.has('2026-07-26')).toBe(true);
    expect(dates.has('2026-07-01')).toBe(true);
  });
});

describe('shiftIsoDay', () => {
  it('steps across month boundaries in UTC', () => {
    expect(shiftIsoDay('2026-07-01', -1)).toBe('2026-06-30');
    expect(shiftIsoDay('2026-02-28', 1)).toBe('2026-03-01');
  });
});
