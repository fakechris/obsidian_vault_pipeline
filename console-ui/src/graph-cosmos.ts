// GPU-accelerated graph view for large graphs (thousands → tens of thousands
// of nodes). Uses @cosmograph/cosmos: the force simulation runs on the GPU and
// rendering is WebGL, so it stays smooth where the Cytoscape layout chokes.
// Cytoscape (graph2d) remains the engine for small/medium graphs because it
// gives crisp always-on labels and richer provenance styling.

import { Graph } from '@cosmograph/cosmos';
import { fetchClaim } from './shared/api';
import type { GraphData, ClaimDetail } from './shared/types';

interface CNode {
  id: string;
  type: string;
  label: string;
  degree: number;
  cluster: number;
  theme?: string;
  url?: string;
}
interface CLink {
  source: string;
  target: string;
  type: string;
}

let graph: Graph<CNode, CLink> | null = null;
let cNodes: CNode[] = [];
let byId = new Map<string, CNode>();
let colorMode: 'type' | 'cluster' = 'type';
let controlsWired = false;

// ── DOM ──
const cosmosContainer = document.getElementById('cosmos-container')!;
const detailPanel = document.getElementById('detail-panel')!;
const detailContent = document.getElementById('detail-content')!;
const closeBtn = document.getElementById('close-detail')!;
const searchInput = document.getElementById('search-input') as HTMLInputElement;
const statsEl = document.getElementById('graph-stats');
const colorModeBtn = document.getElementById('color-mode') as HTMLButtonElement | null;
const askPanel = document.getElementById('ask-panel');
const askToggle = document.getElementById('toggle-ask') as HTMLButtonElement | null;
const askInput = document.getElementById('ask-input') as HTMLInputElement | null;
const askResults = document.getElementById('ask-results');

let tooltip: HTMLElement | null = null;

// ── Colors (return rgba() strings — universally parseable by the lib) ──

const TYPE_RGB: Record<string, string> = {
  claim: '196,181,253',
  unit: '147,197,253',
  source: '134,239,172',
};
const CLUSTER_HUES = [265, 210, 145, 35, 0, 320, 175, 50, 240, 110, 300, 20, 90, 190, 330];

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  s /= 100; l /= 100;
  const k = (n: number) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return [Math.round(f(0) * 255), Math.round(f(8) * 255), Math.round(f(4) * 255)];
}

function nodeColor(n: CNode): string {
  if (colorMode === 'cluster') {
    if (!n.cluster) return 'rgba(100,116,139,0.9)';
    const [r, g, b] = hslToRgb(CLUSTER_HUES[(n.cluster - 1) % CLUSTER_HUES.length], 62, 68);
    return `rgba(${r},${g},${b},0.95)`;
  }
  return `rgba(${TYPE_RGB[n.type] || '148,163,184'},0.92)`;
}

function linkColor(l: CLink): string {
  if (l.type === 'related') return 'rgba(251,191,36,0.30)';
  if (l.type === 'cites') return 'rgba(167,139,250,0.22)';
  return 'rgba(63,77,99,0.18)';
}

function nodeSize(n: CNode): number {
  if (n.type === 'claim') return 3 + Math.sqrt(n.degree) * 1.1;
  if (n.type === 'source') return 2.4 + Math.sqrt(n.degree) * 0.6;
  return 1.4;
}

// ── Init ──

export async function initCosmos(data: GraphData) {
  cNodes = data.nodes.map(n => ({
    id: n.id,
    type: n.type,
    label: n.label,
    degree: n.degree || 0,
    cluster: n.cluster || 0,
    theme: n.theme,
    url: n.url,
  }));
  byId = new Map(cNodes.map(n => [n.id, n]));
  const links: CLink[] = data.edges.map(e => ({ source: e.source, target: e.target, type: e.type }));

  cosmosContainer.innerHTML = '';
  const canvas = document.createElement('canvas');
  canvas.style.width = '100%';
  canvas.style.height = '100%';
  cosmosContainer.appendChild(canvas);

  graph = new Graph<CNode, CLink>(canvas, {
    backgroundColor: '#0f1117',
    spaceSize: 8192,
    nodeColor,
    nodeSize,
    nodeSizeScale: 1,
    scaleNodesOnZoom: true,
    renderLinks: true,
    linkColor,
    linkWidth: (l: CLink) => (l.type === 'related' ? 1.1 : 0.5),
    linkArrows: false,
    linkVisibilityDistanceRange: [60, 1600],
    linkVisibilityMinTransparency: 0.1,
    renderHoveredNodeRing: true,
    hoveredNodeRingColor: '#fbbf24',
    simulation: {
      // Tuned for a spread, "breathing" layout that cools to a stop.
      decay: 30000,
      repulsion: 1.2,
      repulsionTheta: 1.15,
      gravity: 0.25,
      center: 0.0,
      linkSpring: 1.0,
      linkDistance: 10,
      friction: 0.86,
    },
    events: {
      onClick: (node?: CNode) => {
        if (node) onNodeClick(node);
        else clearSelection();
      },
      onNodeMouseOver: (node: CNode, _i: number, pos: [number, number]) => showTooltip(node, pos),
      onNodeMouseOut: () => hideTooltip(),
    },
  } as any);

  graph.setData(cNodes, links);
  // Fit once the first ticks have spread things out.
  setTimeout(() => graph && graph.fitView(800), 1500);

  updateStats(data);
  wireControls();
}

export function destroyCosmos() {
  hideTooltip();
  if (graph) {
    graph.destroy();
    graph = null;
  }
  cosmosContainer.innerHTML = '';
}

// ── Interactions ──

async function onNodeClick(node: CNode) {
  graph?.zoomToNodeById(node.id, 600, 5);
  graph?.selectNodeById(node.id, true);
  if (node.type === 'claim') await showClaimDetail(node.id);
  else showNodeInfo(node);
}

function clearSelection() {
  graph?.unselectNodes();
  detailPanel.classList.add('hidden');
}

function showTooltip(node: CNode, spacePos: [number, number]) {
  if (!tooltip) {
    tooltip = document.createElement('div');
    tooltip.id = 'cosmos-tooltip';
    document.body.appendChild(tooltip);
  }
  const kind = node.type.toUpperCase();
  tooltip.innerHTML = `<span class="tt-kind tt-${node.type}">${kind}</span> ${escapeHtml(node.label)}`;
  tooltip.style.display = 'block';
  const screen = graph?.spaceToScreenPosition(spacePos);
  if (screen) {
    tooltip.style.left = `${screen[0] + 12}px`;
    tooltip.style.top = `${screen[1] + 12}px`;
  }
}

function hideTooltip() {
  if (tooltip) tooltip.style.display = 'none';
}

// ── Controls (shared DOM with the Cytoscape view) ──

function wireControls() {
  if (controlsWired) return;
  controlsWired = true;

  if (colorModeBtn) {
    colorModeBtn.addEventListener('click', () => {
      colorMode = colorMode === 'type' ? 'cluster' : 'type';
      colorModeBtn!.textContent = colorMode === 'cluster' ? 'Color: Cluster' : 'Color: Type';
      colorModeBtn!.classList.toggle('active', colorMode === 'cluster');
      graph?.setConfig({ nodeColor });
    });
  }

  if (searchInput) {
    let st: ReturnType<typeof setTimeout>;
    searchInput.addEventListener('input', () => {
      clearTimeout(st);
      st = setTimeout(() => {
        const q = searchInput.value.toLowerCase().trim();
        if (!q) { graph?.unselectNodes(); return; }
        const ids = cNodes
          .filter(n => n.label.toLowerCase().includes(q) || (n.theme || '').toLowerCase().includes(q))
          .map(n => n.id);
        if (ids.length) graph?.selectNodesByIds(ids);
        else graph?.unselectNodes();
      }, 200);
    });
  }

  if (askToggle && askPanel) {
    askToggle.addEventListener('click', () => {
      const open = askPanel!.classList.toggle('open');
      askToggle!.classList.toggle('active', open);
      if (open && askInput) askInput.focus();
    });
  }
  if (askInput && askResults) {
    let at: ReturnType<typeof setTimeout>;
    askInput.addEventListener('input', () => {
      clearTimeout(at);
      at = setTimeout(runAsk, 200);
    });
  }
  closeBtn.addEventListener('click', () => detailPanel.classList.add('hidden'));
}

function runAsk() {
  if (!askResults) return;
  const q = (askInput?.value || '').toLowerCase().trim();
  if (!q) {
    askResults.innerHTML = '<p class="ask-empty">Search claims, evidence quotes, and sources.</p>';
    graph?.unselectNodes();
    return;
  }
  const order: Record<string, number> = { claim: 0, source: 1, unit: 2 };
  const hits = cNodes
    .filter(n => n.label.toLowerCase().includes(q) || (n.theme || '').toLowerCase().includes(q))
    .sort((a, b) => (order[a.type] - order[b.type]) || (b.degree - a.degree))
    .slice(0, 40);

  if (hits.length) graph?.selectNodesByIds(hits.map(h => h.id));
  else graph?.unselectNodes();

  if (hits.length === 0) {
    askResults.innerHTML = '<p class="ask-empty">No matches.</p>';
    return;
  }
  askResults.innerHTML = hits
    .map(h => `<div class="ask-item" data-id="${h.id}"><span class="ask-kind ask-kind-${h.type}">${h.type}</span><span class="ask-label">${escapeHtml(h.label)}</span></div>`)
    .join('');
  askResults.querySelectorAll('.ask-item').forEach(el => {
    el.addEventListener('click', () => {
      const id = el.getAttribute('data-id')!;
      const n = byId.get(id);
      if (n) onNodeClick(n);
    });
  });
}

// ── Detail panel (mirrors the Cytoscape view's panel) ──

async function showClaimDetail(id: string) {
  try {
    const detail: ClaimDetail = await fetchClaim(id);
    detailContent.innerHTML = `
      <div class="detail-type" style="color:#c4b5fd">◆ CLAIM</div>
      <h3>${escapeHtml(detail.claim)}</h3>
      <div class="meta">
        <span class="tag theme-tag">${escapeHtml(detail.theme)}</span>
        <span class="tag strength-tag">${escapeHtml(detail.strength)}</span>
      </div>
      <div class="citation-list">
        <div class="section-label">Citations (${detail.citations.length})</div>
        ${detail.citations.map(c => `
          <div class="cit-entry">
            <div class="quote">"${escapeHtml(c.quote)}"</div>
            <div class="ref">
              <span style="color:#86efac">● ${escapeHtml(c.source_title)}</span>
              <span class="line-ref">${c.resolved_line ? `L${c.resolved_line}` : ''}</span>
            </div>
          </div>`).join('')}
      </div>`;
  } catch {
    detailContent.innerHTML = `<h3>Claim ${id.slice(0, 12)}…</h3><p class="error-msg">Failed to load details.</p>`;
  }
  detailPanel.classList.remove('hidden');
}

function showNodeInfo(n: CNode) {
  const icon = n.type === 'unit' ? '■' : '●';
  const color = n.type === 'source' ? '#86efac' : '#93c5fd';
  const extra = n.url ? `<a href="${n.url}" target="_blank" class="source-link">Open source ↗</a>` : '';
  detailContent.innerHTML = `
    <div class="detail-type" style="color:${color}">${icon} ${n.type.toUpperCase()}</div>
    <h3>${escapeHtml(n.label)}</h3>
    <div class="meta">
      <span class="tag">${n.degree} connections</span>
      ${n.cluster ? `<span class="tag">cluster ${n.cluster}</span>` : ''}
    </div>
    ${extra}`;
  detailPanel.classList.remove('hidden');
}

function updateStats(data: GraphData) {
  if (!statsEl) return;
  const c = data.nodes.filter(n => n.type === 'claim').length;
  const u = data.nodes.filter(n => n.type === 'unit').length;
  const s = data.nodes.filter(n => n.type === 'source').length;
  statsEl.innerHTML = `
    <span><span class="dot claim-dot"></span>${c} claims</span>
    <span><span class="dot unit-dot"></span>${u} units</span>
    <span><span class="dot source-dot"></span>${s} sources</span>
    <span style="color:#64748b">${data.edges.length} edges · GPU</span>`;
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] || c));
}
