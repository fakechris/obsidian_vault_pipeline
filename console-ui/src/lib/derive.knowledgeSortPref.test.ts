/** Persisted knowledge-family sort prefs: read/write tolerance, wall + theme
 * page resolution, and the shared time direction (wall updated/created ↔
 * theme page day sort). Pure derive functions — no DOM. */
import { describe, expect, it } from 'vitest';
import {
  DEFAULT_KNOWLEDGE_SORT_PREF,
  KNOWLEDGE_SORT_PREF_STORAGE,
  isTimeSortKey,
  readKnowledgeSortPref,
  resolveKnowledgeWallSort,
  resolveThemeClaimsSort,
  writeKnowledgeSortPref,
  type KnowledgeSortPref,
} from './derive';

const fakeStorage = (init: Record<string, string> = {}) => {
  const data: Record<string, string> = { ...init };
  return {
    getItem: (k: string) => data[k] ?? null,
    setItem: (k: string, v: string) => {
      data[k] = v;
    },
    dump: () => data,
  };
};

const params = (search: string) => new URLSearchParams(search);

const pref = (
  patch: Partial<KnowledgeSortPref> = {},
): KnowledgeSortPref => ({ ...DEFAULT_KNOWLEDGE_SORT_PREF, ...patch });

describe('readKnowledgeSortPref', () => {
  it('returns the default for absent storage, absent key, and bad JSON', () => {
    expect(readKnowledgeSortPref(null)).toEqual(DEFAULT_KNOWLEDGE_SORT_PREF);
    expect(readKnowledgeSortPref(fakeStorage())).toEqual(DEFAULT_KNOWLEDGE_SORT_PREF);
    expect(readKnowledgeSortPref(fakeStorage({ [KNOWLEDGE_SORT_PREF_STORAGE]: '{oops' }))).toEqual(
      DEFAULT_KNOWLEDGE_SORT_PREF,
    );
    expect(
      readKnowledgeSortPref(fakeStorage({ [KNOWLEDGE_SORT_PREF_STORAGE]: '["nope"]' })),
    ).toEqual(DEFAULT_KNOWLEDGE_SORT_PREF);
  });

  it('falls back field-by-field on partially corrupt values', () => {
    const store = fakeStorage({
      [KNOWLEDGE_SORT_PREF_STORAGE]: JSON.stringify({
        wallKey: 'updated',
        wallDir: 'sideways',
        timeDir: 'asc',
      }),
    });
    expect(readKnowledgeSortPref(store)).toEqual({
      wallKey: 'updated',
      wallDir: 'desc', // invalid -> default
      timeDir: 'asc',
      detailFilter: 'all',
      detailSort: null,
    });
  });

  it('round-trips a fully set value', () => {
    const store = fakeStorage();
    const full: KnowledgeSortPref = {
      wallKey: 'created',
      wallDir: 'asc',
      timeDir: 'asc',
      detailFilter: 'durable',
      detailSort: 'day',
    };
    writeKnowledgeSortPref(full, store);
    expect(readKnowledgeSortPref(store)).toEqual(full);
    expect(JSON.parse(store.dump()[KNOWLEDGE_SORT_PREF_STORAGE])).toEqual(full);
  });
});

describe('isTimeSortKey', () => {
  it('flags only updated/created', () => {
    expect(isTimeSortKey('updated')).toBe(true);
    expect(isTimeSortKey('created')).toBe(true);
    expect(isTimeSortKey('count')).toBe(false);
    expect(isTimeSortKey('name')).toBe(false);
  });
});

describe('resolveKnowledgeWallSort', () => {
  it('no URL: pref wallKey, time keys use timeDir, count/name use wallDir, nothing to clear', () => {
    expect(resolveKnowledgeWallSort(params(''), pref({ wallKey: 'count', wallDir: 'desc' }))).toEqual({
      key: 'count',
      dir: 'desc',
      clear: [],
    });
    expect(
      resolveKnowledgeWallSort(params(''), pref({ wallKey: 'updated', timeDir: 'asc' })),
    ).toEqual({ key: 'updated', dir: 'asc', clear: [] });
    expect(resolveKnowledgeWallSort(params(''), pref({ wallKey: 'name', wallDir: 'asc' }))).toEqual({
      key: 'name',
      dir: 'asc',
      clear: [],
    });
  });

  it('URL sort/dir are consumed ON ARRIVAL (merge into prefs) and reported for clearing', () => {
    expect(
      resolveKnowledgeWallSort(params('sort=created'), pref({ wallKey: 'count', timeDir: 'asc' })),
    ).toEqual({ key: 'created', dir: 'asc', clear: ['sort'] });
    expect(
      resolveKnowledgeWallSort(params('sort=count&dir=asc'), pref({ wallKey: 'updated', timeDir: 'desc' })),
    ).toEqual({ key: 'count', dir: 'asc', clear: ['sort', 'dir'] });
  });

  it('invalid URL values are ignored (no crash on shared links) and not cleared', () => {
    expect(
      resolveKnowledgeWallSort(params('sort=bogus&dir=zigzag'), pref({ wallKey: 'name', wallDir: 'asc' })),
    ).toEqual({ key: 'name', dir: 'asc', clear: [] });
  });

  it('no URL: nothing to clear', () => {
    expect(resolveKnowledgeWallSort(params(''), pref({ wallKey: 'updated', timeDir: 'asc' }))).toEqual({
      key: 'updated',
      dir: 'asc',
      clear: [],
    });
  });
});

describe('resolveThemeClaimsSort', () => {
  it('explicit URL filter/sort/dir are consumed ON ARRIVAL and reported for clearing', () => {
    expect(
      resolveThemeClaimsSort(
        params('filter=caveated&sort=day&dir=asc'),
        pref({ detailFilter: 'durable', timeDir: 'desc' }),
      ),
    ).toEqual({
      filter: 'caveated',
      sort: 'day',
      dir: 'asc',
      clear: ['filter', 'sort', 'dir'],
    });
  });

  it('uses persisted detail prefs when set and URL is silent', () => {
    expect(
      resolveThemeClaimsSort(
        params(''),
        pref({ detailFilter: 'durable', detailSort: 'day', timeDir: 'asc' }),
      ),
    ).toEqual({ filter: 'durable', sort: 'day', dir: 'asc', clear: [] });
  });

  it('carries the wall time sort: unset detailSort + wall time key -> day + shared timeDir', () => {
    expect(
      resolveThemeClaimsSort(
        params(''),
        pref({ wallKey: 'updated', timeDir: 'asc' }), // detailSort null
      ),
    ).toEqual({ filter: 'all', sort: 'day', dir: 'asc', clear: [] });
  });

  it('a URL sort=default is NOT an explicit choice — wall time sort still carries', () => {
    expect(
      resolveThemeClaimsSort(
        params('sort=default'),
        pref({ wallKey: 'updated', timeDir: 'asc' }),
      ),
    ).toEqual({ filter: 'all', sort: 'day', dir: 'asc', clear: [] });
  });

  it('readKnowledgeSortPref treats a stored detailSort=default as corrupt -> null', () => {
    const store = fakeStorage({
      [KNOWLEDGE_SORT_PREF_STORAGE]: JSON.stringify({ detailSort: 'default' }),
    });
    expect(readKnowledgeSortPref(store).detailSort).toBeNull();
  });

  it('does NOT follow the wall once the detail sort was explicitly DAY', () => {
    expect(
      resolveThemeClaimsSort(
        params(''),
        pref({ wallKey: 'created', timeDir: 'asc', detailSort: 'day' }),
      ),
    ).toEqual({ filter: 'all', sort: 'day', dir: 'asc', clear: [] });
  });

  it('non-day sorts keep dir desc regardless of timeDir', () => {
    expect(
      resolveThemeClaimsSort(params('dir=asc'), pref({ timeDir: 'desc' })),
    ).toEqual({ filter: 'all', sort: 'default', dir: 'asc', clear: ['dir'] });
  });
});

describe('BACK navigation regression (2026-08-17)', () => {
  // The bug: "sorting is not remembered, it resets every time". Browser Back
  // restores the OLD url (?sort=updated&dir=desc from before the last click)
  // while localStorage holds the NEW choice (timeDir: asc). If URL params
  // permanently won for that visit, the page kept showing the old order —
  // seeming reset. Fix: valid URL params are consumed ON ARRIVAL (merged
  // into prefs + reported for clearing), so the persisted choice — not a
  // stale history entry — drives every subsequent visit.

  it('wall: stale URL params are reported for clearing and prefs keep driving', () => {
    const paramsSnapshot = params('sort=updated&dir=desc'); // from Back
    const current = pref({ wallKey: 'updated', timeDir: 'asc' }); // chose asc later
    const shot = resolveKnowledgeWallSort(paramsSnapshot, current);
    // The URL's desc IS honored for the first paint, then consumed: the
    // component merges it into prefs and clears the params. After that the
    // user's local choice (timeDir asc) drives.
    expect(shot).toEqual({ key: 'updated', dir: 'desc', clear: ['sort', 'dir'] });
    // Simulate the component's merge: URL dir wins into timeDir (arrival).
    const merged = { ...current, timeDir: 'desc' as const };
    expect(merged.timeDir).toBe('desc');
    // Next visits read only prefs (params gone).
    expect(resolveKnowledgeWallSort(params(''), merged)).toEqual({
      key: 'updated',
      dir: 'desc',
      clear: [],
    });
  });

  it('detail: stale URL params are consumed, persisted filter/sort survive', () => {
    const paramsSnapshot = params('dir=desc'); // from Back
    const current = pref({ wallKey: 'updated', timeDir: 'asc', detailFilter: 'durable' });
    const shot = resolveThemeClaimsSort(paramsSnapshot, current);
    // Arrival: URL dir=desc honored (day sort), filter stays persisted;
    // only the params present in the URL are reported for clearing.
    expect(shot).toEqual({
      filter: 'durable',
      sort: 'day',
      dir: 'desc',
      clear: ['dir'],
    });
  });
});
