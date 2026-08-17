/** Persisted knowledge-family sort prefs: read/write tolerance, wall + theme
 * page resolution, and the shared time direction (wall updated/created ↔
 * theme page day sort). Pure derive functions — no DOM. */
import { describe, expect, it } from 'vitest';
import {
  DEFAULT_KNOWLEDGE_SORT_PREF,
  KNOWLEDGE_SORT_PREF_KEY,
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
    expect(readKnowledgeSortPref(fakeStorage({ [KNOWLEDGE_SORT_PREF_KEY]: '{oops' }))).toEqual(
      DEFAULT_KNOWLEDGE_SORT_PREF,
    );
    expect(
      readKnowledgeSortPref(fakeStorage({ [KNOWLEDGE_SORT_PREF_KEY]: '["nope"]' })),
    ).toEqual(DEFAULT_KNOWLEDGE_SORT_PREF);
  });

  it('falls back field-by-field on partially corrupt values', () => {
    const store = fakeStorage({
      [KNOWLEDGE_SORT_PREF_KEY]: JSON.stringify({
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
    expect(JSON.parse(store.dump()[KNOWLEDGE_SORT_PREF_KEY])).toEqual(full);
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
  it('no URL: pref wallKey, time keys use timeDir, count/name use wallDir', () => {
    expect(resolveKnowledgeWallSort(params(''), pref({ wallKey: 'count', wallDir: 'desc' }))).toEqual({
      key: 'count',
      dir: 'desc',
    });
    expect(
      resolveKnowledgeWallSort(params(''), pref({ wallKey: 'updated', timeDir: 'asc' })),
    ).toEqual({ key: 'updated', dir: 'asc' });
    expect(resolveKnowledgeWallSort(params(''), pref({ wallKey: 'name', wallDir: 'asc' }))).toEqual({
      key: 'name',
      dir: 'asc',
    });
  });

  it('explicit URL sort wins; missing URL dir falls back per key type', () => {
    expect(
      resolveKnowledgeWallSort(params('sort=created'), pref({ wallKey: 'count', timeDir: 'asc' })),
    ).toEqual({ key: 'created', dir: 'asc' });
    expect(
      resolveKnowledgeWallSort(params('sort=count&dir=asc'), pref({ wallKey: 'updated', timeDir: 'desc' })),
    ).toEqual({ key: 'count', dir: 'asc' });
  });

  it('invalid URL values are ignored (no crash on shared links)', () => {
    expect(
      resolveKnowledgeWallSort(params('sort=bogus&dir=zigzag'), pref({ wallKey: 'name', wallDir: 'asc' })),
    ).toEqual({ key: 'name', dir: 'asc' });
  });
});

describe('resolveThemeClaimsSort', () => {
  it('explicit URL filter/sort/dir all win', () => {
    expect(
      resolveThemeClaimsSort(
        params('filter=caveated&sort=day&dir=asc'),
        pref({ detailFilter: 'durable', detailSort: 'default', timeDir: 'desc' }),
      ),
    ).toEqual({ filter: 'caveated', sort: 'day', dir: 'asc' });
  });

  it('uses persisted detail prefs when set and URL is silent', () => {
    expect(
      resolveThemeClaimsSort(
        params(''),
        pref({ detailFilter: 'durable', detailSort: 'day', timeDir: 'asc' }),
      ),
    ).toEqual({ filter: 'durable', sort: 'day', dir: 'asc' });
  });

  it('carries the wall time sort: unset detailSort + wall time key -> day + shared timeDir', () => {
    expect(
      resolveThemeClaimsSort(
        params(''),
        pref({ wallKey: 'updated', timeDir: 'asc' }), // detailSort null
      ),
    ).toEqual({ filter: 'all', sort: 'day', dir: 'asc' });
  });

  it('does NOT follow the wall once the detail sort was explicitly chosen', () => {
    expect(
      resolveThemeClaimsSort(
        params(''),
        pref({ wallKey: 'created', timeDir: 'asc', detailSort: 'default' }),
      ),
    ).toEqual({ filter: 'all', sort: 'default', dir: 'desc' });
  });

  it('non-day sorts keep dir desc regardless of timeDir', () => {
    expect(
      resolveThemeClaimsSort(params('sort=default&dir=asc'), pref({ timeDir: 'desc' })),
    ).toEqual({ filter: 'all', sort: 'default', dir: 'asc' });
  });
});
