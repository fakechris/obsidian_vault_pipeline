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
import LibraryPage from './pages/LibraryPage';
import {
  AskPage,
  KnowledgePage,
  SearchPage,
  SystemPage,
} from './pages/PlaceholderPages';
import SourceDetailPage from './pages/SourceDetailPage';
import TodayPage from './pages/TodayPage';
import { cn } from './lib/cn';

// The graph route carries @antv/g6 (~1MB min) — lazy so the portal pages
// stay light.
const GraphView = lazy(() => import('./graph/GraphView'));
const ExplorePage = lazy(() => import('./pages/ExplorePage'));
const FlowPage = lazy(() => import('./pages/FlowPage'));
const MonitorPage = lazy(() => import('./pages/MonitorPage'));

// Pre-B1 console routes — functional but OUT of the portal top nav
// (design §2: graph becomes a component in B2/B3, flow/monitor fold into
// System in B5). They keep their own dark layout until rethemed.
const LEGACY_NAV = [
  { to: '/graph', label: 'Graph 图谱' },
  { to: '/explore', label: 'Explore 检索' },
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
          to="/"
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
 * to the same route at the root: /viz/graph → /graph. */
function VizRedirect() {
  const location = useLocation();
  const rest = location.pathname.replace(/^\/viz\/?/, '/');
  return <Navigate to={rest === '/' ? '/graph' : rest} replace />;
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
          <Route path="/ask" element={<AskPage />} />
          <Route path="/system" element={<SystemPage />} />
        </Route>
        <Route element={<LegacyLayout />}>
          <Route path="/graph" element={<GraphView />} />
          <Route path="/explore" element={<ExplorePage />} />
          <Route path="/flow" element={<FlowPage />} />
          <Route path="/monitor" element={<MonitorPage />} />
        </Route>
        <Route path="/viz/*" element={<VizRedirect />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </ModelProvider>
  );
}
