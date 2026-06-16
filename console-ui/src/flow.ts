import { sankey, sankeyLinkHorizontal, SankeyNode, SankeyLink } from 'd3-sankey';
import { select } from 'd3-selection';
import { scaleOrdinal } from 'd3-scale';
import { fetchFlow } from './shared/api';
import type { FlowData } from './shared/types';

const STAGE_COLORS = [
  '#a855f7', '#3b82f6', '#22c55e', '#f59e0b', '#ef4444', '#06b6d4',
];

interface SNode {
  name: string;
  index?: number;
}

interface SLink {
  source: number;
  target: number;
  value: number;
  label: string;
}

async function init() {
  const container = document.getElementById('sankey-container')!;
  const stats = document.getElementById('flow-stats')!;

  let data: FlowData;
  try {
    data = await fetchFlow();
  } catch (err) {
    container.innerHTML = `<p style="padding:2rem;color:#94a3b8">Failed to load flow data. Ensure OVP server is running.</p>`;
    return;
  }

  const nodes: SNode[] = data.stages.map(name => ({ name }));
  const stageIndex = new Map(data.stages.map((s, i) => [s, i]));

  const links: SLink[] = data.flows
    .filter(f => stageIndex.has(f.from) && stageIndex.has(f.to))
    .map(f => ({
      source: stageIndex.get(f.from)!,
      target: stageIndex.get(f.to)!,
      value: f.value,
      label: f.label,
    }));

  const width = container.clientWidth || 900;
  const height = container.clientHeight || 500;

  const colorScale = scaleOrdinal<string>().domain(data.stages).range(STAGE_COLORS);

  const sankeyGen = sankey<SNode, SLink>()
    .nodeWidth(20)
    .nodePadding(24)
    .extent([[40, 40], [width - 40, height - 40]]);

  const { nodes: sNodes, links: sLinks } = sankeyGen({ nodes, links });

  const svg = select(container).append('svg')
    .attr('viewBox', `0 0 ${width} ${height}`)
    .attr('preserveAspectRatio', 'xMidYMid meet');

  svg.append('g').selectAll('path')
    .data(sLinks)
    .join('path')
    .attr('d', sankeyLinkHorizontal())
    .attr('fill', 'none')
    .attr('stroke', (d: SankeyLink<SNode, SLink>) => {
      const src = d.source as SankeyNode<SNode, SLink>;
      return colorScale(src.name || '');
    })
    .attr('stroke-opacity', 0.4)
    .attr('stroke-width', (d: SankeyLink<SNode, SLink>) => Math.max(2, d.width || 0))
    .append('title')
    .text((d: SankeyLink<SNode, SLink>) => {
      const src = d.source as SankeyNode<SNode, SLink>;
      const tgt = d.target as SankeyNode<SNode, SLink>;
      return `${src.name} → ${tgt.name}: ${d.value}`;
    });

  svg.append('g').selectAll('rect')
    .data(sNodes)
    .join('rect')
    .attr('x', (d: SankeyNode<SNode, SLink>) => d.x0 || 0)
    .attr('y', (d: SankeyNode<SNode, SLink>) => d.y0 || 0)
    .attr('width', (d: SankeyNode<SNode, SLink>) => (d.x1 || 0) - (d.x0 || 0))
    .attr('height', (d: SankeyNode<SNode, SLink>) => (d.y1 || 0) - (d.y0 || 0))
    .attr('fill', (d: SankeyNode<SNode, SLink>) => colorScale(d.name || ''))
    .attr('rx', 4)
    .attr('opacity', 0.9);

  svg.append('g').selectAll('text')
    .data(sNodes)
    .join('text')
    .attr('x', (d: SankeyNode<SNode, SLink>) => (d.x0 || 0) < width / 2 ? (d.x1 || 0) + 8 : (d.x0 || 0) - 8)
    .attr('y', (d: SankeyNode<SNode, SLink>) => ((d.y0 || 0) + (d.y1 || 0)) / 2)
    .attr('dy', '0.35em')
    .attr('text-anchor', (d: SankeyNode<SNode, SLink>) => (d.x0 || 0) < width / 2 ? 'start' : 'end')
    .attr('fill', '#e2e8f0')
    .attr('font-size', '13px')
    .text((d: SankeyNode<SNode, SLink>) => d.name || '');

  const totalFlow = links.reduce((s, l) => s + l.value, 0);
  stats.innerHTML = `<span>Total flow: ${totalFlow} items</span><span>Stages: ${data.stages.length}</span>`;
}

init();
