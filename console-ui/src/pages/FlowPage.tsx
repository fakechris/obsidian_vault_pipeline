import { useEffect, useRef, useState } from 'react';
import {
  sankey,
  sankeyLinkHorizontal,
  type SankeyNode,
  type SankeyLink,
} from 'd3-sankey';
import { fetchFlow } from '../lib/api';
import type { FlowData } from '../lib/types';

const STAGE_COLORS = [
  '#a855f7', '#3b82f6', '#22c55e', '#f59e0b', '#ef4444', '#06b6d4', '#94a3b8',
];

interface SNode {
  name: string;
}

interface SLink {
  source: number;
  target: number;
  value: number;
  label: string;
}

type LaidNode = SankeyNode<SNode, SLink>;
type LaidLink = SankeyLink<SNode, SLink>;

// d3-sankey is a pure layout computation — React renders the SVG directly,
// no d3-selection DOM manipulation needed.
export default function FlowPage() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<FlowData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState({ width: 900, height: 500 });

  useEffect(() => {
    fetchFlow().then(setData, (e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => {
      setSize({
        width: el.clientWidth || 900,
        height: el.clientHeight || 500,
      });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  let laidNodes: LaidNode[] = [];
  let laidLinks: LaidLink[] = [];
  let totalFlow = 0;
  if (data) {
    const stageIndex = new Map(data.stages.map((s, i) => [s, i]));
    const nodes: SNode[] = data.stages.map((name) => ({ name }));
    const links: SLink[] = data.flows
      .filter((f) => stageIndex.has(f.from) && stageIndex.has(f.to))
      .map((f) => ({
        source: stageIndex.get(f.from)!,
        target: stageIndex.get(f.to)!,
        value: f.value,
        label: f.label,
      }));
    totalFlow = links.reduce((s, l) => s + l.value, 0);
    if (totalFlow > 0) {
      const generator = sankey<SNode, SLink>()
        .nodeWidth(20)
        .nodePadding(24)
        .extent([
          [40, 40],
          [size.width - 40, size.height - 40],
        ]);
      const laid = generator({
        nodes: nodes.map((n) => ({ ...n })),
        links: links.map((l) => ({ ...l })),
      });
      laidNodes = laid.nodes;
      laidLinks = laid.links;
    }
  }

  const colorOf = (name: string) =>
    STAGE_COLORS[(data?.stages.indexOf(name) ?? 0) % STAGE_COLORS.length];
  const linkPath = sankeyLinkHorizontal<SNode, SLink>();

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-6 border-b border-border-soft px-6 py-3 text-sm text-slate-400">
        <span className="font-semibold text-slate-200">
          Pipeline Flow 数据流
        </span>
        {data && (
          <>
            <span>Total flow 总量: {totalFlow}</span>
            <span>Stages 阶段: {data.stages.length}</span>
          </>
        )}
      </div>
      <div ref={containerRef} className="relative min-h-0 flex-1">
        {error && (
          <p className="p-8 text-sm text-slate-500">
            Failed to load flow data 加载失败: {error}
          </p>
        )}
        {data && totalFlow === 0 && (
          <p className="p-8 text-sm text-slate-500">No flow yet 暂无数据。</p>
        )}
        {laidNodes.length > 0 && (
          <svg
            viewBox={`0 0 ${size.width} ${size.height}`}
            preserveAspectRatio="xMidYMid meet"
            className="h-full w-full"
          >
            <g>
              {laidLinks.map((l, i) => {
                const src = l.source as LaidNode;
                const tgt = l.target as LaidNode;
                return (
                  <path
                    key={i}
                    d={linkPath(l) ?? undefined}
                    fill="none"
                    stroke={colorOf(src.name)}
                    strokeOpacity={0.4}
                    strokeWidth={Math.max(2, l.width ?? 0)}
                  >
                    <title>{`${src.name} → ${tgt.name}: ${l.value}`}</title>
                  </path>
                );
              })}
            </g>
            <g>
              {laidNodes.map((n) => (
                <rect
                  key={n.name}
                  x={n.x0 ?? 0}
                  y={n.y0 ?? 0}
                  width={(n.x1 ?? 0) - (n.x0 ?? 0)}
                  height={(n.y1 ?? 0) - (n.y0 ?? 0)}
                  fill={colorOf(n.name)}
                  rx={4}
                  opacity={0.9}
                />
              ))}
            </g>
            <g>
              {laidNodes.map((n) => (
                <text
                  key={n.name}
                  x={(n.x0 ?? 0) < size.width / 2 ? (n.x1 ?? 0) + 8 : (n.x0 ?? 0) - 8}
                  y={((n.y0 ?? 0) + (n.y1 ?? 0)) / 2}
                  dy="0.35em"
                  textAnchor={(n.x0 ?? 0) < size.width / 2 ? 'start' : 'end'}
                  fill="#e2e8f0"
                  fontSize={13}
                >
                  {n.name}
                </text>
              ))}
            </g>
          </svg>
        )}
      </div>
    </div>
  );
}
