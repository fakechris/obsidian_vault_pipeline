/** B-axis time derivation + filtering/sorting on the knowledge surface:
 * claimDay / sourceDay / sourceDaysByCase (theme cards + detail list),
 * themeWall's firstSeen/lastSeen, the wall's time sort keys, and the
 * detail list's status filter + day sort. Pure derive functions — no DOM. */
import { describe, expect, it } from 'vitest';
import {
  claimDay,
  filterClaimsByStatus,
  sortThemeClaims,
  sortThemeWall,
  sourceDay,
  sourceDaysByCase,
  themeWall,
  uniqueClaimKeys,
  type ClaimStatusFilter,
  type ThemeClaimSortKey,
  type ThemeGroup,
  type ThemeSortDir,
} from './derive';
import type { ClaimRow, IndexModel, SourceRow } from './types';

const claim = (
  id: string,
  theme: string,
  sources: string[],
  patch: Partial<ClaimRow> = {},
): ClaimRow => ({
  claim_id: id,
  claim: `claim ${id}`,
  theme,
  theme_id: null,
  status: 'durable',
  sources,
  ...patch,
});

const src = (sha: string, patch: Partial<SourceRow> = {}): SourceRow => ({
  sha256: sha,
  status: 'processed',
  fail_count: 0,
  ...patch,
});

describe('claimDay', () => {
  it('prefers run_date over source days', () => {
    const sourceDays = new Map([['case-a', '2026-07-01']]);
    expect(
      claimDay(claim('c1', 'T', ['case-a'], { run_date: '2026-06-10' }), sourceDays),
    ).toBe('2026-06-10');
  });

  it('falls back to the NEWEST cited source day when run_id has no date', () => {
    const sourceDays = new Map([
      ['case-a', '2026-07-01'],
      ['case-b', '2026-07-20'],
    ]);
    expect(claimDay(claim('c1', 'T', ['case-a', 'case-b']), sourceDays)).toBe(
      '2026-07-20',
    );
  });

  it('returns null when the claim and all its sources are undated', () => {
    expect(claimDay(claim('c1', 'T', ['case-a']), null)).toBeNull();
    expect(
      claimDay(claim('c1', 'T', ['case-a']), new Map([['case-a', 'x']].slice(0, 0) as [string, string][])),
    ).toBeNull();
  });
});

describe('sourceDay / sourceDaysByCase', () => {
  it('picks processed_on over captured_on over legacy date', () => {
    expect(
      sourceDay(src('s', {
        processed_on: '2026-07-28',
        captured_on: '2026-07-01',
        date: '2026-06-01',
      })),
    ).toBe('2026-07-28');
    expect(sourceDay(src('s', { captured_on: '2026-07-01' }))).toBe('2026-07-01');
    expect(sourceDay(src('s', { date: '2026-06-01' }))).toBe('2026-06-01');
    expect(sourceDay(src('s'))).toBeNull();
  });

  it('maps claim case ids (pack dir tails) to their source days via shas', () => {
    const model: IndexModel = {
      schema: 'x',
      date: '2026-07-28',
      totals: {
        sources: 0, queued: 0, processed: 0, failed: 0, blocked: 0,
        needs_content: 0, unparseable: 0, duplicates: 0, packs: 0,
        claims_durable: 0, claims_caveated: 0, runs: 0,
      },
      sources: [
        src('sha-a', { processed_on: '2026-07-28' }),
        src('sha-b', { date: '2026-06-15' }),
      ],
      packs: [
        {
          pack_dir: '40-Resources/Reader/case-a', title: 'a', units: 1, cards: 0,
          json_repaired: false, card_titles: [], source_sha256: 'sha-a',
        },
        {
          pack_dir: '40-Resources/Reader/case-b', title: 'b', units: 1, cards: 0,
          json_repaired: false, card_titles: [], source_sha256: 'sha-b',
        },
        {
          pack_dir: '40-Resources/Reader/case-orphan', title: 'o', units: 1, cards: 0,
          json_repaired: false, card_titles: [],
        },
      ],
      claims: [],
      runs: [],
      ops: { blocked_sources: [], queue_depth: 0 },
    };
    const days = sourceDaysByCase(model);
    expect(days.get('case-a')).toBe('2026-07-28');
    expect(days.get('case-b')).toBe('2026-06-15');
    expect(days.has('case-orphan')).toBe(false);
  });
});

describe('themeWall firstSeen/lastSeen', () => {
  it('tracks the earliest and latest claim day per theme', () => {
    const sourceDays = new Map([['case-a', '2026-07-01']]);
    const wall = themeWall(
      [
        claim('c1', 'T', ['case-a'], { run_date: '2026-07-09' }),
        claim('c2', 'T', ['case-a'], { status: 'caveated' }),
        claim('c3', 'T', ['case-a'], { run_date: '2026-06-15' }),
      ],
      [],
      undefined,
      sourceDays,
    );
    expect(wall[0].firstSeen).toBe('2026-06-15');
    expect(wall[0].lastSeen).toBe('2026-07-09');
  });

  it('leaves the time fields absent when no claim has a date', () => {
    const wall = themeWall(
      [claim('c1', 'T', ['case-a'])],
      [],
      undefined,
      null,
    );
    expect(wall[0].firstSeen).toBeUndefined();
    expect(wall[0].lastSeen).toBeUndefined();
  });
});

describe('sortThemeWall time keys', () => {
  const wall: ThemeGroup[] = [
    {
      id: null, theme: 'Old', total: 1, durable: 1, caveated: 0, sources: 1,
      firstSeen: '2026-06-01', lastSeen: '2026-06-01',
    },
    {
      id: null, theme: 'New', total: 1, durable: 1, caveated: 0, sources: 1,
      firstSeen: '2026-07-01', lastSeen: '2026-07-09',
    },
    { id: null, theme: 'Undated A', total: 1, durable: 1, caveated: 0, sources: 1 },
    { id: null, theme: 'Undated B', total: 1, durable: 1, caveated: 0, sources: 1 },
  ];

  const days = (key: 'updated' | 'created', dir: ThemeSortDir) =>
    sortThemeWall(wall, key, dir).map((g) => g.theme);

  it('updated desc puts the most recently active theme first', () => {
    expect(days('updated', 'desc')).toEqual(['New', 'Old', 'Undated A', 'Undated B']);
  });

  it('updated asc flips known days but keeps undated last', () => {
    expect(days('updated', 'asc')).toEqual(['Old', 'New', 'Undated A', 'Undated B']);
  });

  it('created desc orders by earliest claim day, undated last', () => {
    expect(days('created', 'desc')).toEqual(['New', 'Old', 'Undated A', 'Undated B']);
  });
});

describe('filterClaimsByStatus', () => {
  const rows = [
    claim('c1', 'T', []),
    claim('c2', 'T', [], { status: 'caveated' }),
    claim('c3', 'T', [], { status: 'rejected' as ClaimRow['status'] }),
  ];

  const ids = (f: ClaimStatusFilter) =>
    filterClaimsByStatus(rows, f).map((c) => c.claim_id);

  it('all keeps every row (filters nothing)', () => {
    expect(ids('all')).toEqual(['c1', 'c2', 'c3']);
  });

  it('durable / caveated keep only that band', () => {
    expect(ids('durable')).toEqual(['c1']);
    expect(ids('caveated')).toEqual(['c2']);
  });
});

describe('sortThemeClaims', () => {
  const rows = [
    claim('c1', 'T', ['case-a'], { run_date: '2026-07-09' }),
    claim('c2', 'T', ['case-a'], { status: 'caveated', run_date: '2026-06-15' }),
    claim('c3', 'T', ['case-a'], { status: 'caveated' }),
    claim('c4', 'T', ['case-a'], { run_date: '2026-07-01' }),
  ];
  const sourceDays = new Map([['case-a', '2026-07-20']]);

  const ids = (key: ThemeClaimSortKey, dir: ThemeSortDir) =>
    sortThemeClaims(rows, key, dir, sourceDays).map((c) => c.claim_id);

  it("'default' reproduces durable-first, claim_id order", () => {
    expect(ids('default', 'desc')).toEqual(['c1', 'c4', 'c2', 'c3']);
  });

  // c3 has no run_date but cites case-a (source day 2026-07-20) — it gets the
  // newest day via the source fallback, proving the fallback feeds the sort.
  it('day desc orders by claim day', () => {
    expect(ids('day', 'desc')).toEqual(['c3', 'c1', 'c4', 'c2']);
  });

  it('day asc flips known days', () => {
    expect(ids('day', 'asc')).toEqual(['c2', 'c4', 'c1', 'c3']);
  });

  it('does not mutate its input', () => {
    const before = rows.map((c) => c.claim_id);
    sortThemeClaims(rows, 'day', 'asc', sourceDays);
    expect(rows.map((c) => c.claim_id)).toEqual(before);
  });
});

describe('uniqueClaimKeys', () => {
  it('keys are unique even when claim_id collides and claim_key is missing', () => {
    const a = claim('dup-id', 'T', [], { claim_key: 'ck-a' });
    const b = claim('dup-id', 'T', [], { claim_key: 'ck-b' });
    const legacy = claim('dup-id', 'T', []); // no claim_key
    const keys = uniqueClaimKeys([a, b, legacy]);
    const set = new Set([
      keys.get(a),
      keys.get(b),
      keys.get(legacy),
    ]);
    expect(set.size).toBe(3);
  });

  it('keys computed against the full list stay unique for a re-sorted subset', () => {
    const rows = [
      claim('c1', 'T', [], { run_date: '2026-06-01' }),
      claim('c2', 'T', [], { run_date: '2026-07-01' }),
      claim('c1', 'T', [], { run_date: '2026-05-01', claim_key: 'ck-x' }),
    ];
    // The component computes keys ONCE against the stable `claims` order,
    // then re-sorts/filters with the SAME row objects — so the WeakMap lookup
    // must stay unique and stable for every row in the new order (that is
    // exactly what React reconciliation needs to move the DOM correctly).
    const key = uniqueClaimKeys(rows);
    const asc = sortThemeClaims([...rows], 'day', 'asc', null);
    const keys = asc.map((r) => key.get(r));
    expect(new Set(keys).size).toBe(asc.length);
    // The dup-id rows share the id but got distinct keys.
    const dup = rows.filter((r) => r.claim_id === 'c1');
    expect(key.get(dup[0])).not.toBe(key.get(dup[1]));
  });
});
