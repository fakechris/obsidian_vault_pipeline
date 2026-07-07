import { useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useGraphStore } from '../store/graphStore';
import GraphCanvas from './GraphCanvas';
import CommunityLabels from './CommunityLabels';
import NodeTooltip from './NodeTooltip';
import DetailPanel from './DetailPanel';
import Legend from './Legend';
import SearchBar from './SearchBar';

export default function GraphView() {
  const data = useGraphStore((s) => s.data);
  const loading = useGraphStore((s) => s.loading);
  const error = useGraphStore((s) => s.error);
  const viewMode = useGraphStore((s) => s.viewMode);
  const focusId = useGraphStore((s) => s.focusId);
  const transformTick = useGraphStore((s) => s.transformTick);
  const loadOverview = useGraphStore((s) => s.loadOverview);
  const loadFocus = useGraphStore((s) => s.loadFocus);
  const backToOverview = useGraphStore((s) => s.backToOverview);
  const [searchParams, setSearchParams] = useSearchParams();

  // Deep link: /graph?focus=claim:<key> lands directly in focus mode
  // (explore page and, later, the static console link here).
  useEffect(() => {
    const wanted = searchParams.get('focus');
    const state = useGraphStore.getState();
    if (wanted && state.focusId !== wanted) {
      void loadFocus(wanted);
    } else if (!wanted && !state.data) {
      void loadOverview();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  // Keep the URL shareable as focus changes in-app. Never touch the URL
  // while a load is in flight — on first mount the deep-link fetch is still
  // pending and this would strip ?focus before it resolves.
  useEffect(() => {
    const state = useGraphStore.getState();
    if (state.loading || !state.data) return;
    const inUrl = searchParams.get('focus');
    if (viewMode === 'focus' && focusId && inUrl !== focusId) {
      setSearchParams({ focus: focusId }, { replace: true });
    } else if (viewMode === 'overview' && inUrl) {
      setSearchParams({}, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewMode, focusId, loading]);

  return (
    <div className="absolute inset-0 overflow-hidden bg-bg">
      {data && (
        <GraphCanvas
          data={data}
          kind={viewMode}
          focusId={focusId}
        />
      )}
      {data && viewMode === 'overview' && (
        <CommunityLabels
          communities={data.communities}
          nodes={data.nodes}
          transformTick={transformTick}
        />
      )}
      {data && viewMode === 'overview' && (
        <Legend communities={data.communities} nodes={data.nodes} />
      )}
      {data && <NodeTooltip communities={data.communities} />}
      <SearchBar />
      <DetailPanel />

      <div className="pointer-events-none absolute left-4 top-3 z-30 text-xs text-slate-500">
        {data && viewMode === 'overview' && (
          <span>
            {data.nodes.length.toLocaleString()} of{' '}
            {data.total_nodes.toLocaleString()} nodes 节点 ·{' '}
            {data.communities.length} communities 社区
            {data.truncated && ' · top by importance 按重要度截取'}
          </span>
        )}
        {data && viewMode !== 'overview' && (
          <span className="pointer-events-auto flex items-center gap-3">
            <button
              onClick={() => void backToOverview()}
              className="rounded-md border border-border-soft bg-panel px-2.5 py-1 text-slate-300 shadow backdrop-blur-md transition-colors hover:bg-white/10"
            >
              ← Overview 返回全景
            </button>
            {viewMode === 'focus' ? (
              <span>
                Neighborhood 邻域 · {data.nodes.length} nodes
                {data.truncated && ' (capped 截断)'}
              </span>
            ) : (
              <span>
                {data.nodes.filter((n) => n.hit).length} hits 命中 ·{' '}
                {data.nodes.filter((n) => !n.hit).length} context 关联
                {data.truncated && ' · more matches truncated 更多命中被截断'}
              </span>
            )}
          </span>
        )}
      </div>

      {loading && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-bg/60 text-sm text-slate-400 backdrop-blur-sm">
          Loading graph 图谱加载中…
        </div>
      )}
      {error && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="rounded-lg border border-red-500/30 bg-red-950/40 px-4 py-3 text-sm text-red-300">
            Failed to load graph 图谱加载失败: {error}
            <div className="mt-1 text-xs text-red-400/80">
              Is `ovp2 serve` running? 请确认服务已启动
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
