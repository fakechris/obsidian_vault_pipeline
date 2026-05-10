/* ============================================================
   OVP UI Kit · Mock vault graph data (8 communities, ~120 nodes)
   Exposed as window.OVP_GRAPH = { communities, nodes, links }.
   ============================================================ */

(function () {
  const communities = [
    { id: "c1", name: "AI Research",      slug: "ai-research",   color: "var(--c-1)", count: 0 },
    { id: "c2", name: "Tools",            slug: "tools",         color: "var(--c-2)", count: 0 },
    { id: "c3", name: "Programming",      slug: "programming",   color: "var(--c-3)", count: 0 },
    { id: "c4", name: "Investing",        slug: "investing",     color: "var(--c-4)", count: 0 },
    { id: "c5", name: "Writing",          slug: "writing",       color: "var(--c-5)", count: 0 },
    { id: "c6", name: "Ops & Pipelines",  slug: "ops",           color: "var(--c-6)", count: 0 },
    { id: "c7", name: "Research Method",  slug: "research-method", color: "var(--c-7)", count: 0 },
    { id: "c8", name: "Archive",          slug: "archive",       color: "var(--c-8)", count: 0 },
  ];

  // node template: { id, label, type, community, quality, backlinks, openQuestion, source, absorbedAt }
  // type ∈ evergreen | deepdive | topic | open-question
  // source ∈ pinboard | clipper | manual | github
  const NODES = [
    // ---- AI Research (c1) ----
    ["RAG", "evergreen", "c1", 4.4, 12, false, "manual", "2026-03-22"],
    ["AI Agent", "evergreen", "c1", 4.2, 9, false, "pinboard", "2026-04-03"],
    ["ReAct", "evergreen", "c1", 4.0, 6, false, "clipper", "2026-03-29"],
    ["Toolformer", "evergreen", "c1", 3.8, 5, false, "clipper", "2026-03-15"],
    ["Self-RAG", "evergreen", "c1", 3.9, 4, false, "manual", "2026-04-01"],
    ["Re-ranking", "evergreen", "c1", 3.6, 7, true, "manual", "2026-03-30"],
    ["Plan-and-Execute", "evergreen", "c1", 3.5, 3, false, "pinboard", "2026-03-19"],
    ["Reflection", "evergreen", "c1", 3.4, 4, false, "pinboard", "2026-03-12"],
    ["Multi-Agent Coordination", "evergreen", "c1", 3.0, 2, false, "clipper", "2026-02-28"],
    ["Tool Use", "evergreen", "c1", 4.1, 8, false, "manual", "2026-03-05"],
    ["RAG vs. Agent Architectures", "topic", "c1", 4.6, 14, true, "manual", "2026-04-04"],
    ["ai-agent-architecture_深度解读", "deepdive", "c1", 4.3, 6, false, "manual", "2026-03-28"],
    ["react-paper-replication_深度解读", "deepdive", "c1", 4.0, 4, false, "manual", "2026-03-22"],
    ["self-rag-2023_深度解读", "deepdive", "c1", 3.7, 3, false, "manual", "2026-04-01"],
    ["Does re-ranking help recall@10?", "open-question", "c1", null, 0, true, "manual", "2026-04-02"],

    // ---- Tools (c2) ----
    ["Obsidian", "evergreen", "c2", 4.5, 10, false, "manual", "2026-03-12"],
    ["SQLite", "evergreen", "c2", 4.4, 7, false, "manual", "2026-03-08"],
    ["Markdown", "evergreen", "c2", 4.2, 9, false, "manual", "2026-02-20"],
    ["Pinboard", "evergreen", "c2", 3.8, 4, false, "manual", "2026-03-01"],
    ["Obsidian Clipper", "evergreen", "c2", 3.6, 5, false, "manual", "2026-03-15"],
    ["FTS5", "evergreen", "c2", 4.0, 4, false, "github", "2026-03-20"],
    ["Local-first Knowledge Tooling", "topic", "c2", 4.4, 11, false, "manual", "2026-04-04"],
    ["local-first-tooling_深度解读", "deepdive", "c2", 4.2, 6, false, "manual", "2026-04-02"],
    ["sqlite-fts5-bm25_深度解读", "deepdive", "c2", 3.9, 3, false, "manual", "2026-03-28"],
    ["Obsidian Graph View", "evergreen", "c2", 3.4, 3, false, "clipper", "2026-02-12"],
    ["Markdown-it", "evergreen", "c2", 3.2, 2, false, "github", "2026-03-18"],

    // ---- Programming (c3) ----
    ["Python Packaging", "evergreen", "c3", 4.1, 6, false, "manual", "2026-03-04"],
    ["Type Systems", "evergreen", "c3", 4.0, 5, false, "manual", "2026-02-25"],
    ["Six-term Architecture Vocabulary", "topic", "c3", 4.7, 13, false, "manual", "2026-04-04"],
    ["Source", "evergreen", "c3", 4.5, 9, false, "manual", "2026-03-30"],
    ["Candidate", "evergreen", "c3", 4.5, 9, false, "manual", "2026-03-30"],
    ["Canonical State", "evergreen", "c3", 4.6, 11, false, "manual", "2026-03-30"],
    ["Projection", "evergreen", "c3", 4.6, 12, false, "manual", "2026-03-30"],
    ["Access Surface", "evergreen", "c3", 4.4, 7, false, "manual", "2026-03-30"],
    ["Governance", "evergreen", "c3", 4.4, 7, false, "manual", "2026-03-30"],
    ["Authority Identity Discipline", "evergreen", "c3", 4.0, 5, true, "manual", "2026-03-31"],
    ["Idempotency", "evergreen", "c3", 3.9, 4, false, "manual", "2026-03-10"],
    ["DAG Execution", "evergreen", "c3", 3.7, 3, false, "manual", "2026-03-12"],
    ["Handler Registry Pattern", "evergreen", "c3", 3.5, 3, false, "manual", "2026-03-08"],
    ["six-term-architecture_深度解读", "deepdive", "c3", 4.5, 8, false, "manual", "2026-04-03"],

    // ---- Investing (c4) ----
    ["Compound Interest", "evergreen", "c4", 4.2, 5, false, "pinboard", "2026-02-10"],
    ["Index Funds", "evergreen", "c4", 4.0, 4, false, "pinboard", "2026-02-15"],
    ["Asset Allocation", "evergreen", "c4", 3.8, 4, false, "manual", "2026-02-22"],
    ["Risk Parity", "evergreen", "c4", 3.5, 3, false, "clipper", "2026-03-01"],
    ["Sharpe Ratio", "evergreen", "c4", 3.3, 2, false, "clipper", "2026-02-28"],
    ["bogleheads-philosophy_深度解读", "deepdive", "c4", 4.0, 5, false, "manual", "2026-02-20"],

    // ---- Writing (c5) ----
    ["Atomic Notes", "evergreen", "c5", 4.5, 11, false, "manual", "2026-02-18"],
    ["Evergreen Notes (Matuschak)", "evergreen", "c5", 4.4, 9, false, "manual", "2026-02-19"],
    ["Zettelkasten", "evergreen", "c5", 4.2, 7, false, "manual", "2026-02-19"],
    ["MOC (Map of Content)", "evergreen", "c5", 4.1, 8, false, "manual", "2026-02-22"],
    ["Six-dimension Quality Rubric", "evergreen", "c5", 4.3, 9, false, "manual", "2026-04-03"],
    ["PARA Method", "evergreen", "c5", 3.9, 4, false, "pinboard", "2026-02-10"],
    ["evergreen-notes-system_深度解读", "deepdive", "c5", 4.2, 6, false, "manual", "2026-02-25"],
    ["Karpathy LLM Wiki Pattern", "evergreen", "c5", 4.6, 14, false, "manual", "2026-04-04"],
    ["karpathy-llm-wiki_深度解读", "deepdive", "c5", 4.4, 8, false, "manual", "2026-04-04"],
    ["Should Evergreen pages cap at one concept?", "open-question", "c5", null, 0, true, "manual", "2026-04-01"],

    // ---- Ops & Pipelines (c6) ----
    ["AutoPilot Daemon", "evergreen", "c6", 4.0, 5, false, "manual", "2026-03-25"],
    ["WIGS 5-layer Lint", "evergreen", "c6", 4.2, 6, false, "manual", "2026-03-26"],
    ["Transaction Manager", "evergreen", "c6", 3.8, 4, false, "manual", "2026-03-20"],
    ["Pack API", "evergreen", "c6", 4.1, 5, false, "manual", "2026-03-22"],
    ["research-tech pack", "evergreen", "c6", 3.7, 3, false, "github", "2026-03-23"],
    ["pipeline-dag_深度解读", "deepdive", "c6", 4.1, 5, false, "manual", "2026-03-30"],
    ["AutoPilot Watch Mode", "evergreen", "c6", 3.6, 3, false, "manual", "2026-03-26"],

    // ---- Research Method (c7) ----
    ["Deterministic Section Embeddings", "evergreen", "c7", 4.3, 6, false, "manual", "2026-04-02"],
    ["BM25", "evergreen", "c7", 4.1, 5, false, "github", "2026-03-15"],
    ["Hybrid Retrieval", "evergreen", "c7", 3.6, 3, true, "clipper", "2026-03-22"],
    ["Citation Graph", "evergreen", "c7", 3.7, 3, false, "manual", "2026-03-19"],
    ["Truth Projection", "evergreen", "c7", 4.4, 9, false, "manual", "2026-04-03"],
    ["Contradiction Crystal", "evergreen", "c7", 4.2, 6, true, "manual", "2026-04-02"],
    ["Community Crystal", "evergreen", "c7", 4.3, 7, false, "manual", "2026-04-02"],
    ["Is QMD Authority or Projection?", "open-question", "c7", null, 0, true, "manual", "2026-04-01"],

    // ---- Archive (c8) ----
    ["Old: SaaS Knowledge Base", "evergreen", "c8", 2.4, 1, false, "pinboard", "2025-08-12"],
    ["Old: Notion Database", "evergreen", "c8", 2.5, 1, false, "pinboard", "2025-09-04"],
    ["Old: Roam Research", "evergreen", "c8", 2.6, 2, false, "pinboard", "2025-10-22"],
    ["Old: LogSeq Trial", "evergreen", "c8", 2.8, 1, false, "pinboard", "2025-11-30"],
  ];

  const nodes = NODES.map((n, i) => ({
    id: "n" + i,
    label: n[0],
    type: n[1],
    community: n[2],
    quality: n[3],
    backlinks: n[4],
    openQuestion: n[5],
    source: n[6],
    absorbedAt: n[7],
  }));

  // Update community counts
  nodes.forEach(n => {
    const c = communities.find(c => c.id === n.community);
    if (c) c.count++;
  });

  // Build links: dense within community, sparse across
  const links = [];
  function addLink(a, b, kind) { links.push({ source: a, target: b, kind }); }

  // Within-community: chain + a few cross-edges
  communities.forEach(c => {
    const members = nodes.filter(n => n.community === c.id).map(n => n.id);
    for (let i = 0; i < members.length - 1; i++) {
      addLink(members[i], members[i + 1], "ref");
    }
    for (let i = 0; i < Math.floor(members.length / 3); i++) {
      const a = members[Math.floor(Math.random() * members.length)];
      const b = members[Math.floor(Math.random() * members.length)];
      if (a !== b) addLink(a, b, "ref");
    }
  });

  // Cross-community: hand-picked semantically meaningful bridges
  const byLabel = Object.fromEntries(nodes.map(n => [n.label, n.id]));
  const bridges = [
    ["RAG", "Re-ranking", "ref"],
    ["RAG", "FTS5", "ref"],
    ["RAG", "BM25", "ref"],
    ["RAG", "Hybrid Retrieval", "contradict"],
    ["RAG vs. Agent Architectures", "AI Agent", "cite"],
    ["RAG vs. Agent Architectures", "RAG", "cite"],
    ["RAG vs. Agent Architectures", "Does re-ranking help recall@10?", "contradict"],
    ["AI Agent", "Tool Use", "ref"],
    ["AI Agent", "ReAct", "ref"],
    ["AI Agent", "Reflection", "ref"],
    ["Six-term Architecture Vocabulary", "Source", "cite"],
    ["Six-term Architecture Vocabulary", "Candidate", "cite"],
    ["Six-term Architecture Vocabulary", "Canonical State", "cite"],
    ["Six-term Architecture Vocabulary", "Projection", "cite"],
    ["Six-term Architecture Vocabulary", "Access Surface", "cite"],
    ["Six-term Architecture Vocabulary", "Governance", "cite"],
    ["Karpathy LLM Wiki Pattern", "Evergreen Notes (Matuschak)", "ref"],
    ["Karpathy LLM Wiki Pattern", "Atomic Notes", "ref"],
    ["Karpathy LLM Wiki Pattern", "Six-dimension Quality Rubric", "ref"],
    ["Karpathy LLM Wiki Pattern", "Authority Identity Discipline", "ref"],
    ["AutoPilot Daemon", "Pack API", "ref"],
    ["AutoPilot Daemon", "Transaction Manager", "ref"],
    ["WIGS 5-layer Lint", "Authority Identity Discipline", "ref"],
    ["Truth Projection", "Projection", "cite"],
    ["Truth Projection", "Canonical State", "cite"],
    ["Contradiction Crystal", "Truth Projection", "ref"],
    ["Community Crystal", "Truth Projection", "ref"],
    ["Local-first Knowledge Tooling", "Obsidian", "cite"],
    ["Local-first Knowledge Tooling", "SQLite", "cite"],
    ["Local-first Knowledge Tooling", "Markdown", "cite"],
    ["Obsidian", "Markdown", "ref"],
    ["Obsidian", "Obsidian Clipper", "ref"],
    ["FTS5", "SQLite", "ref"],
    ["BM25", "FTS5", "ref"],
    ["Self-RAG", "RAG", "ref"],
    ["Re-ranking", "BM25", "ref"],
    ["Hybrid Retrieval", "BM25", "ref"],
    ["Hybrid Retrieval", "Deterministic Section Embeddings", "ref"],
    ["MOC (Map of Content)", "Atomic Notes", "ref"],
    ["MOC (Map of Content)", "PARA Method", "ref"],
    ["Six-dimension Quality Rubric", "Atomic Notes", "ref"],
    ["Should Evergreen pages cap at one concept?", "Atomic Notes", "contradict"],
    ["Is QMD Authority or Projection?", "Authority Identity Discipline", "contradict"],
    ["Is QMD Authority or Projection?", "Projection", "contradict"],
    ["Old: Notion Database", "Local-first Knowledge Tooling", "contradict"],
    ["Old: Roam Research", "Local-first Knowledge Tooling", "contradict"],
  ];
  bridges.forEach(([a, b, k]) => {
    if (byLabel[a] && byLabel[b]) addLink(byLabel[a], byLabel[b], k);
  });

  window.OVP_GRAPH = { communities, nodes, links };
})();
