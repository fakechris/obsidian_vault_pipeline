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
let colorMode: 'type' | 'cluster' = 'type';
// Claim ids sorted by degree (descending) — drives level-of-detail labels:
// at low zoom only the top hubs are labeled, more appear as you zoom in.
let claimsByDegree: string[] = [];
let isLarge = false; // engage LOD + thinning only when the graph is big

// Distinct, evenly-spaced hues for community coloring. Cluster 0 (no
// community) stays neutral gray; clusters 1..n cycle the palette.
const CLUSTER_HUES = [265, 210, 145, 35, 0, 320, 175, 50, 240, 110, 300, 20];
function clusterColor(cluster: number | undefined): string {
  if (!cluster) return '#64748b';
  const h = CLUSTER_HUES[(cluster - 1) % CLUSTER_HUES.length];
  return `hsl(${h}, 62%, 68%)`;
}
function clusterBorder(cluster: number | undefined): string {
  if (!cluster) return '#475569';
  const h = CLUSTER_HUES[(cluster - 1) % CLUSTER_HUES.length];
  return `hsl(${h}, 60%, 45%)`;
}

// ── DOM refs (shared with graph-main) ──

const container = document.getElementById('cy-container')!;
const detailPanel = document.getElementById('detail-panel')!;
const detailContent = document.getElementById('detail-content')!;
const closeBtn = document.getElementById('close-detail')!;
const themeFilter = document.getElementById('theme-filter') as HTMLSelectElement;
const typeFilter = document.getElementById('type-filter') as HTMLSelectElement;
const searchInput = document.getElementById('search-input') as HTMLInputElement;
const statsEl = document.getElementById('graph-stats');
const colorModeBtn = document.getElementById('color-mode') as HTMLButtonElement | null;
const askPanel = document.getElementById('ask-panel');
const askToggle = document.getElementById('toggle-ask') as HTMLButtonElement | null;
const askInput = document.getElementById('ask-input') as HTMLInputElement | null;
const askResults = document.getElementById('ask-results');

// ── Cytoscape style definitions ──

// Label styling shared by every node: a dark rounded pill sits *below* the
// node so text never overlaps the circle or its neighbors. Labels are clipped
// to a single line and hidden when zoomed out (min-zoomed-font-size).
const LABEL_BASE = {
  'text-valign': 'bottom',
  'text-halign': 'center',
  'text-margin-y': 5,
  'font-family': 'Inter, Noto Sans SC, system-ui, sans-serif',
  'font-size': '11px',
  'font-weight': 500,
  'color': '#e2e8f0',
  'text-max-width': '110px',
  'text-wrap': 'ellipsis',
  'text-background-color': '#0b0d13',
  'text-background-opacity': 0.78,
  'text-background-padding': '3px',
  'text-background-shape': 'roundrectangle',
  'min-zoomed-font-size': 9,
} as const;

const CY_STYLE: cytoscape.StylesheetStyle[] = [
  {
    selector: 'node',
    style: {
      'label': 'data(label)',
      ...LABEL_BASE,
      'background-opacity': 0.9,
      'border-width': 2,
      'overlay-padding': '4px',
      'transition-property': 'opacity, background-color, border-color, border-width, text-opacity',
      'transition-duration': 180,
    } as any,
  },
  {
    selector: 'node[type="claim"]',
    style: {
      'background-color': '#c4b5fd',
      'border-color': '#8b5cf6',
      'font-size': '12px',
      'font-weight': 600,
      'color': '#f5f3ff',
      'width': 'mapData(degree, 1, 14, 34, 92)',
      'height': 'mapData(degree, 1, 14, 34, 92)',
      'z-index': 3,
    } as any,
  },
  // Units and sources are provenance detail — keep them small and, by
  // default, label-free so the canvas isn't a wall of text. Their labels
  // reappear on hover / search (see .neighbor / .highlighted / .search-match).
  {
    selector: 'node[type="unit"]',
    style: {
      'background-color': '#93c5fd',
      'border-color': '#3b82f6',
      'color': '#eff6ff',
      'width': 'mapData(degree, 1, 10, 14, 34)',
      'height': 'mapData(degree, 1, 10, 14, 34)',
      'text-opacity': 0,
      'z-index': 1,
    } as any,
  },
  {
    selector: 'node[type="source"]',
    style: {
      'background-color': '#86efac',
      'border-color': '#22c55e',
      'font-size': '10px',
      'color': '#f0fdf4',
      'width': 'mapData(degree, 1, 14, 20, 56)',
      'height': 'mapData(degree, 1, 14, 20, 56)',
      'text-opacity': 0,
      'z-index': 2,
    } as any,
  },
  {
    selector: 'edge',
    style: {
      'width': 1,
      'line-color': '#475569',
      'curve-style': 'bezier',
      'opacity': 0.45,
      'transition-property': 'opacity, line-color, width',
      'transition-duration': 180,
    } as any,
  },
  // claim ↔ claim "shares a source" — the connective tissue. Undirected,
  // accent-colored, thickness scales with how many sources are shared.
  {
    selector: 'edge[type="related"]',
    style: {
      'line-color': '#fbbf24',
      'width': 'mapData(weight, 1, 4, 2, 7)',
      'opacity': 0.55,
      'curve-style': 'bezier',
      'line-cap': 'round',
      'z-index': 4,
    } as any,
  },
  // claim → unit (which sentence backs the claim)
  {
    selector: 'edge[type="cites"]',
    style: {
      'line-color': '#a78bfa',
      'target-arrow-color': '#a78bfa',
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.8,
      'width': 1.6,
      'opacity': 0.5,
    } as any,
  },
  // unit → source (where the sentence came from) — quietest layer
  {
    selector: 'edge[type="extracted_from"]',
    style: {
      'line-color': '#3f4d63',
      'width': 1,
      'opacity': 0.32,
    } as any,
  },
  // Cheap straight edges for big graphs (no arrowheads, fastest to draw).
  {
    selector: 'edge.fast-edge',
    style: {
      'curve-style': 'haystack',
      'haystack-radius': 0,
      'target-arrow-shape': 'none',
    } as any,
  },
  // At scale the within-cluster `related` mesh gets dense; keep it faint so
  // it reads as a halo, not a solid fill. Cluster position/color carry the
  // community signal.
  {
    selector: 'edge.fast-edge[type="related"]',
    style: { 'opacity': 0.14, 'width': 1 } as any,
  },
  // Compact node sizing for big graphs so clusters don't overlap into blobs.
  {
    selector: 'node.compact[type="claim"]',
    style: {
      'width': 'mapData(degree, 1, 14, 12, 34)',
      'height': 'mapData(degree, 1, 14, 12, 34)',
    } as any,
  },
  {
    selector: 'node.compact[type="source"]',
    style: { 'width': 'mapData(degree, 1, 14, 8, 22)', 'height': 'mapData(degree, 1, 14, 8, 22)' } as any,
  },
  {
    selector: 'node.compact[type="unit"]',
    style: { 'width': 8, 'height': 8 } as any,
  },
  // Level-of-detail: hide label when the node isn't important enough at the
  // current zoom. Declared BEFORE highlight states so hover/search re-show it.
  {
    selector: 'node.label-suppressed',
    style: { 'text-opacity': 0 } as any,
  },
  // Thinning: leaf nodes hidden when zoomed out on a large graph.
  {
    selector: 'node.thinned',
    style: { 'display': 'none' } as any,
  },
  // Hover/highlight states
  {
    selector: 'node.highlighted',
    style: {
      'border-width': 4,
      'background-opacity': 1,
      'text-opacity': 1,
      'z-index': 30,
    } as any,
  },
  {
    selector: 'node.neighbor',
    style: {
      'border-width': 3,
      'background-opacity': 0.98,
      'text-opacity': 1,
      'z-index': 29,
    } as any,
  },
  {
    selector: 'node.faded',
    style: {
      'opacity': 0.07,
      'text-opacity': 0,
    } as any,
  },
  {
    selector: 'edge.highlighted',
    style: {
      'opacity': 0.95,
      'width': 3,
      'z-index': 30,
    } as any,
  },
  {
    selector: 'edge.faded',
    style: {
      'opacity': 0.03,
    } as any,
  },
  {
    selector: 'node.search-match',
    style: {
      'border-width': 4,
      'border-color': '#fbbf24',
      'background-opacity': 1,
      'text-opacity': 1,
      'z-index': 40,
    } as any,
  },
];

// ── Initialization ──

export async function init2D() {
  const loading = showLoading('Loading knowledge graph…');
  try {
    const data = await fetchGraph();
    allNodes = data.nodes;
    allEdges = data.edges;
    if (allNodes.length > 600) {
      loading.textContent = `Laying out ${allNodes.length} nodes…`;
    }

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
          cluster: n.cluster || 0,
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

    // Layout cost scales badly with node count: 'proof' quality + animation
    // is fine for a few hundred elements but janks on thousands. Drop to a
    // cheaper, non-animated pass once the graph is large, and crank repulsion
    // + edge length so a cluster spreads into a readable mesh instead of a
    // solid overlapping blob.
    const big = elements.length > 600;

    cy = cytoscape({
      container,
      elements,
      style: CY_STYLE,
      // Rendering hints — the difference between "unusable" and "smooth" on a
      // 1000+ element graph: render a cached texture while panning/zooming,
      // drop edges mid-gesture, and don't pay for retina pixels.
      textureOnViewport: true,
      hideEdgesOnViewport: big,
      pixelRatio: big ? 1 : (undefined as any),
      motionBlur: false,
      layout: {
        name: 'fcose',
        animate: !big,
        animationDuration: 800,
        // Strong repulsion on big graphs so intra-cluster claims fan out;
        // longer `related` edges keep the cluster open rather than collapsed.
        nodeRepulsion: () => (big ? 80000 : 9000),
        idealEdgeLength: (edge: any) =>
          edge.data('type') === 'related' ? (big ? 150 : 70) : (big ? 220 : 150),
        edgeElasticity: (edge: any) =>
          edge.data('type') === 'related' ? 0.45 : 0.25,
        // Weaker gravity at scale → components breathe out (the "sparse,
        // comfortable" feel) instead of being pulled into the center.
        gravity: big ? 0.12 : 0.6,
        gravityRangeCompound: 1.5,
        gravityRange: big ? 4.0 : 2.4,
        numIter: big ? 300 : 2500,
        quality: big ? 'default' : 'proof',
        randomize: true,
        nodeSeparation: big ? 180 : 90,
        packComponents: true,
      } as any,
      minZoom: 0.06,
      maxZoom: 4,
      wheelSensitivity: 0.3,
    });

    // On big graphs, straight "haystack" edges are far cheaper to draw than
    // beziers (no arrowheads, but worth it for interactivity at scale), and
    // compact node sizing keeps clusters from overlapping into blobs.
    if (big) {
      cy.edges().addClass('fast-edge');
      cy.nodes().addClass('compact');
    }

    // LOD/thinning prep: rank claims by degree, decide if the graph is big
    // enough to warrant hiding detail when zoomed out.
    claimsByDegree = allNodes
      .filter(n => n.type === 'claim')
      .sort((a, b) => (b.degree || 0) - (a.degree || 0))
      .map(n => n.id);
    isLarge = allNodes.length > 80;

    setupInteractions();
    populateFilters();
    updateStats();
    setupAsk();
    setupColorMode();

    // Re-apply LOD whenever the view scale changes, and once the layout
    // settles. Throttled to one pass per animation frame.
    let lodScheduled = false;
    const scheduleLOD = () => {
      if (lodScheduled) return;
      lodScheduled = true;
      requestAnimationFrame(() => { lodScheduled = false; applyLOD(); });
    };
    cy.on('zoom', scheduleLOD);
    cy.on('layoutstop', () => { applyLOD(); loading.remove(); });
    applyLOD();
    // Fallback: ensure the overlay never gets stuck if layoutstop is missed.
    setTimeout(() => loading.remove(), 12000);

  } catch (err) {
    loading.remove();
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

// ── Color mode: by node type (default) or by community/cluster ──

function setupColorMode() {
  if (!colorModeBtn) return;
  colorModeBtn.addEventListener('click', () => {
    colorMode = colorMode === 'type' ? 'cluster' : 'type';
    applyColorMode();
    colorModeBtn!.textContent =
      colorMode === 'cluster' ? 'Color: Cluster' : 'Color: Type';
    colorModeBtn!.classList.toggle('active', colorMode === 'cluster');
  });
}

function applyColorMode() {
  if (!cy) return;
  if (colorMode === 'cluster') {
    cy.batch(() => {
      cy!.nodes().forEach(n => {
        const c = n.data('cluster') as number;
        n.style('background-color', clusterColor(c));
        n.style('border-color', clusterBorder(c));
      });
    });
  } else {
    cy.batch(() => {
      cy!.nodes().forEach(n => {
        n.removeStyle('background-color');
        n.removeStyle('border-color');
      });
    });
  }
}

// ── Level-of-detail: reveal labels / leaf nodes progressively by zoom ──

function applyLOD() {
  if (!cy) return;
  const z = cy.zoom();

  // How many of the top claims (by degree) carry a label at this zoom.
  let labelTopN: number;
  if (!isLarge) labelTopN = claimsByDegree.length;      // small graph: all
  else if (z < 0.35) labelTopN = 8;
  else if (z < 0.7) labelTopN = 24;
  else if (z < 1.2) labelTopN = 64;
  else labelTopN = claimsByDegree.length;
  const labeled = new Set(claimsByDegree.slice(0, labelTopN));

  // Thinning: drop unit leaves when zoomed out on a big graph.
  const thin = isLarge && z < 0.6;

  cy.batch(() => {
    cy!.nodes('[type="claim"]').forEach(n => {
      n.toggleClass('label-suppressed', !labeled.has(n.id()));
    });
    cy!.nodes('[type="unit"]').forEach(n => {
      n.toggleClass('thinned', thin);
    });
  });
}

// ── Ask panel: search the loaded graph, then focus + highlight on click ──

function setupAsk() {
  if (askToggle && askPanel) {
    askToggle.addEventListener('click', () => {
      const open = askPanel!.classList.toggle('open');
      askToggle!.classList.toggle('active', open);
      if (open && askInput) askInput.focus();
    });
  }
  if (!askInput || !askResults) return;
  let t: ReturnType<typeof setTimeout>;
  askInput.addEventListener('input', () => {
    clearTimeout(t);
    t = setTimeout(runAsk, 200);
  });
}

function runAsk() {
  if (!cy || !askResults) return;
  const q = (askInput?.value || '').toLowerCase().trim();
  cy.elements().removeClass('search-match');
  if (!q) {
    askResults.innerHTML =
      '<p class="ask-empty">Search claims, evidence quotes, and sources.</p>';
    return;
  }
  const order: Record<string, number> = { claim: 0, source: 1, unit: 2 };
  const hits = allNodes
    .filter(n =>
      n.label.toLowerCase().includes(q) ||
      (n.theme || '').toLowerCase().includes(q))
    .sort((a, b) =>
      (order[a.type] - order[b.type]) || ((b.degree || 0) - (a.degree || 0)))
    .slice(0, 40);

  hits.forEach(h => cy!.getElementById(h.id).addClass('search-match'));

  if (hits.length === 0) {
    askResults.innerHTML = '<p class="ask-empty">No matches.</p>';
    return;
  }
  askResults.innerHTML = hits
    .map(h => `
      <div class="ask-item" data-id="${h.id}">
        <span class="ask-kind ask-kind-${h.type}">${h.type}</span>
        <span class="ask-label">${escapeHtml(h.label)}</span>
      </div>`)
    .join('');
  askResults.querySelectorAll('.ask-item').forEach(el => {
    el.addEventListener('click', () =>
      focusNode(el.getAttribute('data-id')!));
  });
}

function focusNode(id: string) {
  if (!cy) return;
  const node = cy.getElementById(id);
  if (node.empty()) return;
  // Ensure a thinned/unlabeled node becomes visible.
  cy.animate(
    { center: { eles: node }, zoom: Math.max(cy.zoom(), 1.6) },
    { duration: 500 }
  );
  const neighborhood = node.closedNeighborhood();
  cy.elements().addClass('faded');
  neighborhood.removeClass('faded thinned');
  node.removeClass('faded label-suppressed thinned').addClass('highlighted');
  neighborhood.nodes().not(node).addClass('neighbor');
  neighborhood.edges().addClass('highlighted');
  if (node.data('type') === 'claim') showClaimDetail(id);
  else showNodeInfo(node.data());
  setTimeout(() => { if (cy) cy.elements().removeClass('faded'); }, 2200);
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] || c));
}

// ── Loading overlay (layout on a big graph blocks for a few seconds) ──

function showLoading(text: string): HTMLElement {
  const el = document.createElement('div');
  el.textContent = text;
  el.style.cssText =
    'position:fixed;inset:0;z-index:200;display:flex;align-items:center;' +
    'justify-content:center;background:#0f1117;color:#94a3b8;font-size:14px;' +
    'font-family:Inter,Noto Sans SC,system-ui,sans-serif;letter-spacing:0.02em;';
  document.body.appendChild(el);
  return el;
}

// ── Cleanup (for view switching) ──

export function destroy2D() {
  if (cy) {
    cy.destroy();
    cy = null;
  }
}
