import { useEffect, useRef } from 'react';
import { CanvasEvent, Graph, GraphEvent, NodeEvent } from '@antv/g6';
import { useGraphStore } from '../store/graphStore';
import type { GraphNode, GraphResponse } from '../lib/types';
import { buildGraphOptions, type GraphViewKind } from './g6/config';
import { hullPlugins } from './g6/hulls';
import { initDensity, updateDensity, type DensityState } from './g6/density';
import { registerGraph } from './controller';

const HOVER_DELAY_MS = 600;

interface Props {
  data: GraphResponse;
  kind: GraphViewKind;
  focusId: string | null;
}

/**
 * The only file that touches @antv/g6. Owns the Graph instance lifecycle;
 * writes interactions into the zustand store, which React panels subscribe
 * to. The Graph instance itself never enters the store (non-serializable,
 * re-render storms) — overlays reach it through controller.ts.
 */
export default function GraphCanvas({ data, kind, focusId }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const graph = new Graph({
      container,
      ...buildGraphOptions(data, { kind, focusId }),
      ...(kind === 'overview'
        ? { plugins: hullPlugins(data.communities, data.nodes) }
        : {}),
    });
    registerGraph(graph);

    const nodeById = new Map(data.nodes.map((n) => [n.id, n]));
    let density: DensityState | null = null;
    let hoverTimer: ReturnType<typeof setTimeout> | undefined;
    let transformTimer: ReturnType<typeof setTimeout> | undefined;

    const targetId = (evt: unknown): string =>
      (evt as { target: { id: string } }).target.id;

    graph.on(NodeEvent.CLICK, (evt) => {
      const { select, selection } = useGraphStore.getState();
      const id = targetId(evt);
      if (selection && selection !== id) {
        graph.setElementState(selection, []).catch(() => {});
      }
      graph.setElementState(id, ['selected']).catch(() => {});
      select(id);
    });

    graph.on(CanvasEvent.CLICK, () => {
      const { select, selection } = useGraphStore.getState();
      if (selection) {
        graph.setElementState(selection, []).catch(() => {});
      }
      select(null);
    });

    graph.on(NodeEvent.POINTER_OVER, (evt) => {
      const id = targetId(evt);
      const node = nodeById.get(id) as GraphNode | undefined;
      if (!node) return;
      const client = (evt as { client?: { x: number; y: number } }).client;
      clearTimeout(hoverTimer);
      hoverTimer = setTimeout(() => {
        useGraphStore
          .getState()
          .setHover({ node, x: client?.x ?? 0, y: client?.y ?? 0 });
      }, HOVER_DELAY_MS);
    });

    graph.on(NodeEvent.POINTER_OUT, () => {
      clearTimeout(hoverTimer);
      if (useGraphStore.getState().hover) {
        useGraphStore.getState().setHover(null);
      }
    });

    graph.on(NodeEvent.DBLCLICK, (evt) => {
      const id = targetId(evt);
      if (id.startsWith('claim:')) {
        void useGraphStore.getState().loadFocus(id);
      }
    });

    // Viewport transforms drive the label LOD (density tiers) and the
    // screen-space community-label overlay; a moving viewport also
    // invalidates any hover tooltip.
    graph.on(GraphEvent.AFTER_TRANSFORM, () => {
      clearTimeout(hoverTimer);
      if (useGraphStore.getState().hover) {
        useGraphStore.getState().setHover(null);
      }
      clearTimeout(transformTimer);
      transformTimer = setTimeout(() => {
        if (graph.destroyed) return;
        // Zoom-driven label LOD is an overview concern; focus subgraphs keep
        // their fixed claim/source labels.
        if (kind === 'overview' && density) updateDensity(graph, density);
        useGraphStore.getState().bumpTransform();
      }, 90);
    });

    graph
      .render()
      .then(() => {
        if (graph.destroyed) return;
        density = initDensity(graph, data);
        if (focusId && kind === 'focus') {
          graph.setElementState(focusId, ['selected']).catch(() => {});
        }
        useGraphStore.getState().bumpTransform();
      })
      .catch((err: unknown) => {
        // Destroyed mid-render (StrictMode double-mount) is expected noise.
        if (!graph.destroyed) console.error('graph render failed', err);
      });

    // Debug handle (mirrors the reference product's window.__knowledgeGraphRef).
    (window as unknown as { __ovpGraph?: Graph }).__ovpGraph = graph;

    return () => {
      clearTimeout(hoverTimer);
      clearTimeout(transformTimer);
      registerGraph(null);
      graph.destroy();
    };
  }, [data, kind, focusId]);

  return <div ref={containerRef} className="absolute inset-0" />;
}
