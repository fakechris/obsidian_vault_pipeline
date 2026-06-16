import { init2D, destroy2D } from './graph2d';

const cyContainer = document.getElementById('cy-container')!;
const graphContainer = document.getElementById('graph-container')!;
const toggleBtn = document.getElementById('toggle-view') as HTMLButtonElement;
const toggleDag = document.getElementById('toggle-dag') as HTMLButtonElement;

let currentView: '2d' | '3d' = '2d';
let graph3dLoaded = false;

// Default: start with 2D
graphContainer.style.display = 'none';
cyContainer.style.display = 'block';
toggleDag.style.display = 'none';
init2D();

toggleBtn.addEventListener('click', async () => {
  if (currentView === '2d') {
    // Switch to 3D
    currentView = '3d';
    toggleBtn.textContent = '2D';
    toggleBtn.title = 'Switch to 2D view';
    toggleDag.style.display = '';

    destroy2D();
    cyContainer.style.display = 'none';
    graphContainer.style.display = 'block';

    if (!graph3dLoaded) {
      await import('./graph');
      graph3dLoaded = true;
    }
  } else {
    // Switch to 2D
    currentView = '2d';
    toggleBtn.textContent = '3D';
    toggleBtn.title = 'Switch to 3D immersive view';
    toggleDag.style.display = 'none';

    graphContainer.style.display = 'none';
    cyContainer.style.display = 'block';
    init2D();
  }
});
