/** Live process graph for Ask.
 *
 * As the agent touches claims / sources / memory cards, nodes accumulate
 * into a small force graph so the operator sees *what* entered the answer
 * trail, not just a linear tool log. Seeded from progress-feed hits while
 * a turn is in flight; after the answer lands, final citations are merged
 * in (cited nodes get a stronger visual weight).
 *
 * Reuses react-force-graph-2d (already on the Knowledge graph) and the same
 * DS community color tokens so the viz language stays one system.
 */
import { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useI18n } from '../i18n';
import type {
  AskCitation,
  AskProgressEvent,
  AskProgressHit,
  AskTraceEntry,
} from '../lib/types';

/* eslint-disable @typescript-eslint/no-explicit-any */
const ForceGraph2D = lazy(() => import('react-force-graph-2d')) as any;
/* eslint-enable @typescript-eslint/no-explicit-any */

export interface ProcessNode {
  id: string;
  kind: 'claim' | 'source' | 'card' | 'unit' | string;
  label: string;
  /** True when the final answer cites this entity. */
  cited?: boolean;
  /** True when a live tool result introduced this entity. */
  hit?: boolean;
  link_target?: string | null;
  /** force-graph mutates these */
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
}

export interface ProcessEdge {
  source: string;
  target: string;
  type: 'retrieved' | 'cites' | 'from';
}

export interface AskProcessGraphProps {
  /** Live progress events (while a turn is in flight). */
  events?: AskProgressEvent[];
  /** Completed tool trail (post-answer / history replay). */
  toolTrace?: AskTraceEntry[];
  /** Final answer citations — mark cited nodes and fill gaps. */
  citations?: AskCitation[];
  height?: number;
  /** Hover from the citations rail — highlight matching node. */
  hoverId?: string | null;
  onOpen?: (node: ProcessNode) => void;
}

function readToken(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return v || fallback;
}

function kindFill(kind: string): string {
  if (kind === 'source') return readToken('--c-1', '#3b82f6');
  if (kind === 'claim') return readToken('--c-3', '#22c55e');
  // card / unit / memory share the memory-layer color
  return readToken('--c-2', '#a855f7');
}

function nodeKey(kind: string, id: string): string {
  // Sources already use bare sha; claims use claim_key; cards carry a
  // synthetic card:… id from the progress feed.
  if (kind === 'source' && !id.startsWith('source:')) return `source:${id}`;
  if (kind === 'claim' && !id.startsWith('claim:') && !id.startsWith('ck-')) {
    return `claim:${id}`;
  }
  if (kind === 'claim' && id.startsWith('ck-')) return `claim:${id}`;
  return id.includes(':') ? id : `${kind}:${id}`;
}

function linkFor(kind: string, id: string, sourceId?: string | null): string | null {
  if (kind === 'source') {
    const sha = id.replace(/^source:/, '');
    return sha ? `/library/${encodeURIComponent(sha)}` : null;
  }
  if (kind === 'claim') {
    const key = id.replace(/^claim:/, '');
    return key ? `/knowledge#${encodeURIComponent(key)}` : null;
  }
  if ((kind === 'card' || kind === 'unit') && sourceId) {
    return `/library/${encodeURIComponent(sourceId)}`;
  }
  return null;
}

function upsertNode(
  map: Map<string, ProcessNode>,
  partial: {
    kind: string;
    id: string;
    label: string;
    source_id?: string | null;
    cited?: boolean;
    hit?: boolean;
  },
) {
  const key = nodeKey(partial.kind, partial.id);
  const existing = map.get(key);
  if (existing) {
    if (partial.cited) existing.cited = true;
    if (partial.hit) existing.hit = true;
    // Prefer a human label over a bare id.
    if (
      partial.label &&
      (!existing.label ||
        existing.label === existing.id ||
        existing.label.startsWith('source '))
    ) {
      existing.label = partial.label;
    }
    return;
  }
  map.set(key, {
    id: key,
    kind: partial.kind,
    label: partial.label || key,
    cited: partial.cited,
    hit: partial.hit,
    link_target: linkFor(partial.kind, partial.id, partial.source_id),
  });
}

function addEdge(
  edges: ProcessEdge[],
  seen: Set<string>,
  source: string,
  target: string,
  type: ProcessEdge['type'],
) {
  if (source === target) return;
  const k = `${type}|${source}|${target}`;
  if (seen.has(k)) return;
  seen.add(k);
  edges.push({ source, target, type });
}

/** Fold progress hits + tool_trace + citations into graph data. */
export function buildProcessGraph(opts: {
  events?: AskProgressEvent[];
  toolTrace?: AskTraceEntry[];
  citations?: AskCitation[];
}): { nodes: ProcessNode[]; edges: ProcessEdge[] } {
  const nodes = new Map<string, ProcessNode>();
  const edges: ProcessEdge[] = [];
  const edgeSeen = new Set<string>();

  const ingestHit = (h: AskProgressHit) => {
    upsertNode(nodes, {
      kind: h.kind,
      id: h.id,
      label: h.label,
      source_id: h.source_id,
      hit: true,
    });
    if (h.source_id && h.kind !== 'source') {
      const srcKey = nodeKey('source', h.source_id);
      // Stub the source node if we only know its id — label filled later.
      if (!nodes.has(srcKey)) {
        upsertNode(nodes, {
          kind: 'source',
          id: h.source_id,
          label: `source ${h.source_id.slice(0, 12)}…`,
          source_id: h.source_id,
          hit: true,
        });
      }
      addEdge(
        edges,
        edgeSeen,
        nodeKey(h.kind, h.id),
        srcKey,
        h.kind === 'claim' ? 'cites' : 'from',
      );
    }
  };

  for (const ev of opts.events ?? []) {
    if (ev.event === 'tool_finished' && ev.hits) {
      for (const h of ev.hits) ingestHit(h);
    }
  }
  for (const t of opts.toolTrace ?? []) {
    for (const h of t.hits ?? []) ingestHit(h);
  }

  for (const c of opts.citations ?? []) {
    const kind = c.kind || (c.id.includes(':') ? c.id.split(':')[0] : 'claim');
    const rawId = c.id.includes(':') ? c.id.slice(c.id.indexOf(':') + 1) : c.id;
    const token = rawId.trim().split(/\s+/)[0]?.replace(/^<|>$/g, '') ?? rawId;
    upsertNode(nodes, {
      kind,
      id: kind === 'source' || kind === 'claim' ? token : c.id,
      label: c.title || c.id,
      cited: true,
      hit: true,
    });
  }

  return { nodes: Array.from(nodes.values()), edges };
}

export default function AskProcessGraph({
  events,
  toolTrace,
  citations,
  height = 220,
  hoverId,
  onOpen,
}: AskProcessGraphProps) {
  const { t } = useI18n();
  const navigate = useNavigate();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fgRef = useRef<any>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);

  const data = useMemo(
    () => buildProcessGraph({ events, toolTrace, citations }),
    [events, toolTrace, citations],
  );

  // Fresh objects so force-graph can own physics without mutating our memo.
  const graphData = useMemo(
    () => ({
      nodes: data.nodes.map((n) => ({ ...n })),
      links: data.edges.map((e) => ({ ...e })),
    }),
    [data],
  );

  const hoverKey = useMemo(() => {
    if (!hoverId) return null;
    // Citations use stable ids like source:<sha> / claim:<key>.
    return hoverId;
  }, [hoverId]);

  // Canvas wrapper is ALWAYS mounted (even with 0 nodes) so ResizeObserver
  // can measure width. Previously we early-returned an empty state without
  // the wrapper; the observer effect then bound to null on first paint and
  // never re-ran when History async-loaded tool_trace — legend showed
  // "53 sources · 33 memory" but ForceGraph stayed unmounted (width=0).
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => {
      const w = el.clientWidth;
      setWidth((prev) => (prev === w ? prev : w));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Nudge the camera when the graph first gains nodes or resizes.
  useEffect(() => {
    if (data.nodes.length === 0 || width <= 0) return;
    const id = window.setTimeout(() => {
      try {
        fgRef.current?.zoomToFit?.(400, 28);
      } catch {
        /* force-graph not mounted yet */
      }
    }, 180);
    return () => window.clearTimeout(id);
  }, [data.nodes.length, width]);

  const counts = data.nodes.reduce(
    (acc, n) => {
      acc[n.kind] = (acc[n.kind] ?? 0) + 1;
      return acc;
    },
    {} as Record<string, number>,
  );
  const empty = data.nodes.length === 0;

  return (
    <div className={`ask-process${empty ? ' empty' : ''}`}>
      {!empty && (
        <div className="ask-process-legend tiny muted">
          {counts.claim ? (
            <span>
              <i className="ask-process-swatch claim" />
              {t('ask.processClaims', { n: counts.claim })}
            </span>
          ) : null}
          {counts.source ? (
            <span>
              <i className="ask-process-swatch source" />
              {t('ask.processSources', { n: counts.source })}
            </span>
          ) : null}
          {(counts.card ?? 0) + (counts.unit ?? 0) > 0 ? (
            <span>
              <i className="ask-process-swatch card" />
              {t('ask.processMemory', {
                n: (counts.card ?? 0) + (counts.unit ?? 0),
              })}
            </span>
          ) : null}
        </div>
      )}
      <div className="ask-process-canvas" ref={wrapRef} style={{ height }}>
        {empty ? (
          <p className="tiny muted ask-process-empty-msg">{t('ask.processEmpty')}</p>
        ) : width > 0 ? (
          <Suspense
            fallback={<div className="tiny muted">{t('common.loading')}</div>}
          >
            <ForceGraph2D
              ref={fgRef}
              graphData={graphData}
              width={width}
              height={height}
              backgroundColor="transparent"
              nodeId="id"
              linkSource="source"
              linkTarget="target"
              cooldownTicks={80}
              enableNodeDrag
              enableZoomInteraction
              enablePanInteraction
              nodeRelSize={5}
              linkColor={() =>
                readToken('--graph-link', 'rgba(128,128,128,0.35)')
              }
              linkWidth={(l: ProcessEdge) => (l.type === 'cites' ? 1.6 : 1)}
              linkDirectionalArrowLength={3}
              linkDirectionalArrowRelPos={1}
              nodeCanvasObject={(
                node: ProcessNode,
                ctx: CanvasRenderingContext2D,
                globalScale: number,
              ) => {
                const r = node.cited ? 6.5 : node.hit ? 5.5 : 4.5;
                const x = node.x ?? 0;
                const y = node.y ?? 0;
                const fill = kindFill(node.kind);
                const isHover =
                  hoverKey != null &&
                  (node.id === hoverKey ||
                    node.id.endsWith(hoverKey) ||
                    hoverKey.endsWith(
                      node.id.replace(/^(source|claim):/, ''),
                    ));

                ctx.beginPath();
                ctx.arc(x, y, r, 0, 2 * Math.PI);
                ctx.fillStyle = fill;
                ctx.globalAlpha = isHover ? 1 : 0.92;
                ctx.fill();
                ctx.globalAlpha = 1;

                if (node.cited || isHover) {
                  ctx.strokeStyle = readToken('--accent', '#38bdf8');
                  ctx.lineWidth = isHover ? 2 : 1.4;
                  ctx.stroke();
                }

                // Dense graphs (50+ search hits): label cited hubs + hover;
                // label more aggressively when the set is small.
                const showLabel =
                  globalScale > 1.2 ||
                  node.cited ||
                  isHover ||
                  data.nodes.length <= 12;
                if (showLabel) {
                  const label =
                    node.label.length > 28
                      ? `${node.label.slice(0, 27)}…`
                      : node.label;
                  const fontSize = Math.max(9, 11 / Math.sqrt(globalScale));
                  ctx.font = `${fontSize}px ${readToken('--ovp-font-sans', 'system-ui')}`;
                  ctx.textAlign = 'center';
                  ctx.textBaseline = 'top';
                  ctx.fillStyle = readToken('--text', '#e5e7eb');
                  ctx.globalAlpha = 0.9;
                  ctx.fillText(label, x, y + r + 2);
                  ctx.globalAlpha = 1;
                }
              }}
              nodePointerAreaPaint={(
                node: ProcessNode,
                color: string,
                ctx: CanvasRenderingContext2D,
              ) => {
                ctx.beginPath();
                ctx.arc(node.x ?? 0, node.y ?? 0, 10, 0, 2 * Math.PI);
                ctx.fillStyle = color;
                ctx.fill();
              }}
              onNodeClick={(node: ProcessNode) => {
                if (onOpen) {
                  onOpen(node);
                  return;
                }
                if (node.link_target) navigate(node.link_target);
              }}
            />
          </Suspense>
        ) : null}
      </div>
    </div>
  );
}
