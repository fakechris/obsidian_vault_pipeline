import ForceGraph3D from '3d-force-graph';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { CSS2DRenderer, CSS2DObject } from 'three/examples/jsm/renderers/CSS2DRenderer.js';
import * as THREE from 'three';
import { fetchGraph, fetchClaim } from './shared/api';
import { COLORS, BLOOM, GRAPH, NODE_COLORS, EDGE_COLORS, nodeColor } from './shared/theme';
import type { GraphNode, GraphEdge, ClaimDetail } from './shared/types';

interface FGNode extends GraphNode {
  x?: number;
  y?: number;
  z?: number;
  __threeObj?: THREE.Object3D;
}

interface FGEdge {
  source: string | FGNode;
  target: string | FGNode;
  type: string;
}

const container = document.getElementById('graph-container')!;
const detailPanel = document.getElementById('detail-panel')!;
const detailContent = document.getElementById('detail-content')!;
const closeBtn = document.getElementById('close-detail')!;
const themeFilter = document.getElementById('theme-filter') as HTMLSelectElement;
const typeFilter = document.getElementById('type-filter') as HTMLSelectElement;
const searchInput = document.getElementById('search-input') as HTMLInputElement;
const toggleDag = document.getElementById('toggle-dag') as HTMLButtonElement;
const resetCamera = document.getElementById('reset-camera') as HTMLButtonElement;

let allNodes: FGNode[] = [];
let allEdges: FGEdge[] = [];
let isDag = false;
let highlightNodes = new Set<string>();
let hoverNode: FGNode | null = null;

// ── Text label (CSS2D — always visible in 3D space) ──

function createTextLabel(text: string, type: string): CSS2DObject {
  const div = document.createElement('div');
  div.className = `node-label node-label-${type}`;
  const truncated = text.length > 24 ? text.slice(0, 22) + '…' : text;
  div.textContent = truncated;
  const obj = new CSS2DObject(div);
  return obj;
}

// ── Node geometry: soft sphere + gentle halo ──

function createNodeObject(node: FGNode): THREE.Object3D {
  const group = new THREE.Group();
  const cfg = NODE_COLORS[node.type] || NODE_COLORS.unit;
  const deg = node.degree || 1;

  // Claim nodes are bigger to stand out as the primary entity
  const radius = node.type === 'claim'
    ? Math.max(4, Math.sqrt(deg) * 3.2)
    : node.type === 'source'
      ? Math.max(2.5, Math.sqrt(deg) * 2)
      : Math.max(3, Math.sqrt(deg) * 2.5);

  // Core sphere — standard material with subtle emissive
  const coreGeo = new THREE.SphereGeometry(radius, 32, 24);
  const coreMat = new THREE.MeshStandardMaterial({
    color: cfg.main,
    emissive: cfg.emissive,
    emissiveIntensity: cfg.emissiveIntensity,
    roughness: 0.4,
    metalness: 0.05,
    transparent: true,
    opacity: 0.9,
  });
  const core = new THREE.Mesh(coreGeo, coreMat);
  group.add(core);

  // Soft outer glow (much subtler than before)
  const haloSize = radius * 3.5;
  const canvas = document.createElement('canvas');
  canvas.width = 64;
  canvas.height = 64;
  const ctx = canvas.getContext('2d')!;
  const gradient = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
  const hex = '#' + cfg.main.toString(16).padStart(6, '0');
  gradient.addColorStop(0, hex + '55');
  gradient.addColorStop(0.35, hex + '22');
  gradient.addColorStop(0.7, hex + '08');
  gradient.addColorStop(1, hex + '00');
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, 64, 64);
  const haloTex = new THREE.CanvasTexture(canvas);

  const haloMat = new THREE.SpriteMaterial({
    map: haloTex,
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    opacity: 0.5,
  });
  const halo = new THREE.Sprite(haloMat);
  halo.scale.set(haloSize, haloSize, 1);
  group.add(halo);

  // Claim ring indicator (subtle)
  if (node.type === 'claim') {
    const ringGeo = new THREE.TorusGeometry(radius * 1.6, 0.2, 8, 48);
    const ringMat = new THREE.MeshBasicMaterial({
      color: cfg.main,
      transparent: true,
      opacity: 0.25,
    });
    const ring = new THREE.Mesh(ringGeo, ringMat);
    ring.rotation.x = Math.PI / 2;
    group.add(ring);
  }

  // Always-visible text label
  const label = createTextLabel(node.label, node.type);
  label.position.set(0, radius + 4, 0);
  group.add(label);

  (group as any).__radius = radius;
  (group as any).__coreMat = coreMat;
  (group as any).__haloMat = haloMat;
  (group as any).__label = label;
  return group;
}

// ── Subtle starfield (very dim — more atmosphere than focus) ──

function createStarfield(scene: THREE.Scene) {
  const geo = new THREE.BufferGeometry();
  const count = GRAPH.starCount;
  const pos = new Float32Array(count * 3);

  for (let i = 0; i < count; i++) {
    const r = 1200 + Math.random() * 800;
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    pos[i * 3 + 2] = r * Math.cos(phi);
  }

  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));

  const mat = new THREE.PointsMaterial({
    size: 0.6,
    color: 0x475569,
    transparent: true,
    opacity: 0.35,
    sizeAttenuation: true,
    depthWrite: false,
  });
  scene.add(new THREE.Points(geo, mat));
}

// ── Animation loop ──

let animFrame = 0;
function animateNodes() {
  animFrame++;
  const t = animFrame * GRAPH.nodePulseSpeed;

  allNodes.forEach((node) => {
    const obj = node.__threeObj as any;
    if (!obj || !obj.__radius) return;

    // Gentle breathing
    const pulse = 1 + Math.sin(t + (node.degree || 1) * 0.3) * GRAPH.nodePulseRange;
    obj.children[0]?.scale.set(pulse, pulse, pulse);

    // Slow ring rotation for claims
    if (node.type === 'claim' && obj.children[2]) {
      obj.children[2].rotation.z += 0.003;
    }

    // Highlight/dim on hover
    if (highlightNodes.size > 0) {
      const isHl = highlightNodes.has(node.id);
      if (obj.__coreMat) {
        obj.__coreMat.opacity = isHl ? 0.95 : 0.15;
        obj.__coreMat.emissiveIntensity = isHl
          ? (NODE_COLORS[node.type]?.emissiveIntensity || 0.4) * 2
          : 0.05;
      }
      if (obj.__haloMat) obj.__haloMat.opacity = isHl ? 0.6 : 0.02;
      if (obj.__label) obj.__label.element.style.opacity = isHl ? '1' : '0.08';
    } else {
      const cfg = NODE_COLORS[node.type];
      if (obj.__coreMat && cfg) {
        obj.__coreMat.opacity = 0.9;
        obj.__coreMat.emissiveIntensity = cfg.emissiveIntensity;
      }
      if (obj.__haloMat) obj.__haloMat.opacity = 0.5;
      if (obj.__label) obj.__label.element.style.opacity = '1';
    }
  });

  requestAnimationFrame(animateNodes);
}

// ── Graph initialization ──

const extraRenderers = [new CSS2DRenderer() as any];

const graph = new ForceGraph3D(container, { extraRenderers })
  .backgroundColor(COLORS.background)
  .showNavInfo(false)
  .nodeThreeObject((n: object) => createNodeObject(n as FGNode))
  .nodeThreeObjectExtend(false)
  .nodeLabel(() => '')
  .linkColor((e: object) => {
    const edge = e as FGEdge;
    if (edge.type === 'cites') return '#a78bfa88';
    if (edge.type === 'extracted_from') return '#67e8f966';
    return '#64748b66';
  })
  .linkWidth((e: object) => {
    const edge = e as FGEdge;
    return edge.type === 'cites' ? GRAPH.linkWidthCites : GRAPH.linkWidth;
  })
  .linkCurvature(0.15)
  .linkCurveRotation(0.4)
  .linkDirectionalArrowLength(2.5)
  .linkDirectionalArrowRelPos(0.88)
  .linkDirectionalArrowColor((e: object) => {
    const edge = e as FGEdge;
    if (edge.type === 'cites') return '#a78bfacc';
    if (edge.type === 'extracted_from') return '#67e8f9aa';
    return '#94a3b8aa';
  })
  .linkDirectionalParticles(GRAPH.particleCount)
  .linkDirectionalParticleWidth(GRAPH.particleWidth)
  .linkDirectionalParticleSpeed(GRAPH.particleSpeed)
  .linkDirectionalParticleColor((e: object) => {
    const edge = e as FGEdge;
    if (edge.type === 'cites') return '#c4b5fd';
    if (edge.type === 'extracted_from') return '#67e8f9';
    return '#94a3b8';
  })
  .linkOpacity(GRAPH.linkOpacity)
  .onNodeClick(async (node: object) => {
    const n = node as FGNode;
    if (n.type === 'claim') {
      showClaimDetail(n.id);
    } else {
      showNodeInfo(n);
    }
    const dist = 50 + (n.degree || 1) * 6;
    graph.cameraPosition(
      { x: (n.x || 0) + dist * 0.7, y: (n.y || 0) + dist * 0.3, z: (n.z || 0) + dist },
      { x: n.x || 0, y: n.y || 0, z: n.z || 0 },
      1000
    );
  })
  .onNodeHover((node: object | null) => {
    container.style.cursor = node ? 'pointer' : 'default';
    hoverNode = node as FGNode | null;
    highlightNodes.clear();

    if (node) {
      const n = node as FGNode;
      highlightNodes.add(n.id);
      allEdges.forEach(e => {
        const s = typeof e.source === 'string' ? e.source : (e.source as FGNode).id;
        const t = typeof e.target === 'string' ? e.target : (e.target as FGNode).id;
        if (s === n.id || t === n.id) {
          highlightNodes.add(s);
          highlightNodes.add(t);
        }
      });
    }
  })
  .onBackgroundClick(() => {
    detailPanel.classList.add('hidden');
    highlightNodes.clear();
  })
  .cooldownTime(GRAPH.cooldownTime)
  .warmupTicks(120)
  .d3AlphaDecay(0.015)
  .d3VelocityDecay(0.35);

// Configure forces after graph object exists
const chargeFn = graph.d3Force('charge') as any;
if (chargeFn?.strength) chargeFn.strength(GRAPH.chargeStrength);
const linkFn = graph.d3Force('link') as any;
if (linkFn?.distance) linkFn.distance(GRAPH.linkDistance);

// ── Post-processing: subtle bloom ──

const bloomPass = new UnrealBloomPass(
  new THREE.Vector2(window.innerWidth, window.innerHeight),
  BLOOM.strength,
  BLOOM.radius,
  BLOOM.threshold
);
graph.postProcessingComposer().addPass(bloomPass);

// ── Scene enhancements ──

const scene = graph.scene();

scene.add(new THREE.AmbientLight(0xe2e8f0, 0.4));

const keyLight = new THREE.DirectionalLight(0xf1f5f9, 0.6);
keyLight.position.set(80, 120, 80);
scene.add(keyLight);

const fillLight = new THREE.PointLight(0x8b5cf6, 0.3, 400);
fillLight.position.set(-80, -60, -80);
scene.add(fillLight);

scene.fog = new THREE.FogExp2(new THREE.Color(COLORS.background).getHex(), 0.0004);

createStarfield(scene);

// Auto-rotate
const controls = graph.controls() as any;
if (controls) {
  controls.autoRotate = true;
  controls.autoRotateSpeed = GRAPH.autoRotateSpeed;
}

// ── UI Controls ──

closeBtn.addEventListener('click', () => detailPanel.classList.add('hidden'));

toggleDag.addEventListener('click', () => {
  isDag = !isDag;
  graph.dagMode(isDag ? 'radialout' : null as any);
  toggleDag.classList.toggle('active', isDag);
});

resetCamera.addEventListener('click', () => {
  graph.cameraPosition({ x: 0, y: 0, z: 200 }, { x: 0, y: 0, z: 0 }, 1200);
  highlightNodes.clear();
});

themeFilter.addEventListener('change', applyFilters);
typeFilter.addEventListener('change', applyFilters);
searchInput.addEventListener('input', applyFilters);

function applyFilters() {
  const theme = themeFilter.value;
  const type = typeFilter.value;
  const query = searchInput.value.toLowerCase();

  let nodes = allNodes;
  if (theme) nodes = nodes.filter(n => n.theme === theme);
  if (type) nodes = nodes.filter(n => n.type === type);
  if (query) nodes = nodes.filter(n => n.label.toLowerCase().includes(query));

  const nodeIds = new Set(nodes.map(n => n.id));
  const edges = allEdges.filter(e => {
    const s = typeof e.source === 'string' ? e.source : (e.source as FGNode).id;
    const t = typeof e.target === 'string' ? e.target : (e.target as FGNode).id;
    return nodeIds.has(s) && nodeIds.has(t);
  });

  graph.graphData({ nodes, links: edges });
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

function showNodeInfo(node: FGNode) {
  const typeIcon = node.type === 'unit' ? '■' : '●';
  const color = nodeColor(node.type);
  const extra = node.url
    ? `<a href="${node.url}" target="_blank" class="source-link">Open source ↗</a>`
    : '';
  detailContent.innerHTML = `
    <div class="detail-type" style="color:${color}">${typeIcon} ${node.type.toUpperCase()}</div>
    <h3>${node.label}</h3>
    <div class="meta">
      <span class="tag">${node.id.slice(0, 16)}…</span>
      ${node.degree ? `<span class="tag">${node.degree} connections</span>` : ''}
    </div>
    ${extra}
  `;
  detailPanel.classList.remove('hidden');
}

// ── Load & start ──

async function init() {
  try {
    const data = await fetchGraph();
    allNodes = data.nodes.map(n => ({ ...n }));
    allEdges = data.edges.map(e => ({ source: e.source, target: e.target, type: e.type }));

    allNodes.forEach(n => {
      n.degree = allEdges.filter(e => e.source === n.id || e.target === n.id).length;
    });

    // Clear existing theme options (may have been populated by 2D view)
    while (themeFilter.options.length > 1) themeFilter.remove(1);
    const themes = [...new Set(allNodes.filter(n => n.theme).map(n => n.theme!))].sort();
    themes.forEach(t => {
      const opt = document.createElement('option');
      opt.value = t;
      opt.textContent = t;
      themeFilter.appendChild(opt);
    });

    graph.graphData({ nodes: allNodes, links: allEdges });

    setTimeout(() => animateNodes(), 1500);
    setTimeout(() => {
      graph.cameraPosition({ x: 0, y: 50, z: 200 }, { x: 0, y: 0, z: 0 }, 2000);
    }, 400);

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

const statsEl = document.getElementById('graph-stats');
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

init().then(updateStats);
