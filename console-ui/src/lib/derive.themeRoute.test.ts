/** Theme routing by stable community id — the fix for the "renamed theme
 * dead-ends the page" bug. The route key is the community id (mutable label
 * is presentation only), so a `crystal-themes` relabel that keeps the id
 * must not orphan existing theme URLs. Pure derive functions — no DOM. */
import { describe, expect, it } from 'vitest';
import {
  UNCLASSIFIED_ID,
  UNTHEMED_SEGMENT,
  claimMatchesThemeKey,
  themeClaims,
  themeFromRoute,
  themeRoute,
  type ThemeRouteKey,
} from './derive';
import type { ClaimRow } from './types';

const claim = (
  id: string,
  theme: string,
  themeId: number | null,
  status: ClaimRow['status'] = 'durable',
): ClaimRow => ({
  claim_id: id,
  claim: `claim ${id}`,
  theme,
  theme_id: themeId,
  status,
  sources: [],
});

describe('themeRoute / themeFromRoute round-trip', () => {
  it('routes by the stable community id when present', () => {
    expect(themeRoute({ id: 18, theme: 'Obsidian vault' })).toBe(
      '/knowledge/theme/18',
    );
  });

  it('encodes the Unclassified sentinel id directly (not ~none)', () => {
    expect(themeRoute({ id: UNCLASSIFIED_ID, theme: 'Unclassified' })).toBe(
      '/knowledge/theme/-1',
    );
  });

  it('falls back to the label (with ~none for empty) on pre-theme projections', () => {
    expect(themeRoute({ id: null, theme: 'Agent memory' })).toBe(
      '/knowledge/theme/Agent%20memory',
    );
    expect(themeRoute({ id: null, theme: '' })).toBe(
      `/knowledge/theme/${UNTHEMED_SEGMENT}`,
    );
  });

  it('parses numeric params (incl. -1) as id keys, ~none and strings as labels', () => {
    expect(themeFromRoute('18')).toEqual<ThemeRouteKey>({ kind: 'id', id: 18 });
    expect(themeFromRoute('-1')).toEqual<ThemeRouteKey>({
      kind: 'id',
      id: -1,
    });
    expect(themeFromRoute(UNTHEMED_SEGMENT)).toEqual<ThemeRouteKey>({
      kind: 'label',
      label: '',
    });
    expect(themeFromRoute('Obsidian%20second%20brain')).toEqual<ThemeRouteKey>({
      kind: 'label',
      label: 'Obsidian%20second%20brain',
    });
    expect(themeFromRoute(undefined)).toEqual<ThemeRouteKey>({
      kind: 'label',
      label: '',
    });
  });
});

describe('themeClaims survives a relabel (the bug being fixed)', () => {
  // Same community (id 18), two label generations across a crystal-themes
  // relabel. Routing by id keeps the page populated; routing by the old
  // label would dead-end (the original bug).
  const claims = [
    claim('c1', 'Obsidian second brain & AI-assisted vault', 18),
    claim('c2', 'Obsidian second brain & AI-assisted vault', 18, 'caveated'),
    claim('c3', 'Agent memory', 0),
  ];

  it('an id route resolves claims even after the label changed', () => {
    // The CURRENT projection relabeled community 18 to "Obsidian vault &
    // personal note-taking" — the claims now carry the new label but the
    // SAME id. An id-18 route still finds them.
    const relabeled = [
      claim('c1', 'Obsidian vault & personal note-taking', 18),
      claim('c2', 'Obsidian vault & personal note-taking', 18, 'caveated'),
    ];
    const hits = themeClaims(relabeled, { kind: 'id', id: 18 });
    expect(hits.map((c) => c.claim_id)).toEqual(['c1', 'c2']);
  });

  it('a legacy label route matches the (mutable) label string only', () => {
    const hits = themeClaims(claims, {
      kind: 'label',
      label: 'Obsidian second brain & AI-assisted vault',
    });
    expect(hits.map((c) => c.claim_id)).toEqual(['c1', 'c2']);
  });

  it('a label route does NOT match a relabeled community (id is what survives)', () => {
    const relabeled = [
      claim('c1', 'Obsidian vault & personal note-taking', 18),
    ];
    expect(
      themeClaims(relabeled, {
        kind: 'label',
        label: 'Obsidian second brain & AI-assisted vault',
      }),
    ).toEqual([]);
  });

  it('the Unclassified id (-1) matches claims mapped to the sentinel', () => {
    const unclassified = [claim('c1', 'Unclassified', UNCLASSIFIED_ID)];
    expect(
      themeClaims(unclassified, { kind: 'id', id: UNCLASSIFIED_ID }).map(
        (c) => c.claim_id,
      ),
    ).toEqual(['c1']);
  });
});

describe('claimMatchesThemeKey', () => {
  it('id key checks theme_id; label key checks theme', () => {
    const c = claim('c1', 'Agent memory', 0);
    expect(claimMatchesThemeKey(c, { kind: 'id', id: 0 })).toBe(true);
    expect(claimMatchesThemeKey(c, { kind: 'id', id: 1 })).toBe(false);
    expect(claimMatchesThemeKey(c, { kind: 'label', label: 'Agent memory' })).toBe(true);
    expect(claimMatchesThemeKey(c, { kind: 'label', label: 'Other' })).toBe(false);
  });
});
