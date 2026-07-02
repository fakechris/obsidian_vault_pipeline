import type { Graph, NodeData } from '@antv/g6';
import type { GraphResponse } from '../../lib/types';

/** Labels shown at the initial (fit) zoom. */
export const BASE_LABEL_COUNT = 30;
const MAX_LABEL_COUNT = 500;
/** Below this absolute zoom node labels are unreadable — community labels
 * (screen-space overlay) carry the navigation instead. */
export const NODE_LABEL_MIN_ZOOM = 0.45;

/**
 * Zoom-driven label LOD ("不同信息密度展示不同内容"). Keeps on-screen label
 * density roughly constant: the label budget grows with the square of the
 * zoom ratio (viewport shows 1/ratio² of the world), and label font size is
 * computed in world units to stay ~12px on screen.
 */
export interface DensityState {
  fitZoom: number;
  currentCount: number;
  currentSize: number;
  /** Node ids sorted by importance desc — bucket boundaries move over this. */
  ranked: string[];
}

export function initDensity(graph: Graph, data: GraphResponse): DensityState {
  return {
    fitZoom: Math.max(0.01, graph.getZoom()),
    currentCount: BASE_LABEL_COUNT,
    currentSize: 12,
    ranked: [...data.nodes]
      .sort((a, b) => (b.importance ?? 0) - (a.importance ?? 0))
      .map((n) => n.id),
  };
}

/** World-unit font size that reads ~12px on screen at the given zoom. */
export function labelWorldSize(zoom: number): number {
  return Math.max(3, Math.min(26, 12 / Math.max(0.01, zoom)));
}

export function updateDensity(graph: Graph, state: DensityState): void {
  const zoom = graph.getZoom();
  let count: number;
  if (zoom < NODE_LABEL_MIN_ZOOM && state.fitZoom < NODE_LABEL_MIN_ZOOM) {
    // Zoomed-out tier of a large graph: landmarks only.
    count = BASE_LABEL_COUNT;
  } else {
    const ratio = zoom / state.fitZoom;
    count = Math.round(BASE_LABEL_COUNT * ratio * ratio);
  }
  count = Math.max(BASE_LABEL_COUNT, Math.min(MAX_LABEL_COUNT, count));
  count = Math.min(count, state.ranked.length);

  const size = Math.round(labelWorldSize(zoom) * 10) / 10;
  if (count === state.currentCount && size === state.currentSize) return;

  const updates: NodeData[] = [];
  const hi = Math.max(count, state.currentCount);
  for (let i = 0; i < hi; i++) {
    const id = state.ranked[i];
    if (!id) break;
    updates.push({ id, data: { labeled: i < count, labelSize: size } });
  }
  state.currentCount = count;
  state.currentSize = size;
  if (updates.length > 0) {
    graph.updateNodeData(updates);
    void graph.draw();
  }
}
