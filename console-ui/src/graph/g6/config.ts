import type { GraphOptions, NodeData, EdgeData } from '@antv/g6';
import { clusterColor, COLORS, EDGE_COLORS, nodeColor } from '../../lib/palette';
import type { GraphNode, GraphResponse } from '../../lib/types';
import { BASE_LABEL_COUNT } from './density';

export interface G6Data {
  nodes: NodeData[];
  edges: EdgeData[];
}

export type GraphViewKind = 'overview' | 'focus';

function nodeOf(datum: NodeData): GraphNode {
  return datum.data as unknown as GraphNode;
}

export function nodeSize(n: GraphNode): number {
  if (n.type === 'unit') return 9;
  if (n.type === 'source') return 12 + 10 * (n.importance ?? 0);
  return 10 + 22 * (n.importance ?? 0);
}

/** d3-force presets for the small focus/search subgraphs (parameters derived
 * from the Nowledge Mem reference). The overview never uses force — see
 * clusterSeeds. */
export const LAYOUT_PRESETS = {
  focus: {
    type: 'd3-force',
    link: { distance: 60, strength: 0.7 },
    collide: { radius: 22, strength: 1.1 },
    manyBody: { strength: -140 },
    velocityDecay: 0.68,
    alphaDecay: 0.04,
  },
  search: {
    type: 'd3-force',
    link: { distance: 34, strength: 0.82 },
    collide: { radius: 26, strength: 1.05 },
    manyBody: { strength: -60 },
    radial: { strength: 0.09, r: 140 },
    velocityDecay: 0.74,
    alphaDecay: 0.05,
  },
} as const;

export function toG6Data(
  data: GraphResponse,
  kind: GraphViewKind,
  focusId?: string | null,
): G6Data {
  let edges = data.edges;
  let position: (n: GraphNode, i: number) => { x: number; y: number } | undefined;

  if (kind === 'overview') {
    // Tier 0: thousands of cross-community `related` edges drawn as long
    // straight lines across the galaxy map are pure noise. Keep edges inside
    // a community; cross-community relations surface in focus mode.
    const clusterOf = new Map(data.nodes.map((n) => [n.id, n.cluster]));
    edges = data.edges.filter(
      (e) => clusterOf.get(e.source) === clusterOf.get(e.target),
    );
    const seeds = clusterSeeds(data.nodes);
    position = (n, i) => seeds.positionOf(n, i);
  } else {
    position = () => ({ x: 0, y: 0 }); // d3-force takes over
  }

  // Label LOD: top-N by importance start labeled; density.ts moves the
  // boundary with zoom. In focus mode units stay unlabeled — their quotes
  // read as noise; hover/detail carries them.
  const candidates =
    kind === 'focus' ? data.nodes.filter((n) => n.type !== 'unit') : data.nodes;
  const labelBudget = kind === 'focus' ? 60 : BASE_LABEL_COUNT;
  const ranked = new Set(
    [...candidates]
      .sort((a, b) => (b.importance ?? 0) - (a.importance ?? 0))
      .slice(0, labelBudget)
      .map((n) => n.id),
  );

  return {
    nodes: data.nodes.map((n, i) => ({
      id: n.id,
      style: position ? position(n, i) : undefined,
      data: {
        ...n,
        labeled: ranked.has(n.id) || n.id === focusId,
        labelSize: 12,
      } as unknown as Record<string, unknown>,
    })),
    edges: edges.map((e, i) => ({
      id: `e${i}`,
      source: e.source,
      target: e.target,
      data: { type: e.type, weight: e.weight ?? 1 },
    })),
  };
}

export interface BuildOptions {
  kind: GraphViewKind;
  focusId?: string | null;
}

export function buildGraphOptions(
  data: GraphResponse,
  opts: BuildOptions,
): GraphOptions {
  const focus = opts.kind === 'focus';

  return {
    data: toG6Data(data, opts.kind, opts.focusId),
    animation: focus,
    autoResize: true,
    padding: 24,
    autoFit: 'view',
    node: {
      style: {
        size: (d: NodeData) => nodeSize(nodeOf(d)),
        fill: (d: NodeData) => {
          const n = nodeOf(d);
          // Overview colors by community (structure); focus colors by type —
          // the claim/unit/source distinction is the story there.
          return !focus && n.type === 'claim'
            ? clusterColor(n.cluster)
            : nodeColor(n.type);
        },
        fillOpacity: 0.92,
        lineWidth: (d: NodeData) => {
          const n = nodeOf(d);
          return n.strength && n.strength !== 'supported' ? 1.5 : 0;
        },
        stroke: COLORS.textMuted,
        lineDash: (d: NodeData) => {
          const n = nodeOf(d);
          return n.strength && n.strength !== 'supported' ? [3, 2] : [];
        },
        labelText: (d: NodeData) =>
          (d.data as { labeled?: boolean }).labeled ? nodeOf(d).label : '',
        labelFill: COLORS.text,
        labelFontSize: (d: NodeData) =>
          (d.data as { labelSize?: number }).labelSize ?? 12,
        labelFontFamily: 'Inter, "Noto Sans SC", system-ui, sans-serif',
        labelBackground: true,
        labelBackgroundFill: 'rgba(15, 17, 23, 0.78)',
        labelBackgroundRadius: 4,
        labelPadding: [2, 5],
        labelPlacement: 'bottom',
        labelMaxWidth: 260,
        labelWordWrap: true,
        labelMaxLines: 2,
      },
      state: {
        selected: {
          stroke: COLORS.highlight,
          lineWidth: 3,
          lineDash: [],
          shadowColor: COLORS.highlight,
          shadowBlur: 12,
        },
        dimmed: { opacity: 0.15 },
        highlight: { lineWidth: 2, stroke: COLORS.highlight, lineDash: [] },
      },
    },
    edge: {
      style: {
        stroke: (d: EdgeData) =>
          EDGE_COLORS[(d.data?.type as string) ?? 'related'] ?? COLORS.edge,
        lineWidth: (d: EdgeData) =>
          Math.min(4, 1 + ((d.data?.weight as number) ?? 1) * 0.5),
        strokeOpacity: focus ? 0.55 : 0.28,
      },
      state: {
        highlight: { strokeOpacity: 0.9, stroke: COLORS.highlight },
        dimmed: { strokeOpacity: 0.06 },
      },
    },
    ...(focus ? { layout: { ...LAYOUT_PRESETS.focus } } : {}),
    behaviors: [
      'zoom-canvas',
      'drag-canvas',
      'drag-element',
      { type: 'brush-select', trigger: 'shift' },
    ],
  };
}

const GOLDEN_ANGLE = 2.399963;
/** Sunflower ring spacing inside a community disc. */
const SEED_SPACING = 20;

function discRadius(size: number): number {
  return SEED_SPACING * Math.sqrt(size) + 34;
}

/**
 * Deterministic "galaxy map" layout for the overview: communities are packed
 * as non-overlapping discs (greedy spiral circle-packing, largest first) and
 * members fill each disc in a sunflower pattern, importance-ranked from the
 * center out. d3-force is NOT used here — at 2000 cross-linked nodes it
 * collapses every community into one hairball and the hulls turn to mush;
 * force layout is reserved for the small focus/search subgraphs where an
 * organic shape actually helps.
 */
function clusterSeeds(nodes: GraphNode[]) {
  const sizes = new Map<number, number>();
  for (const n of nodes) {
    sizes.set(n.cluster, (sizes.get(n.cluster) ?? 0) + 1);
  }
  const ordered = [...sizes.entries()].sort(
    (a, b) => b[1] - a[1] || a[0] - b[0],
  );

  // Greedy spiral packing: walk an Archimedean spiral out from the origin
  // and drop each disc at the first collision-free point.
  const placed: { x: number; y: number; r: number }[] = [];
  const anchors = new Map<number, { x: number; y: number }>();
  const MARGIN = 46;
  for (const [cluster, size] of ordered) {
    const r = discRadius(size);
    let angle = 0;
    for (;;) {
      const d = 6 * angle;
      const x = Math.cos(angle) * d;
      const y = Math.sin(angle) * d;
      const free = placed.every(
        (p) => Math.hypot(p.x - x, p.y - y) >= p.r + r + MARGIN,
      );
      if (free) {
        placed.push({ x, y, r });
        anchors.set(cluster, { x, y });
        break;
      }
      angle += 0.25;
    }
  }

  // Members are laid center-out by importance: nodes arrive importance-desc
  // from the server, so the nth member of a cluster sits on ring sqrt(nth).
  const seen = new Map<number, number>();
  return {
    positionOf(n: GraphNode, _index: number): { x: number; y: number } {
      const anchor = anchors.get(n.cluster) ?? { x: 0, y: 0 };
      const nth = seen.get(n.cluster) ?? 0;
      seen.set(n.cluster, nth + 1);
      const r = SEED_SPACING * Math.sqrt(nth + 0.35);
      const a = nth * GOLDEN_ANGLE;
      return { x: anchor.x + Math.cos(a) * r, y: anchor.y + Math.sin(a) * r };
    },
  };
}
