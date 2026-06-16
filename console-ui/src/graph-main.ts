import { init2D, destroy2D } from './graph2d';
import { fetchGraph } from './shared/api';
import type { GraphData } from './shared/types';

const cyContainer = document.getElementById('cy-container')!;
const cosmosContainer = document.getElementById('cosmos-container')!;
const graphContainer = document.getElementById('graph-container')!;
const toggleBtn = document.getElementById('toggle-view') as HTMLButtonElement;
const toggleDag = document.getElementById('toggle-dag') as HTMLButtonElement;

// Above this many nodes, the Cytoscape layout is too slow; fall back to the
// GPU (Cosmos) engine, which simulates + renders thousands of nodes smoothly.
const COSMOS_THRESHOLD = 1000;

let currentView: '2d' | '3d' = '2d';
let twoDEngine: 'cy' | 'cosmos' = 'cy';
let graph3dLoaded = false;
let cosmosMod: typeof import('./graph-cosmos') | null = null;
let graphData: GraphData | null = null;

function hideAll() {
  cyContainer.style.display = 'none';
  cosmosContainer.style.display = 'none';
  graphContainer.style.display = 'none';
}

async function loadCosmos(data: GraphData) {
  if (!cosmosMod) cosmosMod = await import('./graph-cosmos');
  await cosmosMod.initCosmos(data);
}

async function start2D() {
  hideAll();
  graphData = await fetchGraph();
  if (graphData.nodes.length > COSMOS_THRESHOLD) {
    twoDEngine = 'cosmos';
    cosmosContainer.style.display = 'block';
    await loadCosmos(graphData);
  } else {
    twoDEngine = 'cy';
    cyContainer.style.display = 'block';
    init2D(graphData);
  }
}

toggleDag.style.display = 'none';
start2D();

toggleBtn.addEventListener('click', async () => {
  if (currentView === '2d') {
    // Tear down the active 2D engine, switch to the 3D WebGL view.
    currentView = '3d';
    toggleBtn.textContent = '2D';
    toggleBtn.title = 'Switch to 2D view';
    toggleDag.style.display = '';

    if (twoDEngine === 'cy') destroy2D();
    else if (cosmosMod) cosmosMod.destroyCosmos();

    hideAll();
    graphContainer.style.display = 'block';
    if (!graph3dLoaded) {
      await import('./graph');
      graph3dLoaded = true;
    }
  } else {
    // Back to 2D: re-init whichever engine fits the data (cached).
    currentView = '2d';
    toggleBtn.textContent = '3D';
    toggleBtn.title = 'Switch to 3D immersive view';
    toggleDag.style.display = 'none';

    hideAll();
    const data = graphData;
    if (data && data.nodes.length > COSMOS_THRESHOLD) {
      twoDEngine = 'cosmos';
      cosmosContainer.style.display = 'block';
      await loadCosmos(data);
    } else {
      twoDEngine = 'cy';
      cyContainer.style.display = 'block';
      init2D(data ?? undefined);
    }
  }
});
