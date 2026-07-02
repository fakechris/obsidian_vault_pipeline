import type { PluginOptions } from '@antv/g6';
import { clusterColor } from '../../lib/palette';
import type { Community, GraphNode } from '../../lib/types';

/** Hulls drawn on the overview — matches the reference product's cap. */
export const MAX_HULLS = 20;
export const MIN_HULL_MEMBERS = 3;

/**
 * G6 v5 `bubble-sets` plugin configs for the largest communities. The plan
 * called for `hull` (cheaper), but @antv/g6 5.1.1's hull plugin throws
 * during viewport transforms ("Cannot read properties of null") and its
 * destroy path is broken — which silently killed zoom/drag for the whole
 * graph. bubble-sets is what the reference product uses and works cleanly.
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
        label: true,
        labelText: `${c.label} · ${c.size}`,
        labelPlacement: 'top',
        labelCloseToPath: false,
        labelAutoRotate: false,
        labelFill: '#e2e8f0',
        // World-coordinate font: the overview sits at ~0.1-0.2 zoom, so hull
        // labels (the tier-0 navigation layer) must be big in world units
        // to be readable on screen. Scales with community weight.
        labelFontSize: 26 + Math.round(Math.sqrt(c.size) * 3),
        labelFontWeight: 600,
        labelBackground: true,
        labelBackgroundFill: 'rgba(15, 17, 23, 0.7)',
        labelBackgroundRadius: 5,
        labelPadding: [3, 8],
      };
    });
}
