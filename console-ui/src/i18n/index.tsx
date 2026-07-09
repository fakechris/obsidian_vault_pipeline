/** Hand-rolled i18n (design doc §0.6): default English, full Chinese via a
 * toggle. No i18next — two dictionaries + a context hook.
 *
 * Locale precedence at boot: ?lang= param (headless screenshots; NOT
 * persisted) > localStorage['ovp-lang'] > 'en'. Explicit toggles persist. */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { en } from './en';
import { zh } from './zh';

export type Lang = 'en' | 'zh';
export type MsgKey = keyof typeof en;

const DICTS: Record<Lang, Record<MsgKey, string>> = { en, zh };

const LANG_KEY = 'ovp-lang';

function initialLang(): Lang {
  try {
    const q = new URLSearchParams(window.location.search).get('lang');
    if (q === 'en' || q === 'zh') return q;
    const stored = localStorage.getItem(LANG_KEY);
    if (stored === 'en' || stored === 'zh') return stored;
  } catch {
    /* storage disabled */
  }
  return 'en';
}

interface I18nValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  /** Translate a key; `{name}` placeholders are filled from vars. */
  t: (key: MsgKey, vars?: Record<string, string | number>) => string;
}

const I18nContext = createContext<I18nValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(initialLang);

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try {
      localStorage.setItem(LANG_KEY, next);
    } catch {
      /* storage disabled */
    }
  }, []);

  const t = useCallback(
    (key: MsgKey, vars?: Record<string, string | number>) => {
      let msg: string = DICTS[lang][key] ?? en[key] ?? key;
      if (vars) {
        for (const [name, value] of Object.entries(vars)) {
          msg = msg.replaceAll(`{${name}}`, String(value));
        }
      }
      return msg;
    },
    [lang],
  );

  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error('useI18n must be used inside <I18nProvider>');
  return ctx;
}
