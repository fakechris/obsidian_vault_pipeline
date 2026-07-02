import { useMemo } from 'react';
import { clusterColor } from '../lib/palette';
import { flyToNodes } from './controller';
import type { Community, GraphNode } from '../lib/types';

interface Props {
  communities: Community[];
  nodes: GraphNode[];
}

/** Community index (bottom-left) — doubles as tier-0 navigation: clicking a
 * row flies the camera to that community. */
export default function Legend({ communities, nodes }: Props) {
  const memberIds = useMemo(() => {
    const byCluster = new Map<number, string[]>();
    for (const n of nodes) {
      if (n.cluster > 0) {
        const list = byCluster.get(n.cluster) ?? [];
        list.push(n.id);
        byCluster.set(n.cluster, list);
      }
    }
    return byCluster;
  }, [nodes]);

  if (communities.length === 0) return null;
  return (
    <div className="pointer-events-auto absolute bottom-4 left-4 z-30 max-h-64 w-64 overflow-y-auto rounded-lg border border-border-soft bg-panel p-3 shadow-2xl backdrop-blur-xl">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
        Communities 社区
      </div>
      <ul className="space-y-0.5">
        {communities.slice(0, 12).map((c) => (
          <li key={c.id}>
            <button
              onClick={() => flyToNodes(memberIds.get(c.id) ?? [])}
              className="flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-xs transition-colors hover:bg-white/5"
            >
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ backgroundColor: clusterColor(c.id) }}
              />
              <span className="truncate text-slate-300">{c.label}</span>
              <span className="ml-auto shrink-0 text-slate-500">{c.size}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
