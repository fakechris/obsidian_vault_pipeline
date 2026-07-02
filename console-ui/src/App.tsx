import { lazy, Suspense } from 'react';
import { Navigate, NavLink, Route, Routes } from 'react-router-dom';
import { cn } from './lib/cn';

// The graph route carries @antv/g6 (~1MB min) — lazy so explore/flow/monitor
// stay light.
const GraphView = lazy(() => import('./graph/GraphView'));
const ExplorePage = lazy(() => import('./pages/ExplorePage'));
const FlowPage = lazy(() => import('./pages/FlowPage'));
const MonitorPage = lazy(() => import('./pages/MonitorPage'));

const NAV = [
  { to: '/graph', label: 'Graph 图谱' },
  { to: '/explore', label: 'Explore 检索' },
  { to: '/flow', label: 'Flow 流程' },
  { to: '/monitor', label: 'Monitor 监控' },
];

export default function App() {
  return (
    <div className="flex h-screen flex-col">
      <nav className="flex h-12 shrink-0 items-center gap-1 border-b border-border-soft bg-panel px-4 backdrop-blur-xl">
        <span className="mr-4 text-sm font-semibold tracking-wide text-slate-100">
          OVP <span className="text-claim">Crystal</span>
        </span>
        {NAV.map((item) => (
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
        <a
          href="/"
          className="ml-auto rounded-md px-3 py-1.5 text-sm text-slate-400 transition-colors hover:bg-white/5 hover:text-slate-200"
        >
          ← Console 主控台
        </a>
      </nav>
      <main className="relative min-h-0 flex-1">
        <Suspense
          fallback={
            <div className="flex h-full items-center justify-center text-sm text-slate-500">
              Loading 加载中…
            </div>
          }
        >
          <Routes>
            <Route path="/" element={<Navigate to="/graph" replace />} />
            <Route path="/graph" element={<GraphView />} />
            <Route path="/explore" element={<ExplorePage />} />
            <Route path="/flow" element={<FlowPage />} />
            <Route path="/monitor" element={<MonitorPage />} />
            <Route path="*" element={<Navigate to="/graph" replace />} />
          </Routes>
        </Suspense>
      </main>
    </div>
  );
}
