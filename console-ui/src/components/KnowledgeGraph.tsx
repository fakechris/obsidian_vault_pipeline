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
import { useNavigate } from 'react-router-dom';
import { useI18n } from '../i18n';
import {
  fetchGlobalGraph,
  fetchSourceNeighborhood,
  fetchThemeGraph,
} from '../lib/api';
import { isMiscTheme } from '../lib/derive';
import type { GraphNode, GraphResponse } from '../lib/types';
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
type FGNode = GraphNode & { x?: number; y?: number; z?: number };

/** react-force-graph-3d injects `nodeLabel` as tooltip innerHTML — escape it so
 * a source title / claim containing markup can't run in the console origin. */
function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"']/g,
    (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] ?? c,
  );
}

/** WebGL capability probe — gate the 3D toggle so a GPU-less browser doesn't
 * throw while constructing the ForceGraph3D renderer and unmount the app. */
function webglAvailable(): boolean {
  try {
    const c = document.createElement('canvas');
    return !!(c.getContext('webgl2') || c.getContext('webgl'));
  } catch {
    return false;
  }
}

export default function KnowledgeGraph({
  scope,
  id,
  height = DEFAULT_HEIGHT,
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
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [mode, setMode] = useState<'2d' | '3d'>('2d');
  const [no3d, setNo3d] = useState(false);
  const [dims, setDims] = useState({ w: 0, h: height });
  const [themeVersion, setThemeVersion] = useState(0);
  // One-shot auto-fit per dataset, so a click-focus or a user pan isn't yanked
  // back by a later engine-stop.
  const fittedRef = useRef(false);

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
        ? fetchGlobalGraph()
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
  }, [scope, id]);

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

  // Configure forces the moment the (lazy) graph instance mounts — a
  // data-effect would run while the graph is still suspended (ref null) and
  // never apply. Also fires when 2D/3D swaps to a fresh instance.
  const setFg = useCallback((fg: unknown) => {
    fgRef.current = fg;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = fg as any;
    if (!g?.d3Force) return;
    g.d3Force('charge')?.strength(-140);
    g.d3Force('link')?.distance(38).strength(0.5);
  }, []);

  // New dataset → allow one auto-fit again.
  useEffect(() => {
    fittedRef.current = false;
  }, [data, mode]);

  const openNode = (n: GraphNode) => {
    if (n.type === 'source') {
      const sha = n.id.slice('source:'.length);
      if (knownShas.has(sha)) navigate(`/library/${sha}`);
    } else if (n.type === 'claim') {
      navigate(`/knowledge#${n.claim_id ?? n.id.slice('claim:'.length)}`);
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

  const drawNode = (node: FGNode, ctx: CanvasRenderingContext2D, zoom: number) => {
    const isFocus = node.id === focusId;
    const isSel = selected?.id === node.id;
    const isHover = hoverId === node.id;
    const dim =
      hoverId != null &&
      !isHover &&
      !(adjacency.get(hoverId)?.has(node.id) ?? false);
    const r = nodeRadius(node, isFocus);
    const x = node.x ?? 0;
    const y = node.y ?? 0;

    ctx.globalAlpha = dim ? 0.12 : 1;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, 2 * Math.PI);
    ctx.fillStyle = scopedFill(scope, node, tokens);
    ctx.fill();
    if (isFocus || isSel || isHover) {
      ctx.lineWidth = 1.5 / zoom;
      ctx.strokeStyle = isSel || isHover ? tokens.linkHi : tokens.accent;
      ctx.stroke();
    }

    if (!dim && shouldLabel(node, zoom, isFocus || isSel || isHover)) {
      const fontSize = Math.min(14 / zoom, r * 1.6 + 4 / zoom);
      ctx.font = `${fontSize}px 'IBM Plex Sans', 'IBM Plex Sans SC', system-ui, sans-serif`;
      const label =
        node.label.length > 42 ? `${node.label.slice(0, 41)}…` : node.label;
      const tw = ctx.measureText(label).width;
      const ly = y + r + fontSize * 0.9;
      ctx.fillStyle = tokens.surface;
      ctx.globalAlpha = dim ? 0.12 : 0.82;
      ctx.fillRect(x - tw / 2 - 2 / zoom, ly - fontSize, tw + 4 / zoom, fontSize + 2 / zoom);
      ctx.globalAlpha = dim ? 0.12 : 1;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'alphabetic';
      ctx.fillStyle = tokens.text;
      ctx.fillText(label, x, ly - fontSize * 0.2);
    }
    ctx.globalAlpha = 1;
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
    setSelected(n);
    const fg = fgRef.current;
    if (fg && node.x != null && node.y != null) {
      fg.centerAt(node.x, node.y, 500);
      fg.zoom(Math.max(2.4, fg.zoom()), 500);
    }
  };

  // 3D: select + fly the camera to look at the node from a fixed distance.
  const onNodeClick3D = (node: FGNode) => {
    setSelected(nodeById.get(node.id) ?? node);
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
                nodePointerAreaPaint={paintPointerArea}
                linkColor={(l: { source: FGNode; target: FGNode }) => {
                  const active =
                    hoverId != null &&
                    ((l.source as FGNode).id === hoverId ||
                      (l.target as FGNode).id === hoverId);
                  return active ? tokens.linkHi : tokens.link;
                }}
                linkWidth={(l: { source: FGNode; target: FGNode }) =>
                  hoverId != null &&
                  ((l.source as FGNode).id === hoverId ||
                    (l.target as FGNode).id === hoverId)
                    ? 1.5
                    : 0.6
                }
                onNodeHover={(n: FGNode | null) => setHoverId(n?.id ?? null)}
                onNodeClick={onNodeClick}
                onBackgroundClick={() => setSelected(null)}
              />
            ) : (
              <ForceGraph3D
                ref={setFg}
                width={dims.w || undefined}
                height={dims.h || height}
                graphData={graphData}
                backgroundColor={tokens.bg}
                nodeRelSize={4}
                nodeColor={(n: FGNode) => scopedFill(scope, n, tokens)}
                nodeVal={(n: FGNode) => 1 + 7 * (n.importance ?? 0)}
                nodeLabel={(n: FGNode) => escapeHtml(n.label)}
                nodeOpacity={0.95}
                linkColor={() => tokens.link}
                linkOpacity={0.35}
                linkWidth={0.5}
                onNodeClick={onNodeClick3D}
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
            <div className="graph-note graph-truncated">{t('graph.no3d')}</div>
          )}
          {communitiesForLegend.length > 0 && (
            <div className="graph-legend">
              {communitiesForLegend.map((c) => (
                <span key={c.id} className="graph-legend-item">
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
                </span>
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
            </div>
          )}
        </>
      )}
    </div>
  );
}
