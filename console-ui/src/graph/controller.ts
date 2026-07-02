import type { Graph } from '@antv/g6';

/**
 * Module-level handle to the live G6 Graph. Kept OUT of the zustand store
 * (non-serializable; putting it there causes re-render storms). GraphCanvas
 * registers on mount; overlays (community labels, tooltip anchoring) read it.
 */
let current: Graph | null = null;

export function registerGraph(graph: Graph | null): void {
  current = graph;
}

export function getGraph(): Graph | null {
  return current && !current.destroyed ? current : null;
}

/** Fly the camera to fit the given nodes (community navigation). */
export function flyToNodes(ids: string[]): void {
  const graph = getGraph();
  if (!graph || ids.length === 0) return;
  void graph.focusElement(ids, true);
}

/** World (canvas) → viewport (screen, canvas-relative) coordinates. */
export function worldToScreen(pos: [number, number]): [number, number] | null {
  const graph = getGraph();
  if (!graph) return null;
  try {
    const p = graph.getViewportByCanvas([pos[0], pos[1], 0]);
    return [p[0], p[1]];
  } catch {
    return null;
  }
}
