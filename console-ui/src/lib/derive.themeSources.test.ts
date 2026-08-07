/** Distinct-source accounting on the knowledge wall + the source page's
 * "supports crystal knowledge" rail. Pure derive functions — no DOM. */
import { describe, expect, it } from 'vitest';
import { caseCanonicalIds, sourceThemes, themeWall } from './derive';
import type { ClaimRow } from './types';

const claim = (
  id: string,
  theme: string,
  sources: string[],
  status: ClaimRow['status'] = 'durable',
): ClaimRow => ({
  claim_id: id,
  claim: `claim ${id}`,
  theme,
  status,
  sources,
});

describe('themeWall source counts', () => {
  it('counts DISTINCT case ids per theme, not claim rows', () => {
    const wall = themeWall(
      [
        // Three claims, all resting on the same single document.
        claim('c1', 'Mono', ['case-a']),
        claim('c2', 'Mono', ['case-a']),
        claim('c3', 'Mono', ['case-a'], 'caveated'),
        // Two claims across three documents (one shared).
        claim('c4', 'Broad', ['case-a', 'case-b']),
        claim('c5', 'Broad', ['case-b', 'case-c']),
      ],
      [],
    );
    const byTheme = new Map(wall.map((g) => [g.theme, g]));
    expect(byTheme.get('Mono')?.sources).toBe(1);
    expect(byTheme.get('Mono')?.total).toBe(3);
    expect(byTheme.get('Broad')?.sources).toBe(3);
  });

  it('ledger-only themes (no indexed claims yet) report zero sources', () => {
    const wall = themeWall([], [{ theme: 'Ledger only', count: 4 }]);
    expect(wall[0].sources).toBe(0);
    expect(wall[0].total).toBe(4);
  });

  it('re-captures of the same document collapse to ONE source via the canonical map', () => {
    // Live-vault case (2026-08-07): the same article clipped twice a month
    // apart → two case ids, two content shas, ONE url. The theme showed
    // "2 sources" while genuinely resting on one document.
    const model = {
      sources: [
        { sha256: 'sha-april', url: 'https://example.com/a' },
        { sha256: 'sha-may', url: 'https://example.com/a' },
        { sha256: 'sha-other', url: 'https://example.com/b' },
      ],
      packs: [
        { pack_dir: '40-Resources/Reader/case-april', source_sha256: 'sha-april' },
        { pack_dir: '40-Resources/Reader/case-may', source_sha256: 'sha-may' },
        { pack_dir: '40-Resources/Reader/case-other', source_sha256: 'sha-other' },
        // Legacy pack without a source link falls back to the sha, then case id.
        { pack_dir: '40-Resources/Reader/case-orphan', source_sha256: null },
      ],
    };
    const canonical = caseCanonicalIds(model);
    expect(canonical.get('case-april')).toBe('https://example.com/a');
    expect(canonical.get('case-may')).toBe('https://example.com/a');
    expect(canonical.get('case-orphan')).toBe('case-orphan');

    const wall = themeWall(
      [
        claim('c1', 'T', ['case-april', 'case-may']),
        claim('c2', 'T', ['case-april', 'case-other']),
      ],
      [],
      canonical,
    );
    expect(wall[0].sources).toBe(2);
  });

  it('rejected/superseded claims do not contribute sources', () => {
    const wall = themeWall(
      [
        claim('c1', 'T', ['case-a']),
        { ...claim('c2', 'T', ['case-b']), status: 'rejected' as ClaimRow['status'] },
      ],
      [],
    );
    expect(wall[0].sources).toBe(1);
  });
});

describe('sourceThemes', () => {
  it('groups a source\'s citing claims by theme, count desc then name', () => {
    const themes = sourceThemes([
      claim('c1', 'Beta', ['case-a']),
      claim('c2', 'Beta', ['case-a'], 'caveated'),
      claim('c3', 'Alpha', ['case-a']),
      claim('c4', 'Zeta', ['case-a']),
      { ...claim('c5', 'Zeta', ['case-a']), status: 'rejected' as ClaimRow['status'] },
    ]);
    expect(themes).toEqual([
      { theme: 'Beta', count: 2 },
      { theme: 'Alpha', count: 1 },
      { theme: 'Zeta', count: 1 },
    ]);
  });

  it('unthemed claims land in the "" bucket (displayed as Unclassified)', () => {
    const rows = [claim('c1', '', ['case-a'])];
    expect(sourceThemes(rows)).toEqual([{ theme: '', count: 1 }]);
  });

  it('returns empty for a source no active claim cites', () => {
    expect(sourceThemes([])).toEqual([]);
  });
});
