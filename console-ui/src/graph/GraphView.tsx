import { useEffect } from 'react';
import { useGraphStore } from '../store/graphStore';
import GraphCanvas from './GraphCanvas';
import Legend from './Legend';

export default function GraphView() {
  const { data, loading, error, selection, loadOverview } = useGraphStore();

  useEffect(() => {
    if (!useGraphStore.getState().data) {
      void loadOverview();
    }
  }, [loadOverview]);

  return (
    <div className="absolute inset-0 overflow-hidden bg-bg">
      {data && <GraphCanvas data={data} />}
      {data && <Legend communities={data.communities} />}

      <div className="pointer-events-none absolute left-4 top-3 text-xs text-slate-500">
        {data && (
          <span>
            {data.nodes.length.toLocaleString()} of{' '}
            {data.total_nodes.toLocaleString()} nodes 节点 ·{' '}
            {data.communities.length} communities 社区
            {data.truncated && ' · top by importance 按重要度截取'}
          </span>
        )}
      </div>

      {selection && (
        <div className="absolute right-4 top-3 max-w-sm truncate rounded-md border border-border-soft bg-panel px-3 py-1.5 text-xs text-slate-300 shadow-2xl backdrop-blur-xl">
          {selection}
        </div>
      )}

      {loading && (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-slate-500">
          Loading graph 图谱加载中…
        </div>
      )}
      {error && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="rounded-lg border border-red-500/30 bg-red-950/40 px-4 py-3 text-sm text-red-300">
            Failed to load graph 图谱加载失败: {error}
            <div className="mt-1 text-xs text-red-400/80">
              Is `ovp-next serve` running? 请确认服务已启动
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
