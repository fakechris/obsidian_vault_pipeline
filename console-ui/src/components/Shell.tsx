/** OVP2 portal shell — DS top-nav shell (design §6 ext.1): centered column
 * max-width 1180, rounded outer shell on --bg, brand "ovp2." with accent
 * period, text-link nav, status dot + theme + language toggles on the right.
 * Single-locale UI — no inline bilingual pairs (design §0.6).
 *
 * B3: hosts the global search overlay — ⌘K / Ctrl+K anywhere (and the ⌕
 * button in the top bar) open the same SearchOmnibox the /search page
 * renders (design §3.4). */
import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useI18n } from '../i18n';
import { healthLevel } from '../lib/derive';
import { useNowTick } from './RunBanner';
import { useModel } from '../model';
import { useTheme } from '../theme';
import { STATIC_MODE } from '../lib/api';
import { isDesktopApp, openInSystemBrowser } from '../lib/desktopExternalLinks';
import RunBanner from './RunBanner';
import SearchOmnibox from './SearchOmnibox';

// The published site is knowledge-only: Knowledge (home), Library, Search. The
// live portal adds Today/Ask/System (ops + LLM surfaces that need a server).
const NAV = STATIC_MODE
  ? ([
      { to: '/knowledge', key: 'nav.knowledge', end: false },
      { to: '/library', key: 'nav.library', end: false },
      { to: '/entities', key: 'nav.entities', end: false },
      { to: '/search', key: 'nav.search', end: false },
    ] as const)
  : ([
      { to: '/', key: 'nav.today', end: true },
      { to: '/library', key: 'nav.library', end: false },
      { to: '/tags', key: 'nav.tags', end: false },
      { to: '/entities', key: 'nav.entities', end: false },
      { to: '/search', key: 'nav.search', end: false },
      { to: '/knowledge', key: 'nav.knowledge', end: false },
      { to: '/ask', key: 'nav.ask', end: false },
      { to: '/system', key: 'nav.system', end: false },
    ] as const);

/** Desktop-only browser chrome: back / forward / open-in-browser. The Tauri
 * webview has no chrome (only WKWebView's ugly native context menu), so the
 * portal supplies its own. `navigate(-1)/navigate(1)` delegate to
 * `history.go()`, which steps across documents too (e.g. a legacy admin page
 * back into the SPA) and is a harmless no-op at the ends — so the buttons stay
 * always-enabled rather than mixing router-local `idx` with session-wide
 * `history.length` (which disagree after a cross-document navigation). Inert in
 * a real browser via `isDesktopApp()`. */
function DesktopNav() {
  const { t } = useI18n();
  const navigate = useNavigate();
  if (!isDesktopApp()) return null;
  return (
    <span className="nav-history">
      <button
        type="button"
        className="navbtn"
        onClick={() => navigate(-1)}
        aria-label={t('nav.back')}
        title={t('nav.back')}
      >
        ‹
      </button>
      <button
        type="button"
        className="navbtn"
        onClick={() => navigate(1)}
        aria-label={t('nav.forward')}
        title={t('nav.forward')}
      >
        ›
      </button>
      <button
        type="button"
        className="navbtn"
        onClick={() => openInSystemBrowser(window.location.href)}
        aria-label={t('nav.openInBrowser')}
        title={t('nav.openInBrowser')}
      >
        ↗
      </button>
    </span>
  );
}

function StatusLight() {
  const { t } = useI18n();
  const { model } = useModel();
  // Tick so a heartbeat that crosses the staleness threshold flips the dot
  // to red even on a portal left open with no other re-render (codex P2).
  const now = useNowTick();
  if (!model) return null;
  const level = healthLevel(model, now);
  const label = {
    ok: t('status.ok'),
    attention: t('status.attention'),
    failed: t('status.failed'),
  }[level];
  return (
    <span className={`status-light ${level}`} title={label}>
      <span className="dot" />
      {label}
    </span>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useTheme();
  return (
    <span className="seg-toggle">
      <button
        type="button"
        className={theme === 'light' ? 'active' : ''}
        onClick={() => setTheme('light')}
      >
        LIGHT
      </button>
      <button
        type="button"
        className={theme === 'dark' ? 'active' : ''}
        onClick={() => setTheme('dark')}
      >
        DARK
      </button>
    </span>
  );
}

function LangToggle() {
  const { lang, setLang } = useI18n();
  return (
    <span className="seg-toggle">
      <button
        type="button"
        className={lang === 'en' ? 'active' : ''}
        onClick={() => setLang('en')}
      >
        EN
      </button>
      <button
        type="button"
        className={lang === 'zh' ? 'active' : ''}
        onClick={() => setLang('zh')}
      >
        中
      </button>
    </span>
  );
}

export default function Shell() {
  const { t } = useI18n();
  const [searchOpen, setSearchOpen] = useState(false);
  const location = useLocation();
  // The legacy Flow/Monitor routes are System panels (B5): keep the System
  // nav item highlighted while the URL is one of theirs.
  const onLegacySystemRoute =
    location.pathname.startsWith('/flow') ||
    location.pathname.startsWith('/monitor');

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        // Never steal ⌘K from an active editing context (inputs, textareas,
        // selects, contenteditable) — the browser/user owns it there — and
        // composing surfaces (the Ask textarea) opt out explicitly via
        // [data-omnibox-suppress].
        const el = e.target instanceof HTMLElement ? e.target : null;
        if (
          el &&
          (el.isContentEditable ||
            ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) ||
            el.closest('[data-omnibox-suppress]'))
        ) {
          return;
        }
        e.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <div className="portal">
      {/* Run heartbeat + health dot are live-ops chrome — hidden on the
          published static site (no server, no run state). */}
      {!STATIC_MODE && <RunBanner />}
      <div className="page">
        <div className="shell">
          <div className="shell-head">
            <nav className="nav">
              <span className="brand">
                ovp2<span className="dot">.</span>
              </span>
              <DesktopNav />
              {NAV.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    isActive || (item.to === '/system' && onLegacySystemRoute)
                      ? 'active'
                      : ''
                  }
                >
                  {t(item.key)}
                </NavLink>
              ))}
              <span className="nav-right">
                <button
                  type="button"
                  className="omni-open"
                  onClick={() => setSearchOpen(true)}
                  aria-label={t('search.open')}
                  title={t('search.open')}
                >
                  ⌕ <span className="mono tiny">⌘K</span>
                </button>
                {!STATIC_MODE && <StatusLight />}
                <ThemeToggle />
                <LangToggle />
              </span>
            </nav>
          </div>
          <div className="shell-body">
            <Outlet />
          </div>
        </div>
      </div>
      {searchOpen && (
        <div
          className="omni-backdrop"
          onClick={() => setSearchOpen(false)}
          role="presentation"
        >
          <div
            className="omni-panel"
            role="dialog"
            aria-label={t('search.title')}
            onClick={(e) => e.stopPropagation()}
          >
            <SearchOmnibox
              variant="overlay"
              onClose={() => setSearchOpen(false)}
            />
          </div>
        </div>
      )}
    </div>
  );
}
