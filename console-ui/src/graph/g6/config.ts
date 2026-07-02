import type { GraphOptions, NodeData, EdgeData } from '@antv/g6';
import { clusterColor, COLORS, EDGE_COLORS, nodeColor } from '../../lib/palette';
import type { GraphNode, GraphResponse } from '../../lib/types';
import { hullPlugins } from './hulls';

/** How many labels the overview shows before zooming in (tier 0). */
export const OVERVIEW_LABEL_COUNT = 30;

export interface G6Data {
  nodes: NodeData[];
  edges: EdgeData[];
}

export function toG6Data(
  data: GraphResponse,
  opts?: { intraClusterEdgesOnly?: boolean },
): G6Data {
  const seeds = clusterSeeds(data.nodes);
  let edges = data.edges;
  if (opts?.intraClusterEdgesOnly) {
    // Tier 0: thousands of cross-community `related` edges drawn as long
    // straight lines across the galaxy map are pure noise. Keep edges inside
    // a community; cross-community relations surface on focus (Stage 2).
    const clusterOf = new Map(data.nodes.map((n) => [n.id, n.cluster]));
    edges = data.edges.filter(
      (e) => clusterOf.get(e.source) === clusterOf.get(e.target),
    );
  }
  return {
    nodes: data.nodes.map((n, i) => ({
      id: n.id,
      style: seeds.positionOf(n, i),
      data: { ...n } as unknown as Record<string, unknown>,
    })),
    edges: edges.map((e, i) => ({
      id: `e${i}`,
      source: e.source,
      target: e.target,
      data: { type: e.type, weight: e.weight ?? 1 },
    })),
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

function nodeOf(datum: NodeData): GraphNode {
  return datum.data as unknown as GraphNode;
}

export function nodeSize(n: GraphNode): number {
  if (n.type === 'unit') return 8;
  if (n.type === 'source') return 10 + 10 * (n.importance ?? 0);
  return 10 + 22 * (n.importance ?? 0);
}

/** d3-force presets. `browse` for the overview; `search`/focus subgraphs use
 * the tighter parameters (derived from the Nowledge Mem reference). */
export const LAYOUT_PRESETS = {
  browse: {
    type: 'd3-force',
    // Positions are cluster-seeded (see clusterSeeds); alpha 0.3 keeps the
    // force pass a local refinement instead of re-scrambling communities,
    // and the weak link strength stops cross-community `related` edges from
    // pulling everything back into one blob.
    link: { distance: 40, strength: 0.25 },
    collide: { radius: 16, strength: 1.0 },
    manyBody: { strength: -70 },
    alpha: 0.3,
    alphaDecay: 0.04,
    velocityDecay: 0.6,
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

export interface BuildOptions {
  /** Node ids allowed to show a label (importance-ranked LOD bucket). */
  labeled: Set<string>;
  /** 'cluster' colors by community; 'type' by claim/unit/source. */
  colorBy: 'cluster' | 'type';
  /** 'none' keeps the deterministic seeded positions (overview). */
  layout: keyof typeof LAYOUT_PRESETS | 'none';
}

export function buildGraphOptions(
  data: GraphResponse,
  opts: BuildOptions,
): GraphOptions {
  const labeled = opts.labeled;
  const animation = data.nodes.length <= 1500;
  const layout =
    opts.layout === 'none' ? undefined : { ...LAYOUT_PRESETS[opts.layout] };

  return {
    data: toG6Data(data, { intraClusterEdgesOnly: opts.layout === 'none' }),
    animation,
    autoResize: true,
    padding: 24,
    autoFit: 'view',
    node: {
      style: {
        size: (d: NodeData) => nodeSize(nodeOf(d)),
        fill: (d: NodeData) => {
          const n = nodeOf(d);
          return opts.colorBy === 'cluster' && n.type === 'claim'
            ? clusterColor(n.cluster)
            : nodeColor(n.type);
        },
        fillOpacity: 0.92,
        lineWidth: (d: NodeData) =>
          nodeOf(d).strength && nodeOf(d).strength !== 'supported' ? 1.5 : 0,
        stroke: COLORS.textMuted,
        lineDash: (d: NodeData) =>
          nodeOf(d).strength && nodeOf(d).strength !== 'supported'
            ? [3, 2]
            : [],
        labelText: (d: NodeData) => {
          const n = nodeOf(d);
          return labeled.has(n.id) ? n.label : '';
        },
        labelFill: COLORS.text,
        labelFontSize: 11,
        labelFontFamily: 'Inter, "Noto Sans SC", system-ui, sans-serif',
        labelBackground: true,
        labelBackgroundFill: 'rgba(15, 17, 23, 0.78)',
        labelBackgroundRadius: 4,
        labelPadding: [2, 5],
        labelPlacement: 'bottom',
        labelMaxWidth: 240,
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
        strokeOpacity: 0.28,
      },
      state: {
        highlight: { strokeOpacity: 0.9, stroke: COLORS.highlight },
        dimmed: { strokeOpacity: 0.06 },
      },
    },
    ...(layout ? { layout } : {}),
    behaviors: [
      'zoom-canvas',
      'drag-canvas',
      'drag-element',
      { type: 'brush-select', trigger: 'shift' },
    ],
    // Positions are pre-seeded, so hulls can be part of the initial options —
    // no need to wait for a layout event.
    plugins: hullPlugins(data.communities, data.nodes),
  };
}

/** Top-N node ids by importance — the label LOD bucket for the overview. */
export function topLabelIds(data: GraphResponse, n: number): Set<string> {
  return new Set(
    [...data.nodes]
      .sort((a, b) => (b.importance ?? 0) - (a.importance ?? 0))
      .slice(0, n)
      .map((d) => d.id),
  );
}
