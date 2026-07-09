/** OVP2 portal shell — DS top-nav shell (design §6 ext.1): centered column
 * max-width 1180, rounded outer shell on --bg, brand "ovp2." with accent
 * period, text-link nav, status dot + theme + language toggles on the right.
 * Single-locale UI — no inline bilingual pairs (design §0.6). */
import { NavLink, Outlet } from 'react-router-dom';
import { useI18n } from '../i18n';
import { healthLevel } from '../lib/derive';
import { useModel } from '../model';
import { useTheme } from '../theme';

const NAV = [
  { to: '/', key: 'nav.today', end: true },
  { to: '/library', key: 'nav.library', end: false },
  { to: '/search', key: 'nav.search', end: false },
  { to: '/knowledge', key: 'nav.knowledge', end: false },
  { to: '/ask', key: 'nav.ask', end: false },
  { to: '/system', key: 'nav.system', end: false },
] as const;

function StatusLight() {
  const { t } = useI18n();
  const { model } = useModel();
  if (!model) return null;
  const level = healthLevel(model);
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
  return (
    <div className="portal">
      <div className="page">
        <div className="shell">
          <div className="shell-head">
            <nav className="nav">
              <span className="brand">
                ovp2<span className="dot">.</span>
              </span>
              {NAV.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) => (isActive ? 'active' : '')}
                >
                  {t(item.key)}
                </NavLink>
              ))}
              <span className="nav-right">
                <StatusLight />
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
    </div>
  );
}
