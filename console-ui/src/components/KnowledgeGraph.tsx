/** KnowledgeGraph — the scoped graph component (design §4, KMEM pattern).
 * One component, three scopes, one rendering engine:
 *
 *   scope='neighborhood' id=<source sha>  → this source, citing claims,
 *                                           sibling sources (B2, live)
 *   scope='global'                        → knowledge-page graph view (B3)
 *   scope='theme'        id=<theme>       → theme detail rail (B3)
 *
 * All colors are read from the DS custom properties (--graph-*, --c-*,
 * --accent, --text…) at render time, and the graph re-renders when
 * `data-theme` flips (MutationObserver on <html>). Interactions: click →
 * in-component info card; double-click → navigate (source → /library/:sha,
 * claim → /knowledge#<claim_id> placeholder anchor until B3). Embedded
 * height defaults to ~360px with an expand-to-fullscreen toggle.
 *
 * @antv/g6 (~1MB min) loads via dynamic import so portal pages stay light —
 * same policy as the lazy legacy /graph route. */
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useI18n } from '../i18n';
import { fetchSourceNeighborhood } from '../lib/api';
import type { GraphNode, GraphResponse } from '../lib/types';
import { EmptyState } from './ui';

export type KnowledgeGraphScope = 'neighborhood' | 'global' | 'theme';

export interface KnowledgeGraphProps {
  scope: KnowledgeGraphScope;
  /** neighborhood: source sha256 · theme: theme name · global: unused. */
  id?: string;
  /** Embedded height in px (default 360). */
  height?: number;
}

const DEFAULT_HEIGHT = 360;

/** DS tokens resolved from the live theme — read at render time so the
 * graph always matches `data-theme`. */
interface DsTokens {
  link: string;
  linkHi: string;
  text: string;
  muted: string;
  surface: string;
  accent: string;
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
    community: [1, 2, 3, 4, 5, 6, 7, 8].map((n) => v(`--c-${n}`)),
  };
}

/** Node fill by entity kind (mockup: sources --c-1, claims --c-3, units
 * --c-2 — the community palette read for the ACTIVE theme). */
function nodeFill(type: string, t: DsTokens): string {
  if (type === 'source') return t.community[0];
  if (type === 'claim') return t.community[2];
  return t.community[1];
}

function nodeSize(n: GraphNode, isFocus: boolean): number {
  if (isFocus) return 22;
  if (n.type === 'source') return 12 + 8 * (n.importance ?? 0);
  return 10 + 12 * (n.importance ?? 0);
}

export default function KnowledgeGraph({
  scope,
  id,
  height = DEFAULT_HEIGHT,
}: KnowledgeGraphProps) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const containerRef = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<GraphResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [fullscreen, setFullscreen] = useState(false);
  // Bumped when <html data-theme> mutates → graph rebuilds with new tokens.
  const [themeVersion, setThemeVersion] = useState(0);

  useEffect(() => {
    const observer = new MutationObserver(() => setThemeVersion((v) => v + 1));
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (scope !== 'neighborhood' || !id) return;
    let cancelled = false;
    setData(null);
    setError(null);
    fetchSourceNeighborhood(id)
      .then((resp) => {
        if (!cancelled) setData(resp);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [scope, id]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !data || data.nodes.length === 0) return;

    let destroyed = false;
    let cleanup: (() => void) | undefined;

    void import('@antv/g6').then(({ Graph, NodeEvent, CanvasEvent }) => {
      if (destroyed || !containerRef.current) return;
      const tokens = readTokens();
      const focusId = id ? `source:${id}` : null;
      const nodeById = new Map(data.nodes.map((n) => [n.id, n]));

      const graph = new Graph({
        container,
        animation: false,
        autoResize: true,
        padding: 16,
        autoFit: 'view',
        data: {
          nodes: data.nodes.map((n) => ({
            id: n.id,
            data: n as unknown as Record<string, unknown>,
          })),
          edges: data.edges.map((e, i) => ({
            id: `e${i}`,
            source: e.source,
            target: e.target,
            data: { type: e.type },
          })),
        },
        node: {
          style: {
            size: (d: { id?: string }) => {
              const n = nodeById.get(d.id ?? '');
              return n ? nodeSize(n, n.id === focusId) : 10;
            },
            fill: (d: { id?: string }) =>
              nodeFill(nodeById.get(d.id ?? '')?.type ?? '', tokens),
            fillOpacity: 0.92,
            lineWidth: (d: { id?: string }) => (d.id === focusId ? 1.5 : 0),
            stroke: tokens.accent,
            labelText: (d: { id?: string }) =>
              nodeById.get(d.id ?? '')?.label ?? '',
            labelFill: tokens.text,
            labelFontSize: 10,
            labelFontFamily:
              "'IBM Plex Sans', 'IBM Plex Sans SC', system-ui, sans-serif",
            labelBackground: true,
            labelBackgroundFill: tokens.surface,
            labelBackgroundOpacity: 0.85,
            labelBackgroundRadius: 4,
            labelPadding: [1, 4],
            labelPlacement: 'bottom',
            labelMaxWidth: 130,
            labelWordWrap: true,
            labelMaxLines: 2,
          },
          state: {
            selected: {
              stroke: tokens.linkHi,
              lineWidth: 2,
            },
          },
        },
        edge: {
          style: {
            stroke: tokens.link,
            lineWidth: 1,
            strokeOpacity: 0.9,
          },
        },
        layout: {
          type: 'd3-force',
          link: { distance: 110, strength: 0.7 },
          collide: { radius: 48, strength: 1.1 },
          manyBody: { strength: -300 },
          velocityDecay: 0.68,
          alphaDecay: 0.04,
        },
        behaviors: ['zoom-canvas', 'drag-canvas', 'drag-element'],
      });

      let lastSelected: string | null = null;
      const targetId = (evt: unknown): string =>
        (evt as { target: { id: string } }).target.id;

      graph.on(NodeEvent.CLICK, (evt: unknown) => {
        const nodeId = targetId(evt);
        if (lastSelected && lastSelected !== nodeId) {
          graph.setElementState(lastSelected, []).catch(() => {});
        }
        graph.setElementState(nodeId, ['selected']).catch(() => {});
        lastSelected = nodeId;
        setSelected(nodeById.get(nodeId) ?? null);
      });
      graph.on(CanvasEvent.CLICK, () => {
        if (lastSelected) {
          graph.setElementState(lastSelected, []).catch(() => {});
          lastSelected = null;
        }
        setSelected(null);
      });
      graph.on(NodeEvent.DBLCLICK, (evt: unknown) => {
        const nodeId = targetId(evt);
        if (nodeId.startsWith('source:')) {
          navigate(`/library/${nodeId.slice('source:'.length)}`);
        } else if (nodeId.startsWith('claim:')) {
          // Knowledge page is a B3 placeholder; the anchor carries the claim.
          navigate(`/knowledge#${nodeId.slice('claim:'.length)}`);
        }
      });

      graph.render().catch((err: unknown) => {
        // Destroyed mid-render (StrictMode double-mount) is expected noise.
        if (!graph.destroyed) console.error('knowledge graph render failed', err);
      });

      cleanup = () => graph.destroy();
    });

    return () => {
      destroyed = true;
      cleanup?.();
    };
    // themeVersion intentionally re-runs this effect: same data, new tokens.
    // scope must be a dep: flipping away from 'neighborhood' unmounts the
    // container, and only a re-run destroys the old graph instance.
  }, [data, id, navigate, scope, themeVersion]);

  if (scope !== 'neighborhood') {
    return (
      <EmptyState>
        <p>{t('graph.b3')}</p>
      </EmptyState>
    );
  }

  const kindLabel = (type: string) =>
    type === 'claim'
      ? t('graph.kindClaim')
      : type === 'source'
        ? t('graph.kindSource')
        : t('graph.kindUnit');

  return (
    <div
      className={`graph-embed${fullscreen ? ' fullscreen' : ''}`}
      style={fullscreen ? undefined : { height }}
    >
      {error && (
        <EmptyState>
          <p>{t('graph.error')}</p>
        </EmptyState>
      )}
      {!error && data && data.nodes.length === 0 && (
        <EmptyState>
          <p>{t('graph.empty')}</p>
        </EmptyState>
      )}
      {!error && !data && <div className="graph-note">{t('graph.loading')}</div>}
      {!error && data && data.nodes.length > 0 && (
        <>
          <div ref={containerRef} className="graph-canvas" />
          <button
            type="button"
            className="graph-expand"
            onClick={() => setFullscreen((f) => !f)}
          >
            {fullscreen ? t('graph.exitFullscreen') : t('graph.fullscreen')}
          </button>
          {data.truncated && (
            <div className="graph-note graph-truncated">
              {t('graph.truncated')}
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
                <div className="tiny muted">{selected.theme}</div>
              )}
              <div className="tiny muted">{t('graph.openHint')}</div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
