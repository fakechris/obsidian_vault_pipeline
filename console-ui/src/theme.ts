/** Theme system (DS dual scheme): `data-theme` on <html>, persisted in
 * localStorage['ovp-theme']. The initial value is applied by the boot
 * script in index.html BEFORE first paint (?theme param > localStorage >
 * prefers-color-scheme > light); this hook just mirrors and mutates it. */
import { useCallback, useState } from 'react';

export type Theme = 'light' | 'dark';

const THEME_KEY = 'ovp-theme';

function currentTheme(): Theme {
  return document.documentElement.getAttribute('data-theme') === 'dark'
    ? 'dark'
    : 'light';
}

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setThemeState] = useState<Theme>(currentTheme);

  const setTheme = useCallback((next: Theme) => {
    document.documentElement.setAttribute('data-theme', next);
    setThemeState(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* storage disabled */
    }
  }, []);

  return [theme, setTheme];
}
