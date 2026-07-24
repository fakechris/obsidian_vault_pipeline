import { lazy, Suspense, type ReactNode } from 'react';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import Shell from './components/Shell';
import { ModelProvider } from './model';
import { STATIC_MODE } from './lib/api';
import { useI18n, type MsgKey } from './i18n';
import AskPage from './pages/AskPage';
import KnowledgePage from './pages/KnowledgePage';
import LibraryPage from './pages/LibraryPage';
import SearchPage from './pages/SearchPage';
import SourceDetailPage from './pages/SourceDetailPage';
import TagsPage from './pages/TagsPage';
import EntitiesPage from './pages/EntitiesPage';
import EntityDetailPage from './pages/EntityDetailPage';
import SystemPage from './pages/SystemPage';
import ThemeDetailPage from './pages/ThemeDetailPage';
import TodayPage from './pages/TodayPage';

// Flow/Monitor carry d3 — lazy so the portal pages stay light.
const FlowPage = lazy(() => import('./pages/FlowPage'));
const MonitorPage = lazy(() => import('./pages/MonitorPage'));

/** The two remaining legacy views (Flow / Monitor), rethemed minimally in
 * B5: they now live INSIDE the portal Shell (top nav visible; the System
 * item stays highlighted — see Shell) with a DS-styled breadcrumb back to
 * System. Their internal dark canvas is deliberately kept as-is — they are
 * admin depth slated for componentization, and a full DS retheme is not
 * worth it before that; what was jarring was the missing navigation. */
function LegacyPanel({
  titleKey,
  children,
}: {
  titleKey: MsgKey;
  children: ReactNode;
}) {
  const { t } = useI18n();
  return (
    <>
      <div className="crumbs">
        <Link to="/system">{t('nav.system')}</Link> / {t(titleKey)}
      </div>
      <div className="legacy-canvas">
        <Suspense
          fallback={
            <div className="legacy-loading">{t('common.loading')}</div>
          }
        >
          {children}
        </Suspense>
      </div>
    </>
  );
}

/** Old /viz/* deep links (pre-B1 the SPA was mounted under /viz) redirect
 * to the pages that absorbed them: graph → the Knowledge graph view,
 * explore → search; flow/monitor keep their routes (now System panels). */
function VizRedirect() {
  const location = useLocation();
  const rest = location.pathname.replace(/^\/viz\/?/, '/');
  if (rest === '/' || rest === '/graph') {
    return <Navigate to="/knowledge?view=graph" replace />;
  }
  if (rest === '/explore') {
    return <Navigate to="/search" replace />;
  }
  return (
    <Navigate
      to={{ pathname: rest, search: location.search, hash: location.hash }}
      replace
    />
  );
}

export default function App() {
  return (
    <ModelProvider>
      <Routes>
        <Route element={<Shell />}>
          {/* Published site is knowledge-only: home = Knowledge, no ops/ask
              surfaces (they need a live server + are pipeline-internal). */}
          <Route
            path="/"
            element={STATIC_MODE ? <Navigate to="/knowledge" replace /> : <TodayPage />}
          />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/library/:sha" element={<SourceDetailPage />} />
          {!STATIC_MODE && <Route path="/tags" element={<TagsPage />} />}
          <Route path="/entities" element={<EntitiesPage />} />
          <Route path="/entity/:id" element={<EntityDetailPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/knowledge/theme/:theme" element={<ThemeDetailPage />} />
          {!STATIC_MODE && <Route path="/ask" element={<AskPage />} />}
          {!STATIC_MODE && (
            <Route path="/ask/chat/:chatId" element={<AskPage />} />
          )}
          {!STATIC_MODE && <Route path="/system" element={<SystemPage />} />}
          {!STATIC_MODE && (
            <Route
              path="/flow"
              element={
                <LegacyPanel titleKey="system.flowLink">
                  <FlowPage />
                </LegacyPanel>
              }
            />
          )}
          {!STATIC_MODE && (
            <Route
              path="/monitor"
              element={
                <LegacyPanel titleKey="system.monitorLink">
                  <MonitorPage />
                </LegacyPanel>
              }
            />
          )}
        </Route>
        {/* Retired standalone viz routes → their portal homes (design §2). */}
        <Route
          path="/graph"
          element={<Navigate to="/knowledge?view=graph" replace />}
        />
        <Route path="/explore" element={<Navigate to="/search" replace />} />
        <Route path="/viz/*" element={<VizRedirect />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </ModelProvider>
  );
}
