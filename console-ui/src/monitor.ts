import type { ProgressEvent, CompleteEvent } from './shared/types';

const statusBadge = document.getElementById('status-badge')!;
const progressContainer = document.getElementById('progress-bar-container')!;
const progressBar = document.getElementById('progress-bar')!;
const progressText = document.getElementById('progress-text')!;
const sourceTbody = document.getElementById('source-tbody')!;
const eventList = document.getElementById('event-list')!;

const sourceStates = new Map<string, { stage: string; status: string; time: string }>();

function connect() {
  const es = new EventSource('/api/sse');

  es.addEventListener('progress', (ev) => {
    const data: ProgressEvent = JSON.parse(ev.data);
    setLive();
    updateSource(data);
    updateProgress(data);
    logEvent(`[${data.stage}] ${data.source}: ${data.status}`);
  });

  es.addEventListener('complete', (ev) => {
    const data: CompleteEvent = JSON.parse(ev.data);
    setIdle();
    logEvent(`✓ Run ${data.run_id.slice(0, 8)}… complete: ${data.succeeded} ok, ${data.failed} failed`);
    progressContainer.classList.add('hidden');
  });

  es.addEventListener('heartbeat', () => {});

  es.onerror = () => {
    es.close();
    setTimeout(connect, 3000);
  };

  es.onopen = () => {
    logEvent('SSE connected');
  };
}

function setLive() {
  statusBadge.textContent = '● LIVE';
  statusBadge.className = 'badge live';
}

function setIdle() {
  statusBadge.textContent = '○ IDLE';
  statusBadge.className = 'badge idle';
}

function updateSource(ev: ProgressEvent) {
  sourceStates.set(ev.source, {
    stage: ev.stage,
    status: ev.status,
    time: new Date().toLocaleTimeString(),
  });
  renderTable();
}

function updateProgress(ev: ProgressEvent) {
  if (ev.index != null && ev.total != null && ev.total > 0) {
    progressContainer.classList.remove('hidden');
    const pct = Math.round((ev.index / ev.total) * 100);
    progressBar.style.width = `${pct}%`;
    progressText.textContent = `${ev.index}/${ev.total} (${pct}%)`;
  }
}

function renderTable() {
  const rows = Array.from(sourceStates.entries())
    .sort(([, a], [, b]) => b.time.localeCompare(a.time))
    .slice(0, 50);

  sourceTbody.innerHTML = rows.map(([name, s]) => `
    <tr>
      <td>${name}</td>
      <td>${s.stage}</td>
      <td>${s.status}</td>
      <td>${s.time}</td>
    </tr>
  `).join('');
}

function logEvent(msg: string) {
  const line = document.createElement('div');
  line.className = 'event-line';
  line.textContent = `${new Date().toLocaleTimeString()} ${msg}`;
  eventList.prepend(line);

  while (eventList.children.length > 200) {
    eventList.lastChild?.remove();
  }
}

logEvent('Monitor initialized — waiting for SSE...');
connect();
