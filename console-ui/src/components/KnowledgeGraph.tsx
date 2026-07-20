/** KnowledgeGraph — the scoped force-directed graph (design §4, KMEM pattern).
 * One component, three scopes:
 *
 *   scope='neighborhood' id=<source sha>  → this source + its citing claims +
 *                                           sibling sources + memory cards
 *   scope='global'                        → the overview graph, claims colored
 *                                           by community
 *   scope='theme'        id=<theme>       → the theme's claims + their sources
 *
 * Rendered with react-force-graph-2d (canvas + d3-force). Over the old G6 view
 * this adds: zoom-based LEVEL-OF-DETAIL (labels declutter as you zoom out and
 * reveal as you zoom in, gated per-node by importance so hubs label first),
 * hover-to-highlight-neighborhood, click-to-focus with an animated re-center,
 * community coloring + legend, and an info card with an explicit open action.
 * Colors come from the DS custom properties, re-read when `data-theme` flips.
 *
 * react-force-graph-2d loads lazily so portal pages stay light. */
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { forceCollide } from 'd3-force';
import { polygonCentroid, polygonHull } from 'd3-polygon';
import { useNavigate } from 'react-router-dom';
import { useI18n } from '../i18n';
import {
  fetchClaim,
  fetchGlobalGraph,
  fetchSourceNeighborhood,
  fetchThemeGraph,
} from '../lib/api';
import { closureNodeIds, isMiscTheme, themeRoute } from '../lib/derive';
import type { ClaimDetail, GraphNode, GraphResponse } from '../lib/types';
import { useModel } from '../model';
import { EmptyState } from './ui';

/* eslint-disable @typescript-eslint/no-explicit-any */
const ForceGraph2D = lazy(() => import('react-force-graph-2d')) as any;
const ForceGraph3D = lazy(() => import('react-force-graph-3d')) as any;
/* eslint-enable @typescript-eslint/no-explicit-any */

export type KnowledgeGraphScope = 'neighborhood' | 'global' | 'theme';

export interface KnowledgeGraphProps {
  scope: KnowledgeGraphScope;
  /** neighborhood: source sha256 · theme: theme name · global: unused. */
  id?: string;
  /** Embedded height in px (default 360). */
  height?: number;
  /** Global scope only: claim- vs source-centric overview (the portal's shared
   * perspective toggle). Ignored by neighborhood/theme scopes. */
  persp?: 'claim' | 'source';
}

const DEFAULT_HEIGHT = 360;
/** Base zoom at which a MAX-importance node reveals its label; leaves need to
 * be zoomed in further. Below this the graph reads as a labelled constellation
 * of only its most important nodes — the level-of-detail the old view lacked. */
const LABEL_BASE_ZOOM = 1.9;

interface DsTokens {
  link: string;
  linkHi: string;
  text: string;
  muted: string;
  surface: string;
  accent: string;
  bg: string;
  community: string[];
}

function readTokens(): DsTokens {
  const cs = getComputedStyle(document.documentElement);
  const v = (name: string) => cs.getPropertyValue(name).trim();
  return {
    link: v('--graph-link'),
    linkHi: v('--graph-link-hi'),
    text: v('--text'),
    muted: v('--muted'),
    surface: v('--surface'),
    accent: v('--accent'),
    bg: v('--graph-bg') || '#0d0f13',
    community: [1, 2, 3, 4, 5, 6, 7, 8].map((n) => v(`--c-${n}`)),
  };
}

function nodeFill(type: string, t: DsTokens): string {
  if (type === 'source') return t.community[0];
  if (type === 'claim') return t.community[2];
  return t.community[1]; // card + unit share the memory-layer color
}

/** Global scope colors claims by community; focused scopes color by kind. */
function scopedFill(scope: KnowledgeGraphScope, n: GraphNode, t: DsTokens): string {
  if (scope === 'global' && n.cluster > 0) {
    return t.community[(n.cluster - 1) % t.community.length];
  }
  return nodeFill(n.type, t);
}

/** Node radius in graph units, driven by importance (focus node is largest). */
function nodeRadius(n: GraphNode, isFocus: boolean): number {
  if (isFocus) return 9;
  const imp = n.importance ?? 0;
  return n.type === 'source' ? 4 + 4 * imp : 3 + 6 * imp;
}

/** Per-node label LOD: an important (hub) node reveals its label at a lower
 * zoom than a leaf, so zooming out declutters to just the backbone. `forced`
 * (focus/selected/hovered) always labels. */
function shouldLabel(n: GraphNode, zoom: number, forced: boolean): boolean {
  if (forced) return true;
  const imp = n.importance ?? 0;
  return zoom >= LABEL_BASE_ZOOM * (1 - 0.7 * imp);
}

// react-force-graph mutates node objects with x/y/z at runtime.
type FGNode = GraphNode & { x?: number; y?: number; z?: number; vx?: number; vy?: number };

/** A custom d3 force that pulls every node toward its community's live centroid,
 * so communities settle into DISTINCT spatial regions (clean, separated hulls)
 * instead of intermixing. Weak enough that links still shape the within-cluster
 * structure. */
function clusterForce(strength: number) {
  let nodes: FGNode[] = [];
  const force = (alpha: number) => {
    const cen = new Map<number, { x: number; y: number; n: number }>();
    for (const nd of nodes) {
      if (nd.cluster > 0) {
        const c = cen.get(nd.cluster) ?? { x: 0, y: 0, n: 0 };
        c.x += nd.x ?? 0;
        c.y += nd.y ?? 0;
        c.n += 1;
        cen.set(nd.cluster, c);
      }
    }
    for (const c of cen.values()) {
      c.x /= c.n;
      c.y /= c.n;
    }
    const k = strength * alpha;
    for (const nd of nodes) {
      const c = cen.get(nd.cluster);
      if (c) {
        nd.vx = (nd.vx ?? 0) + (c.x - (nd.x ?? 0)) * k;
        nd.vy = (nd.vy ?? 0) + (c.y - (nd.y ?? 0)) * k;
      }
    }
  };
  force.initialize = (n: FGNode[]) => {
    nodes = n;
  };
  return force;
}

/** react-force-graph-3d injects `nodeLabel` as tooltip innerHTML — escape it so
 * a source title / claim containing markup can't run in the console origin. */
function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"']/g,
    (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] ?? c,
  );
}

/** WebGL2 capability probe — gate the 3D toggle so a browser without it doesn't
 * throw while constructing the ForceGraph3D renderer and unmount the app.
 * three@0.185 dropped WebGL1, so a webgl1-only context must NOT pass. */
function webglAvailable(): boolean {
  try {
    return !!document.createElement('canvas').getContext('webgl2');
  } catch {
    return false;
  }
}

export default function KnowledgeGraph({
  scope,
  id,
  height = DEFAULT_HEIGHT,
  persp = 'claim',
}: KnowledgeGraphProps) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { model } = useModel();
  const knownShas = useMemo(
    () => new Set((model?.sources ?? []).map((s) => s.sha256)),
    [model],
  );

  const wrapRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fgRef = useRef<any>(null);
  const [data, setData] = useState<GraphResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  /** Evidence closure of the SELECTED claim node (VZ1): its full citation
   * detail, fetched on selection. `forId` guards against a stale response
   * landing after the selection moved on. */
  const [closure, setClosure] = useState<{ forId: string; detail: ClaimDetail } | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [mode, setMode] = useState<'2d' | '3d'>('2d');
  const [no3d, setNo3d] = useState(false);
  const [dims, setDims] = useState({ w: 0, h: height });
  const [themeVersion, setThemeVersion] = useState(0);
  // One-shot auto-fit per dataset, so a click-focus or a user pan isn't yanked
  // back by a later engine-stop.
  const fittedRef = useRef(false);
  const hoverTimer = useRef<number | undefined>(undefined);

  // Hover-INTENT: apply the hover highlight only once the cursor SETTLES. Every
  // node-enter/leave resets the timer, so sweeping the mouse fast across many
  // nodes changes nothing — the highlight (and its dimming) only kicks in when
  // you pause on a node (or in empty space), which is exactly when a focus
  // effect is wanted. This is what kills the flicker, not removing the dim.
  const handleHover = useCallback((n: { id: string } | null) => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current);
    const next = n?.id ?? null;
    hoverTimer.current = window.setTimeout(() => {
      setHoverId((prev) => (prev === next ? prev : next));
    }, 120);
  }, []);

  const tokens = useMemo(() => readTokens(), [themeVersion]);
  const focusId = scope === 'neighborhood' && id ? `source:${id}` : null;

  // Rebuild with new tokens when the theme flips.
  useEffect(() => {
    const observer = new MutationObserver(() => setThemeVersion((v) => v + 1));
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    });
    return () => observer.disconnect();
  }, []);

  // Track the container size so the canvas fills it (and refits on fullscreen).
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setDims({ w: el.clientWidth, h: el.clientHeight });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Scope → endpoint.
  useEffect(() => {
    const request =
      scope === 'global'
        ? fetchGlobalGraph(400, persp)
        : id
          ? scope === 'neighborhood'
            ? fetchSourceNeighborhood(id)
            : fetchThemeGraph(id)
          : null;
    let cancelled = false;
    setData(null);
    setError(null);
    setSelected(null);
    setHoverId(null);
    if (!request) {
      setError(`KnowledgeGraph scope=${scope} requires id`);
      return;
    }
    request
      .then((resp) => !cancelled && setData(resp))
      .catch((err: unknown) => !cancelled && setError(String(err)));
    return () => {
      cancelled = true;
    };
  }, [scope, id, persp]);

  // Fresh node/link objects per dataset (react-force-graph owns their physics).
  const graphData = useMemo(() => {
    if (!data) return { nodes: [] as FGNode[], links: [] };
    return {
      nodes: data.nodes.map((n) => ({ ...n })) as FGNode[],
      links: data.edges.map((e) => ({
        source: e.source,
        target: e.target,
        type: e.type,
        weight: e.weight,
      })),
    };
  }, [data]);

  // id → neighbor ids, for hover dimming.
  const adjacency = useMemo(() => {
    const m = new Map<string, Set<string>>();
    for (const e of data?.edges ?? []) {
      (m.get(e.source) ?? m.set(e.source, new Set()).get(e.source)!).add(e.target);
      (m.get(e.target) ?? m.set(e.target, new Set()).get(e.target)!).add(e.source);
    }
    return m;
  }, [data]);

  const nodeById = useMemo(
    () => new Map((data?.nodes ?? []).map((n) => [n.id, n])),
    [data],
  );

  // Fetch the selected claim's evidence closure. Silent on failure — the
  // highlight then falls back to direct graph neighbors and the panel keeps
  // its pre-VZ1 content.
  useEffect(() => {
    if (selected?.type !== 'claim') {
      setClosure(null);
      return;
    }
    const nodeId = selected.id;
    const claimKey = nodeId.startsWith('claim:') ? nodeId.slice('claim:'.length) : nodeId;
    let cancelled = false;
    fetchClaim(claimKey)
      .then((detail) => {
        if (!cancelled) setClosure({ forId: nodeId, detail });
      })
      .catch(() => {
        if (!cancelled) setClosure(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selected]);

  /** Node ids inside the selected claim's evidence closure, or null when no
   * claim is selected. Everything OUTSIDE the closure dims. */
  const closureSet = useMemo(() => {
    if (selected?.type !== 'claim') return null;
    const detail = closure?.forId === selected.id ? closure.detail : null;
    return closureNodeIds(
      selected.id,
      detail?.citations ?? null,
      (id) => nodeById.has(id),
      adjacency,
    );
  }, [selected, closure, nodeById, adjacency]);

  // Configure forces the moment the (lazy) graph instance mounts — a
  // data-effect would run while the graph is still suspended (ref null) and
  // never apply. Also fires when 2D/3D swaps to a fresh instance.
  const setFg = useCallback((fg: unknown) => {
    fgRef.current = fg;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = fg as any;
    if (!g?.d3Force) return;
    // Tight, well-spaced clusters: GENTLE repulsion (the old -140 blew clusters
    // apart AND stretched each one), SHORT STRONG links so connected/related
    // claims pull together, and a COLLIDE force so nodes sit close without
    // overlapping. The default center force keeps the whole thing compact.
    g.d3Force('charge')?.strength(-34).distanceMax(240);
    g.d3Force('link')?.distance(16).strength(1);
    g.d3Force(
      'collide',
      forceCollide((n: FGNode) => nodeRadius(n, n.id === focusId) + 2).strength(0.9),
    );
    // Global scope colors by community — pull each community together so the
    // hull blobs are compact + separated. Focused scopes have no communities.
    g.d3Force('cluster', scope === 'global' ? clusterForce(0.22) : null);
    g.d3ReheatSimulation?.();
  }, [focusId, scope]);

  // New dataset → allow one auto-fit again.
  useEffect(() => {
    fittedRef.current = false;
  }, [data, mode]);

  /** ~15% alpha variant of a color — the out-of-closure fade for 3D nodes
   * and 2D links, matching drawNode's globalAlpha dim. Handles both the
   * #rrggbb form and the rgba(...) form the --graph-link tokens use
   * (alpha replaced, not multiplied — the fade is the statement). */
  const faintColor = (c: string) => {
    if (/^#[0-9a-f]{6}$/i.test(c)) return `${c}26`;
    const m = c.match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)/i);
    if (m) return `rgba(${m[1]}, ${m[2]}, ${m[3]}, 0.15)`;
    return c;
  };

  const openNode = (n: GraphNode) => {
    if (n.type === 'source') {
      const sha = n.id.slice('source:'.length);
      if (knownShas.has(sha)) navigate(`/library/${sha}`);
    } else if (n.type === 'claim') {
      // Straight to the claim's theme page, anchored to the claim card. Using
      // the node's own `theme` skips the /knowledge# bounce (and its
      // dead-end when a claim has no theme — themeRoute routes '' too).
      const claimId = n.claim_id ?? n.id.slice('claim:'.length);
      navigate(`${themeRoute(n.theme)}#${claimId}`);
    }
  };

  const canOpen = (n: GraphNode) =>
    n.type === 'claim' ||
    (n.type === 'source' && knownShas.has(n.id.slice('source:'.length)));

  const kindLabel = (type: string) =>
    type === 'claim'
      ? t('graph.kindClaim')
      : type === 'source'
        ? t('graph.kindSource')
        : type === 'card'
          ? t('graph.kindCard')
          : t('graph.kindUnit');

  // Soft translucent community "blobs" behind the graph (global scope only), so
  // same-cluster claims read as one group at a glance. A convex hull per
  // community, expanded and smoothed, filled + stroked with the community color
  // at low alpha. Drawn in the PRE pass so it sits behind edges + nodes.
  const drawHulls = (ctx: CanvasRenderingContext2D, zoom: number) => {
    if (scope !== 'global') return;
    const byCluster = new Map<number, [number, number][]>();
    for (const n of graphData.nodes) {
      if (n.cluster > 0 && n.x != null && n.y != null) {
        let arr = byCluster.get(n.cluster);
        if (!arr) {
          arr = [];
          byCluster.set(n.cluster, arr);
        }
        arr.push([n.x, n.y]);
      }
    }
    const pad = 16;
    const mid = (a: [number, number], b: [number, number]): [number, number] => [
      (a[0] + b[0]) / 2,
      (a[1] + b[1]) / 2,
    ];
    ctx.lineJoin = 'round';
    for (const [cluster, pts] of byCluster) {
      if (pts.length < 3) continue;
      const hull = polygonHull(pts);
      if (!hull || hull.length < 3) continue;
      const [cx, cy] = polygonCentroid(hull);
      // Expand each vertex outward from the centroid so the blob wraps the
      // nodes with breathing room.
      const exp = hull.map(([x, y]) => {
        const dx = x - cx;
        const dy = y - cy;
        const d = Math.hypot(dx, dy) || 1;
        return [x + (dx / d) * pad, y + (dy / d) * pad] as [number, number];
      });
      const color = tokens.community[(cluster - 1) % tokens.community.length];
      const nn = exp.length;
      ctx.beginPath();
      const start = mid(exp[nn - 1], exp[0]);
      ctx.moveTo(start[0], start[1]);
      for (let i = 0; i < nn; i++) {
        const m = mid(exp[i], exp[(i + 1) % nn]);
        ctx.quadraticCurveTo(exp[i][0], exp[i][1], m[0], m[1]);
      }
      ctx.closePath();
      ctx.fillStyle = color;
      ctx.globalAlpha = 0.1;
      ctx.fill();
      ctx.globalAlpha = 0.22;
      ctx.lineWidth = 1.5 / zoom;
      ctx.strokeStyle = color;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
  };

  const drawNode = (node: FGNode, ctx: CanvasRenderingContext2D, zoom: number) => {
    const isFocus = node.id === focusId;
    const isSel = selected?.id === node.id;
    const isHover = hoverId === node.id;
    const isNeighbor =
      hoverId != null && (adjacency.get(hoverId)?.has(node.id) ?? false);
    // Dim the rest to focus the hovered neighborhood — or, when a claim is
    // selected, everything OUTSIDE its evidence closure (VZ1: the claim +
    // its cited sources stay lit; hover still punches through the dim).
    const dim = closureSet
      ? !closureSet.has(node.id) && !isHover
      : hoverId != null && !isHover && !isNeighbor;
    const r = nodeRadius(node, isFocus);
    const x = node.x ?? 0;
    const y = node.y ?? 0;

    ctx.globalAlpha = dim ? 0.15 : 1;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, 2 * Math.PI);
    ctx.fillStyle = scopedFill(scope, node, tokens);
    ctx.fill();
    if (isFocus || isSel || isHover || isNeighbor) {
      ctx.lineWidth = (isHover || isSel ? 1.6 : 1) / zoom;
      ctx.strokeStyle = isSel || isHover || isNeighbor ? tokens.linkHi : tokens.accent;
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
    // Labels are drawn in a SEPARATE post pass (drawLabels) with a budget +
    // collision so they never overlap into spaghetti at any zoom.
  };

  // Second pass over ALL nodes: pick a bounded set of non-overlapping labels,
  // ranked (forced first, then importance), so zooming in never floods the
  // canvas with overlapping text. Runs each frame with the live zoom.
  const drawLabels = (ctx: CanvasRenderingContext2D, zoom: number) => {
    // Hover LABELS the node + its neighbors (on top of the normal budget)
    // rather than hiding everything else — hiding/showing the whole label set
    // as the mouse moved was a flicker source.
    const forced = (n: FGNode) =>
      n.id === focusId ||
      n.id === selected?.id ||
      n.id === hoverId ||
      (hoverId != null && (adjacency.get(hoverId)?.has(n.id) ?? false));
    const ranked = [...graphData.nodes].sort((a, b) => {
      const fa = forced(a) ? 1 : 0;
      const fb = forced(b) ? 1 : 0;
      if (fa !== fb) return fb - fa;
      return (b.importance ?? 0) - (a.importance ?? 0);
    });
    const placed: { x0: number; y0: number; x1: number; y1: number }[] = [];
    let budget = fullscreen ? 40 : 22;
    const fontSize = 12 / zoom; // constant on-screen size
    ctx.font = `${fontSize}px 'IBM Plex Sans', 'IBM Plex Sans SC', system-ui, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'alphabetic';
    const pad = 2 / zoom;
    // Spend the budget on VISIBLE nodes: map each node to screen space via the
    // current transform and skip off-canvas ones, so zooming into a region
    // labels that region (not off-screen global hubs).
    const m = ctx.getTransform();
    const W = dims.w || m.a; // fallback if width unknown
    const H = dims.h || m.d;
    const onScreen = (x: number, y: number) => {
      const sx = m.a * x + m.c * y + m.e;
      const sy = m.b * x + m.d * y + m.f;
      return sx >= -20 && sx <= W + 20 && sy >= -20 && sy <= H + 20;
    };

    for (const n of ranked) {
      const isForced = forced(n);
      if (!isForced) {
        // While settled on a node, only the neighborhood (forced) is labelled.
        if (hoverId != null) continue;
        if (budget <= 0) break;
        if (!onScreen(n.x ?? 0, n.y ?? 0)) continue;
        if (!shouldLabel(n, zoom, false)) continue;
      }
      const x = n.x ?? 0;
      const y = n.y ?? 0;
      const r = nodeRadius(n, n.id === focusId);
      const label = n.label.length > 42 ? `${n.label.slice(0, 41)}…` : n.label;
      const tw = ctx.measureText(label).width;
      const ly = y + r + fontSize;
      const box = {
        x0: x - tw / 2 - pad,
        y0: ly - fontSize - pad,
        x1: x + tw / 2 + pad,
        y1: ly + pad,
      };
      // Collision: skip a non-forced label that overlaps a placed one.
      if (
        !isForced &&
        placed.some(
          (p) => box.x0 < p.x1 && box.x1 > p.x0 && box.y0 < p.y1 && box.y1 > p.y0,
        )
      )
        continue;
      placed.push(box);
      if (!isForced) budget -= 1;

      ctx.fillStyle = tokens.surface;
      ctx.globalAlpha = 0.82;
      ctx.fillRect(box.x0, box.y0, box.x1 - box.x0, box.y1 - box.y0);
      ctx.globalAlpha = 1;
      ctx.fillStyle = tokens.text;
      ctx.fillText(label, x, ly - fontSize * 0.2);
    }
  };

  const paintPointerArea = (
    node: FGNode,
    color: string,
    ctx: CanvasRenderingContext2D,
  ) => {
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(node.x ?? 0, node.y ?? 0, nodeRadius(node, node.id === focusId) + 2, 0, 2 * Math.PI);
    ctx.fill();
  };

  const onNodeClick = (node: FGNode) => {
    const n = nodeById.get(node.id) ?? node;
    // Sources open on single click (matches the Terrain view). Claims now
    // SELECT instead (VZ1): the click lights up the claim's evidence closure
    // and the side panel lists its citations — navigation moved to the
    // panel's Open button. Other non-openable nodes fall back to select+zoom.
    if (n.type !== 'claim' && canOpen(n)) {
      openNode(n);
      return;
    }
    setSelected(n);
    const fg = fgRef.current;
    if (fg && node.x != null && node.y != null) {
      fg.centerAt(node.x, node.y, 500);
      fg.zoom(Math.max(2.4, fg.zoom()), 500);
    }
  };

  // 3D: same contract — sources open, claims select + closure + camera fly.
  const onNodeClick3D = (node: FGNode) => {
    const n = nodeById.get(node.id) ?? node;
    if (n.type !== 'claim' && canOpen(n)) {
      openNode(n);
      return;
    }
    setSelected(n);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const fg = fgRef.current as any;
    const { x = 0, y = 0, z = 0 } = node;
    const dist = 120;
    const d = Math.hypot(x, y, z);
    // A node at (or near) the origin can't be offset by scaling — the camera
    // would land ON its look-at target. Pull back along +z instead.
    const to =
      d < 1e-3
        ? { x: 0, y: 0, z: dist }
        : { x: x * (1 + dist / d), y: y * (1 + dist / d), z: z * (1 + dist / d) };
    fg?.cameraPosition(to, node, 600);
  };

  // Legend click → fly to that community's centroid so you don't have to hunt
  // for it (2D: center + zoom; 3D: pull the camera in on the cluster).
  const focusCommunity = (cluster: number) => {
    const pts = (graphData.nodes as FGNode[]).filter(
      (n) => n.cluster === cluster && n.x != null,
    );
    if (!pts.length) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const fg = fgRef.current as any;
    if (!fg) return;
    const cx = pts.reduce((s, n) => s + (n.x ?? 0), 0) / pts.length;
    const cy = pts.reduce((s, n) => s + (n.y ?? 0), 0) / pts.length;
    setHoverId(null);
    if (mode === '3d') {
      const cz = pts.reduce((s, n) => s + (n.z ?? 0), 0) / pts.length;
      const d = Math.hypot(cx, cy, cz) || 1;
      const dist = 110;
      const r = 1 + dist / d;
      fg.cameraPosition({ x: cx * r, y: cy * r, z: cz * r }, { x: cx, y: cy, z: cz }, 700);
    } else {
      fg.centerAt(cx, cy, 600);
      fg.zoom(Math.max(2.6, fg.zoom?.() ?? 2.6), 600);
    }
  };

  const empty = !error && data && data.nodes.length === 0;
  const communitiesForLegend =
    scope === 'global' ? (data?.communities ?? []).slice(0, 8) : [];

  return (
    <div
      ref={wrapRef}
      className={`graph-embed${fullscreen ? ' fullscreen' : ''}`}
      style={fullscreen ? undefined : { height }}
    >
      {error && (
        <EmptyState>
          <p>{t('graph.error')}</p>
        </EmptyState>
      )}
      {empty && (
        <EmptyState>
          <p>
            {scope === 'neighborhood'
              ? t('graph.empty')
              : scope === 'theme'
                ? t('graph.emptyTheme')
                : t('graph.emptyGlobal')}
          </p>
        </EmptyState>
      )}
      {!error && !data && <div className="graph-note">{t('graph.loading')}</div>}
      {!error && data && data.nodes.length > 0 && (
        <>
          <Suspense fallback={<div className="graph-note">{t('graph.loading')}</div>}>
            {mode === '2d' ? (
              <ForceGraph2D
                ref={setFg}
                width={dims.w || undefined}
                height={dims.h || height}
                graphData={graphData}
                backgroundColor="transparent"
                cooldownTicks={140}
                onEngineStop={() => {
                  if (fittedRef.current) return;
                  fittedRef.current = true;
                  fgRef.current?.zoomToFit(400, 36);
                }}
                nodeRelSize={4}
                nodeCanvasObjectMode={() => 'replace'}
                nodeCanvasObject={drawNode}
                onRenderFramePre={drawHulls}
                onRenderFramePost={drawLabels}
                nodePointerAreaPaint={paintPointerArea}
                linkColor={(l: { source: FGNode; target: FGNode }) => {
                  const s = (l.source as FGNode).id;
                  const t2 = (l.target as FGNode).id;
                  if (closureSet) {
                    // Closure edges (claim ↔ cited source) light up; every
                    // other edge fades with its nodes.
                    return closureSet.has(s) && closureSet.has(t2)
                      ? tokens.linkHi
                      : faintColor(tokens.link);
                  }
                  const active = hoverId != null && (s === hoverId || t2 === hoverId);
                  return active ? tokens.linkHi : tokens.link;
                }}
                linkWidth={(l: { source: FGNode; target: FGNode }) => {
                  const s = (l.source as FGNode).id;
                  const t2 = (l.target as FGNode).id;
                  if (closureSet) {
                    return closureSet.has(s) && closureSet.has(t2) ? 1.5 : 0.4;
                  }
                  return hoverId != null && (s === hoverId || t2 === hoverId) ? 1.5 : 0.6;
                }}
                onNodeHover={handleHover}
                onNodeClick={onNodeClick}
                onBackgroundClick={() => setSelected(null)}
              />
            ) : (
              <ForceGraph3D
                ref={setFg}
                width={dims.w || undefined}
                height={dims.h || height}
                graphData={graphData}
                backgroundColor="#0b0e15"
                nodeRelSize={4}
                nodeColor={(n: FGNode) => {
                  if (n.id === hoverId) return tokens.linkHi;
                  const fill = scopedFill(scope, n, tokens);
                  return closureSet && !closureSet.has(n.id) ? faintColor(fill) : fill;
                }}
                nodeVal={(n: FGNode) => 1.5 + 6 * (n.importance ?? 0)}
                nodeLabel={(n: FGNode) => escapeHtml(n.label)}
                nodeOpacity={1}
                nodeResolution={12}
                showNavInfo={false}
                linkColor={(l: { source: FGNode; target: FGNode }) => {
                  const s = (l.source as FGNode).id;
                  const t2 = (l.target as FGNode).id;
                  if (closureSet) {
                    return closureSet.has(s) && closureSet.has(t2)
                      ? tokens.linkHi
                      : '#2a2f3a';
                  }
                  return hoverId != null && (s === hoverId || t2 === hoverId)
                    ? tokens.linkHi
                    : '#5a6270';
                }}
                linkOpacity={0.5}
                linkWidth={0.6}
                onNodeHover={handleHover}
                onNodeClick={onNodeClick3D}
                onBackgroundClick={() => setSelected(null)}
              />
            )}
          </Suspense>
          <div className="graph-controls">
            <button
              type="button"
              className="graph-expand"
              onClick={() => {
                setMode((m) => {
                  // Probe WebGL before mounting ForceGraph3D — a GPU-less
                  // browser would otherwise throw and unmount the app.
                  if (m === '2d' && !webglAvailable()) {
                    setNo3d(true);
                    return '2d';
                  }
                  return m === '2d' ? '3d' : '2d';
                });
              }}
            >
              {mode === '2d' ? '3D' : '2D'}
            </button>
            <button
              type="button"
              className="graph-expand"
              onClick={() => setFullscreen((f) => !f)}
            >
              {fullscreen ? t('graph.exitFullscreen') : t('graph.fullscreen')}
            </button>
          </div>
          {data.truncated && (
            <div className="graph-note graph-truncated">{t('graph.truncated')}</div>
          )}
          {no3d && (
            <div className="graph-note graph-webgl-note">{t('graph.no3d')}</div>
          )}
          {mode === '3d' && !no3d && (
            <div className="graph-note graph-controls-hint">{t('graph.controls3d')}</div>
          )}
          {communitiesForLegend.length > 0 && (
            <div className="graph-legend">
              {communitiesForLegend.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  className="graph-legend-item"
                  title={t('graph.focusCommunity')}
                  onClick={() => focusCommunity(c.id)}
                >
                  <span
                    className="graph-legend-dot"
                    style={{
                      background:
                        tokens.community[(c.id - 1) % tokens.community.length],
                    }}
                  />
                  <span className="tiny">
                    {isMiscTheme(c.label) ? t('theme.unclassified') : c.label}
                  </span>
                </button>
              ))}
            </div>
          )}
          {selected && (
            <div className="graph-info">
              <div className="graph-info-kind">
                <span className="pill">{kindLabel(selected.type)}</span>
                {selected.strength && (
                  <span className="tiny muted"> {selected.strength}</span>
                )}
              </div>
              <div className="graph-info-title">{selected.label}</div>
              {selected.theme && (
                <div className="tiny muted">
                  {isMiscTheme(selected.theme)
                    ? t('theme.unclassified')
                    : selected.theme}
                </div>
              )}
              {canOpen(selected) ? (
                <button
                  type="button"
                  className="graph-info-open"
                  onClick={() => openNode(selected)}
                >
                  {t('graph.open')}
                </button>
              ) : (
                <div className="tiny muted">
                  {selected.type === 'card' ? t('graph.cardHint') : t('graph.noPage')}
                </div>
              )}
              {selected.type === 'claim' && closure?.forId === selected.id && (
                <div className="graph-evidence">
                  <div className="graph-evidence-title">
                    {t('graph.evidenceTitle')}
                  </div>
                  {closure.detail.citations.map((c, i) => (
                    <div className="graph-evidence-item" key={`${c.unit_id}-${i}`}>
                      <div className="graph-evidence-quote">
                        “{c.quote.length > 160 ? `${c.quote.slice(0, 160)}…` : c.quote}”
                      </div>
                      <div className="tiny muted">
                        {c.unit_id}
                        {c.resolved_line != null &&
                          ` · ${t('graph.evidenceLine', { n: c.resolved_line })}`}
                        {' · '}
                        {c.source_sha256 && knownShas.has(c.source_sha256) ? (
                          <button
                            type="button"
                            className="graph-evidence-src"
                            onClick={() => navigate(`/library/${c.source_sha256}`)}
                          >
                            {c.source_title || c.case_id}
                          </button>
                        ) : (
                          <span>{c.source_title || c.case_id}</span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
