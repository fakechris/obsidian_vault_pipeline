import { lazy, Suspense } from 'react';
import {
  Navigate,
  NavLink,
  Outlet,
  Route,
  Routes,
  useLocation,
} from 'react-router-dom';
import Shell from './components/Shell';
import { ModelProvider } from './model';
import { SystemPage } from './pages/PlaceholderPages';
import AskPage from './pages/AskPage';
import KnowledgePage from './pages/KnowledgePage';
import LibraryPage from './pages/LibraryPage';
import SearchPage from './pages/SearchPage';
import SourceDetailPage from './pages/SourceDetailPage';
import ThemeDetailPage from './pages/ThemeDetailPage';
import TodayPage from './pages/TodayPage';
import { cn } from './lib/cn';

// Flow/Monitor carry d3 — lazy so the portal pages stay light.
const FlowPage = lazy(() => import('./pages/FlowPage'));
const MonitorPage = lazy(() => import('./pages/MonitorPage'));

// The last two pre-B1 console routes. The standalone viz navigation is
// retired in B3 (design §2/§9): the old Graph page folded into
// /knowledge?view=graph, Explore into /search. Flow and Monitor remain
// reachable ONLY from the System placeholder link list until B5 rethemes
// them into System panels — so this bar links back to the portal only.
const LEGACY_NAV = [
  { to: '/flow', label: 'Flow 流程' },
  { to: '/monitor', label: 'Monitor 监控' },
];

function LegacyLayout() {
  return (
    <div className="flex h-screen flex-col bg-bg font-sans text-slate-200 antialiased">
      <nav className="flex h-12 shrink-0 items-center gap-1 border-b border-border-soft bg-panel px-4 backdrop-blur-xl">
        <span className="mr-4 text-sm font-semibold tracking-wide text-slate-100">
          OVP <span className="text-claim">Crystal</span>
        </span>
        {LEGACY_NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              cn(
                'rounded-md px-3 py-1.5 text-sm text-slate-400 transition-colors hover:bg-white/5 hover:text-slate-200',
                isActive && 'bg-white/10 text-slate-100',
              )
            }
          >
            {item.label}
          </NavLink>
        ))}
        <NavLink
          to="/system"
          className="ml-auto rounded-md px-3 py-1.5 text-sm text-slate-400 transition-colors hover:bg-white/5 hover:text-slate-200"
        >
          ← ovp2 portal
        </NavLink>
      </nav>
      <main className="relative min-h-0 flex-1">
        <Suspense
          fallback={
            <div className="flex h-full items-center justify-center text-sm text-slate-500">
              Loading…
            </div>
          }
        >
          <Outlet />
        </Suspense>
      </main>
    </div>
  );
}

/** Old /viz/* deep links (pre-B1 the SPA was mounted under /viz) redirect
 * to the pages that absorbed them: graph → the Knowledge graph view,
 * explore → search; flow/monitor keep their (legacy-themed) routes. */
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
          <Route path="/" element={<TodayPage />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/library/:sha" element={<SourceDetailPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/knowledge/theme/:theme" element={<ThemeDetailPage />} />
          <Route path="/ask" element={<AskPage />} />
          <Route path="/system" element={<SystemPage />} />
        </Route>
        {/* Retired standalone viz routes → their portal homes (design §2). */}
        <Route
          path="/graph"
          element={<Navigate to="/knowledge?view=graph" replace />}
        />
        <Route path="/explore" element={<Navigate to="/search" replace />} />
        <Route element={<LegacyLayout />}>
          <Route path="/flow" element={<FlowPage />} />
          <Route path="/monitor" element={<MonitorPage />} />
        </Route>
        <Route path="/viz/*" element={<VizRedirect />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </ModelProvider>
  );
}
