import { useEffect, useRef } from 'react';
import { CanvasEvent, Graph, NodeEvent } from '@antv/g6';
import { useGraphStore } from '../store/graphStore';
import type { GraphResponse } from '../lib/types';
import { buildGraphOptions, topLabelIds, OVERVIEW_LABEL_COUNT } from './g6/config';

/**
 * The only file that touches @antv/g6. Owns the Graph instance lifecycle;
 * writes interactions into the zustand store, which React panels subscribe
 * to. The Graph instance itself never enters the store (non-serializable,
 * re-render storms).
 */
export default function GraphCanvas({ data }: { data: GraphResponse }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const graph = new Graph({
      container,
      ...buildGraphOptions(data, {
        labeled: topLabelIds(data, OVERVIEW_LABEL_COUNT),
        colorBy: 'cluster',
        // Overview positions come from the deterministic galaxy-map seeding;
        // force layout is for focus/search subgraphs (Stage 2/3).
        layout: 'none',
      }),
    });
    graphRef.current = graph;

    graph.on(NodeEvent.CLICK, (evt) => {
      const { select, selection } = useGraphStore.getState();
      const id = (evt as unknown as { target: { id: string } }).target.id;
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

    graph.render().catch((err: unknown) => {
      // Destroyed mid-render (StrictMode double-mount) is expected noise.
      if (!graph.destroyed) console.error('graph render failed', err);
    });

    // Debug handle (mirrors the reference product's window.__knowledgeGraphRef).
    (window as unknown as { __ovpGraph?: Graph }).__ovpGraph = graph;

    return () => {
      graphRef.current = null;
      graph.destroy();
    };
  }, [data]);

  return <div ref={containerRef} className="absolute inset-0" />;
}
