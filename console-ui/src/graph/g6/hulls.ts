import type { PluginOptions } from '@antv/g6';
import { clusterColor } from '../../lib/palette';
import type { Community, GraphNode } from '../../lib/types';

/** Hulls drawn on the overview — matches the reference product's cap. */
export const MAX_HULLS = 20;
export const MIN_HULL_MEMBERS = 3;

/**
 * G6 v5 `bubble-sets` plugin configs for the largest communities — the blob
 * shapes only. Community labels are a screen-space React overlay
 * (CommunityLabels.tsx): world-space canvas labels can't serve both the
 * zoomed-out overview and zoomed-in exploration at once.
 *
 * NOTE: the plan called for the `hull` plugin (cheaper), but @antv/g6
 * 5.1.1's hull plugin throws during viewport transforms and its destroy
 * path is broken — which silently killed zoom/drag for the whole graph.
 * bubble-sets works cleanly and is what the reference product uses.
 */
export function hullPlugins(
  communities: Community[],
  nodes: GraphNode[],
): PluginOptions {
  const membersByCluster = new Map<number, string[]>();
  for (const n of nodes) {
    if (n.cluster > 0) {
      const list = membersByCluster.get(n.cluster) ?? [];
      list.push(n.id);
      membersByCluster.set(n.cluster, list);
    }
  }

  return communities
    .filter((c) => (membersByCluster.get(c.id)?.length ?? 0) >= MIN_HULL_MEMBERS)
    .slice(0, MAX_HULLS)
    .map((c) => {
      const color = clusterColor(c.id, 60);
      return {
        type: 'bubble-sets',
        key: `hull-${c.id}`,
        members: membersByCluster.get(c.id) ?? [],
        fill: color,
        fillOpacity: 0.1,
        stroke: color,
        strokeOpacity: 0.45,
        lineWidth: 1,
      };
    });
}
