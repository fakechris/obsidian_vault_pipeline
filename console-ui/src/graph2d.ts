import cytoscape from 'cytoscape';
// @ts-ignore — no types available for cytoscape-fcose
import fcose from 'cytoscape-fcose';
import { fetchGraph, fetchClaim } from './shared/api';
import { COLORS, nodeColor } from './shared/theme';
import type { GraphNode, GraphEdge, ClaimDetail } from './shared/types';

cytoscape.use(fcose);

// ── State ──

let cy: cytoscape.Core | null = null;
let allNodes: GraphNode[] = [];
let allEdges: GraphEdge[] = [];

// ── DOM refs (shared with graph-main) ──

const container = document.getElementById('cy-container')!;
const detailPanel = document.getElementById('detail-panel')!;
const detailContent = document.getElementById('detail-content')!;
const closeBtn = document.getElementById('close-detail')!;
const themeFilter = document.getElementById('theme-filter') as HTMLSelectElement;
const typeFilter = document.getElementById('type-filter') as HTMLSelectElement;
const searchInput = document.getElementById('search-input') as HTMLInputElement;
const statsEl = document.getElementById('graph-stats');

// ── Cytoscape style definitions ──

const CY_STYLE: cytoscape.StylesheetStyle[] = [
  {
    selector: 'node',
    style: {
      'label': 'data(label)',
      'text-valign': 'center',
      'text-halign': 'center',
      'font-family': 'Inter, Noto Sans SC, system-ui, sans-serif',
      'font-size': '11px',
      'font-weight': 500,
      'color': '#e2e8f0',
      'text-outline-color': '#0f1117',
      'text-outline-width': 2,
      'text-max-width': '120px',
      'text-wrap': 'ellipsis',
      'background-opacity': 0.85,
      'border-width': 2,
      'overlay-padding': '4px',
      'transition-property': 'opacity, background-color, border-color, border-width',
      'transition-duration': 200,
      'min-zoomed-font-size': 8,
    } as any,
  },
  {
    selector: 'node[type="claim"]',
    style: {
      'background-color': '#c4b5fd',
      'border-color': '#8b5cf6',
      'font-size': '13px',
      'font-weight': 600,
      'color': '#faf5ff',
      'width': 'mapData(degree, 0, 10, 40, 80)',
      'height': 'mapData(degree, 0, 10, 40, 80)',
      'text-outline-color': '#2e1065',
      'text-outline-width': 2.5,
    } as any,
  },
  {
    selector: 'node[type="unit"]',
    style: {
      'background-color': '#93c5fd',
      'border-color': '#3b82f6',
      'font-size': '11px',
      'color': '#eff6ff',
      'width': 'mapData(degree, 0, 10, 28, 60)',
      'height': 'mapData(degree, 0, 10, 28, 60)',
      'text-outline-color': '#1e3a5f',
    } as any,
  },
  {
    selector: 'node[type="source"]',
    style: {
      'background-color': '#86efac',
      'border-color': '#22c55e',
      'font-size': '10px',
      'color': '#f0fdf4',
      'width': 'mapData(degree, 0, 10, 24, 50)',
      'height': 'mapData(degree, 0, 10, 24, 50)',
      'text-outline-color': '#14532d',
    } as any,
  },
  {
    selector: 'edge',
    style: {
      'width': 1.5,
      'line-color': '#334155',
      'target-arrow-color': '#334155',
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.8,
      'curve-style': 'bezier',
      'opacity': 0.5,
      'transition-property': 'opacity, line-color, width',
      'transition-duration': 200,
    } as any,
  },
  {
    selector: 'edge[type="cites"]',
    style: {
      'line-color': '#7c3aed',
      'target-arrow-color': '#7c3aed',
      'width': 2.5,
      'opacity': 0.6,
    } as any,
  },
  {
    selector: 'edge[type="extracted_from"]',
    style: {
      'line-color': '#0891b2',
      'target-arrow-color': '#0891b2',
      'width': 1.5,
      'opacity': 0.45,
    } as any,
  },
  // Hover/highlight states
  {
    selector: 'node.highlighted',
    style: {
      'border-width': 4,
      'background-opacity': 1,
      'z-index': 10,
    } as any,
  },
  {
    selector: 'node.neighbor',
    style: {
      'border-width': 3,
      'background-opacity': 0.95,
      'z-index': 9,
    } as any,
  },
  {
    selector: 'node.faded',
    style: {
      'opacity': 0.08,
    } as any,
  },
  {
    selector: 'edge.highlighted',
    style: {
      'opacity': 0.9,
      'width': 3,
      'z-index': 10,
    } as any,
  },
  {
    selector: 'edge.faded',
    style: {
      'opacity': 0.04,
    } as any,
  },
  {
    selector: 'node.search-match',
    style: {
      'border-width': 4,
      'border-color': '#fbbf24',
      'background-opacity': 1,
      'z-index': 20,
    } as any,
  },
];

// ── Initialization ──

export async function init2D() {
  try {
    const data = await fetchGraph();
    allNodes = data.nodes;
    allEdges = data.edges;

    // Compute degree
    allNodes.forEach(n => {
      n.degree = allEdges.filter(e => e.source === n.id || e.target === n.id).length;
    });

    // Convert to Cytoscape elements
    const elements: cytoscape.ElementDefinition[] = [
      ...allNodes.map(n => ({
        data: {
          id: n.id,
          label: n.label,
          type: n.type,
          theme: n.theme || '',
          degree: n.degree || 0,
          strength: n.strength || '',
          case_id: n.case_id || '',
          url: n.url || '',
        },
      })),
      ...allEdges.map((e, i) => ({
        data: {
          id: `e${i}`,
          source: e.source,
          target: e.target,
          type: e.type,
        },
      })),
    ];

    cy = cytoscape({
      container,
      elements,
      style: CY_STYLE,
      layout: {
        name: 'fcose',
        animate: true,
        animationDuration: 800,
        nodeRepulsion: () => 8000,
        idealEdgeLength: () => 100,
        edgeElasticity: () => 0.45,
        gravity: 0.25,
        gravityRange: 1.5,
        numIter: 2500,
        quality: 'proof',
        randomize: true,
        nodeSeparation: 80,
      } as any,
      minZoom: 0.15,
      maxZoom: 4,
      wheelSensitivity: 0.3,
    });

    setupInteractions();
    populateFilters();
    updateStats();

  } catch (err) {
    console.error('Failed to load graph data:', err);
    container.innerHTML = `
      <div style="
        position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
        text-align:center;color:#64748b;font-size:14px;
      ">
        <div style="font-size:48px;margin-bottom:16px;opacity:0.3">◇</div>
        <p>Failed to load knowledge graph</p>
        <p style="font-size:12px;margin-top:8px;color:#475569">
          Ensure the OVP server is running on :9990
        </p>
      </div>
    `;
  }
}

// ── Interactions ──

function setupInteractions() {
  if (!cy) return;

  // Hover: highlight node + neighbors
  cy.on('mouseover', 'node', (evt) => {
    const node = evt.target;
    const neighborhood = node.closedNeighborhood();

    cy!.elements().addClass('faded');
    neighborhood.removeClass('faded');
    node.addClass('highlighted');
    neighborhood.nodes().not(node).addClass('neighbor');
    neighborhood.edges().addClass('highlighted');
  });

  cy.on('mouseout', 'node', () => {
    cy!.elements().removeClass('faded highlighted neighbor');
  });

  // Click: show detail panel
  cy.on('tap', 'node', async (evt) => {
    const node = evt.target;
    const type = node.data('type');
    const id = node.data('id');

    if (type === 'claim') {
      await showClaimDetail(id);
    } else {
      showNodeInfo(node.data());
    }
  });

  // Double-tap: zoom to node
  cy.on('dbltap', 'node', (evt) => {
    const node = evt.target;
    cy!.animate({
      center: { eles: node },
      zoom: 2.5,
    }, { duration: 600 });
  });

  // Background click: close panel
  cy.on('tap', (evt) => {
    if (evt.target === cy) {
      detailPanel.classList.add('hidden');
    }
  });

  // Close button
  closeBtn.addEventListener('click', () => detailPanel.classList.add('hidden'));

  // Filters
  themeFilter.addEventListener('change', applyFilters);
  typeFilter.addEventListener('change', applyFilters);
  searchInput.addEventListener('input', applyFilters);
}

// ── Filters ──

function applyFilters() {
  if (!cy) return;

  const theme = themeFilter.value;
  const type = typeFilter.value;
  const query = searchInput.value.toLowerCase().trim();

  cy.elements().removeClass('faded search-match');

  cy.nodes().forEach(node => {
    let visible = true;
    if (theme && node.data('theme') !== theme) visible = false;
    if (type && node.data('type') !== type) visible = false;
    if (query && !node.data('label').toLowerCase().includes(query)) visible = false;

    if (!visible) {
      node.addClass('faded');
      node.connectedEdges().addClass('faded');
    }
  });

  if (query) {
    cy.nodes().forEach(node => {
      if (node.data('label').toLowerCase().includes(query)) {
        node.addClass('search-match');
        node.removeClass('faded');
      }
    });
  }
}

function populateFilters() {
  const themes = [...new Set(allNodes.filter(n => n.theme).map(n => n.theme!))].sort();
  themes.forEach(t => {
    const opt = document.createElement('option');
    opt.value = t;
    opt.textContent = t;
    themeFilter.appendChild(opt);
  });
}

// ── Detail panels ──

async function showClaimDetail(id: string) {
  try {
    const detail: ClaimDetail = await fetchClaim(id);
    detailContent.innerHTML = `
      <div class="detail-type" style="color:${COLORS.claim}">◆ CLAIM</div>
      <h3>${detail.claim}</h3>
      <div class="meta">
        <span class="tag theme-tag">${detail.theme}</span>
        <span class="tag strength-tag">${detail.strength}</span>
      </div>
      <div class="citation-list">
        <div class="section-label">Citations (${detail.citations.length})</div>
        ${detail.citations.map(c => `
          <div class="cit-entry">
            <div class="quote">"${c.quote}"</div>
            <div class="ref">
              <span style="color:${COLORS.source}">● ${c.source_title}</span>
              <span class="line-ref">${c.resolved_line ? `L${c.resolved_line}` : ''}</span>
            </div>
          </div>
        `).join('')}
      </div>
    `;
  } catch {
    detailContent.innerHTML = `<h3>Claim ${id.slice(0, 12)}…</h3><p class="error-msg">Failed to load details.</p>`;
  }
  detailPanel.classList.remove('hidden');
}

function showNodeInfo(data: any) {
  const typeIcon = data.type === 'unit' ? '■' : '●';
  const color = nodeColor(data.type);
  const extra = data.url
    ? `<a href="${data.url}" target="_blank" class="source-link">Open source ↗</a>`
    : '';
  detailContent.innerHTML = `
    <div class="detail-type" style="color:${color}">${typeIcon} ${data.type.toUpperCase()}</div>
    <h3>${data.label}</h3>
    <div class="meta">
      <span class="tag">${data.id.slice(0, 16)}…</span>
      ${data.degree ? `<span class="tag">${data.degree} connections</span>` : ''}
    </div>
    ${extra}
  `;
  detailPanel.classList.remove('hidden');
}

// ── Stats ──

function updateStats() {
  if (!statsEl) return;
  const claims = allNodes.filter(n => n.type === 'claim').length;
  const units = allNodes.filter(n => n.type === 'unit').length;
  const sources = allNodes.filter(n => n.type === 'source').length;
  statsEl.innerHTML = `
    <span><span class="dot claim-dot"></span>${claims} claims</span>
    <span><span class="dot unit-dot"></span>${units} units</span>
    <span><span class="dot source-dot"></span>${sources} sources</span>
    <span style="color:#64748b">${allEdges.length} edges</span>
  `;
}

// ── Cleanup (for view switching) ──

export function destroy2D() {
  if (cy) {
    cy.destroy();
    cy = null;
  }
}
