import { fetchSearch, fetchClaim } from './shared/api';
import type { SearchResult, ClaimDetail } from './shared/types';

const searchBox = document.getElementById('search-box') as HTMLInputElement;
const resultsList = document.getElementById('results-list')!;
const provenanceDetail = document.getElementById('provenance-detail')!;

let debounceTimer: ReturnType<typeof setTimeout>;

searchBox.addEventListener('input', () => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(doSearch, 300);
});

async function doSearch() {
  const q = searchBox.value.trim();
  if (!q) {
    resultsList.innerHTML = '';
    return;
  }

  try {
    const results: SearchResult[] = await fetchSearch(q);
    renderResults(results);
  } catch (err) {
    resultsList.innerHTML = `<p style="color:#94a3b8;padding:0.5rem">Search failed</p>`;
  }
}

function renderResults(results: SearchResult[]) {
  if (results.length === 0) {
    resultsList.innerHTML = `<p style="color:#94a3b8;padding:0.5rem">No results found</p>`;
    return;
  }

  resultsList.innerHTML = results.map(r => `
    <div class="result-item" data-id="${r.id}" data-kind="${r.kind}">
      <span class="kind">${r.kind}</span>
      <div>${r.label}</div>
    </div>
  `).join('');

  resultsList.querySelectorAll('.result-item').forEach(el => {
    el.addEventListener('click', () => {
      const id = el.getAttribute('data-id')!;
      const kind = el.getAttribute('data-kind')!;
      showDetail(id, kind);
    });
  });
}

async function showDetail(id: string, kind: string) {
  if (kind === 'claim') {
    try {
      const detail: ClaimDetail = await fetchClaim(id);
      provenanceDetail.innerHTML = `
        <h2 style="color:var(--claim)">${detail.claim}</h2>
        <p style="color:var(--text-muted);margin:0.5rem 0">
          Theme: <strong>${detail.theme}</strong> · Strength: <strong>${detail.strength}</strong>
        </p>
        <h2 style="margin-top:1.5rem">Citation Chain (${detail.citations.length})</h2>
        <div class="citation-chain">
          ${detail.citations.map(c => `
            <div class="citation-item">
              <div><strong>Unit:</strong> ${c.unit_id.slice(0, 12)}…</div>
              <div class="quote">"${c.quote}"</div>
              <div class="source-ref">📄 ${c.source_title}</div>
            </div>
          `).join('')}
        </div>
      `;
    } catch {
      provenanceDetail.innerHTML = `<p class="placeholder">Failed to load claim detail</p>`;
    }
  } else {
    provenanceDetail.innerHTML = `
      <h2>${kind}: ${id.slice(0, 16)}…</h2>
      <p style="color:var(--text-muted)">Detailed provenance for non-claim items is available in the 3D graph view.</p>
    `;
  }
}
