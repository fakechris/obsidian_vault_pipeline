import { describe, expect, it } from 'vitest';
import { sortThemeWall, type ThemeGroup } from './derive';

const g = (theme: string, total: number): ThemeGroup => ({
  id: null,
  theme,
  total,
  durable: total,
  caveated: 0,
  sources: 1,
});

describe('sortThemeWall', () => {
  const wall = [g('beta', 5), g('alpha', 5), g('gamma', 12), g('delta', 1)];

  it('count desc reproduces the historical wall order (ties by name asc)', () => {
    expect(sortThemeWall(wall, 'count', 'desc').map((x) => x.theme)).toEqual([
      'gamma',
      'alpha',
      'beta',
      'delta',
    ]);
  });

  it('count asc flips the primary key but keeps name-asc ties', () => {
    expect(sortThemeWall(wall, 'count', 'asc').map((x) => x.theme)).toEqual([
      'delta',
      'alpha',
      'beta',
      'gamma',
    ]);
  });

  it('name sorts alphabetically both ways', () => {
    expect(sortThemeWall(wall, 'name', 'asc').map((x) => x.theme)).toEqual([
      'alpha',
      'beta',
      'delta',
      'gamma',
    ]);
    expect(sortThemeWall(wall, 'name', 'desc').map((x) => x.theme)).toEqual([
      'gamma',
      'delta',
      'beta',
      'alpha',
    ]);
  });

  it('does not mutate its input', () => {
    const before = wall.map((x) => x.theme);
    sortThemeWall(wall, 'name', 'desc');
    expect(wall.map((x) => x.theme)).toEqual(before);
  });
});
