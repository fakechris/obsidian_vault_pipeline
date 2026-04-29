# Reader Product Shape And Backlog Reconciliation

**Date:** 2026-04-29
**Status:** Working backlog note
**Reference inputs:**

- `/Users/chris/Documents/ovp-vault/30-Projects/Active/OVP-Knowledge-State-Runtime.md`
- `/Users/chris/Documents/ovp-vault/20-Areas/AI-Research/Topics/2026-04/2026-04-29_AI个人知识库难用问题_研究札记.md`
- `docs/plans/2026-04-10-ovp-knowledge-system-execution-plan.md`
- `docs/plans/2026-04-16-phase17-research-graph-visualization-plan.md`
- `docs/plans/2026-04-17-phase19-orientation-and-compiled-knowledge-products.md`
- `docs/plans/2026-04-22-vision-and-roadmap-trusted-reuse-compiler.md`
- `docs/plans/2026-04-29-consolidated-product-roadmap.md`
- LearnBuffett person page: <https://learnbuffett.com/people/%E6%A0%BC%E9%9B%B7%E5%8E%84%E5%A7%86>
- LearnBuffett graph page: <https://learnbuffett.com/graph>

## Product Shape Finding

LearnBuffett is useful as a product-shape reference because it makes the knowledge base feel readable before it feels operational.

The important pattern is not the visual skin. The important pattern is the default posture:

- a user lands in a knowledge product, not an operator console
- people, concepts, companies, letters, and graph nodes are readable entry points
- evidence and backlinks are present, but they sit behind the reading flow
- graph exploration is spatial and legible, not a cluster/debug report
- operations remain available, but they are not the first screen

OVP currently has more powerful internal structure than this reference:

- typed objects
- evidence rows
- claims and relations
- contradictions
- action queue
- runtime ledger
- pack-owned semantics
- review and provenance surfaces

But the default UI still presents itself as an engineering shell. The current home page starts with runtime state, worker state, run history, and workflow maps. The object list is database-like. The cluster page exposes internal modeling terms such as relation components, model notes, and route mechanics before showing a user what the knowledge means.

The backlog should therefore add a reader-facing product layer without weakening the auditable compiler architecture.

The recent KSR project page is the most detailed current task table for knowledge-state-runtime work, but it is not a complete historical source. This note only adds the reader/product projection interpretation and must be reconciled with both KSR and the older repo milestone history.

## Reconciled Thesis

The April 22 vision says:

> OVP is an auditable knowledge compiler for Obsidian: it turns external material into evidence-backed, review-gated, reusable long-term knowledge without polluting the human vault.

This remains the right architecture narrative.

The LearnBuffett reference adds the missing product requirement:

> The compiled knowledge must first be understandable as a reader-facing knowledge base, and only secondarily visible as an operator workbench.

So the external product should be:

> **A reader-first, evidence-backed knowledge atlas over an auditable compiler.**

This does not replace Capture -> Compile -> Reuse. It clarifies the Reuse surface:

- Capture creates traceable sources and deep dives.
- Compile creates objects, claims, relations, contradictions, summaries, and graph structure.
- Reuse should primarily feel like reading and exploring a knowledge atlas, not monitoring a pipeline.

## Reader Surface Principles

### 1. Knowledge Objects Are Pages

Every important object should be renderable as a readable page.

Minimum sections:

- title and kind
- short readable definition or introduction
- why this matters
- key claims or takeaways
- important relations
- source-backed evidence
- mentions / backlinks
- unresolved tensions or open questions
- next reads

Different object kinds need different page contracts:

| Kind | Reader-first page shape |
| --- | --- |
| Person | Who they are, why they matter, key ideas, relationships, quoted/evidenced mentions |
| Concept | Definition, role in the knowledge system, adjacent concepts, canonical claims, tensions |
| Company / tool / project | What it is, why it matters, capabilities, risks, related people/concepts |
| Event | What happened, timeline, involved objects, evidence, downstream implications |
| Claim | Statement, supporting evidence, opposing evidence, status, related claims |

### 2. Evidence Is A Reading Aid Before It Is An Audit Table

Evidence remains first-class, but the UI should not lead with database structure.

Reader flow:

1. readable synthesis
2. selected quotes and source snippets
3. expandable evidence trail
4. raw audit fields only when needed

### 3. Graph Is A Map, Not A Report

The graph page should make users feel the shape of the corpus.

Default graph behavior:

- full-screen or near-full-screen visual canvas
- type-colored nodes
- node size based on degree / evidence / reuse
- minimal count header
- search and type filters
- legend
- click node -> side panel with reader summary and jump links
- ops/debug graph cluster details remain available elsewhere

This is compatible with Phase 17's cluster-first / bounded graph strategy. The difference is the default product route:

- `/graph` should be a visual map
- `/clusters` can remain an analytical/debug route, likely under `/ops/clusters`

### 4. Operator Surfaces Move Behind `/ops`

The current OVP shell is valuable, but it should be framed as maintenance.

Move or reframe these as operator surfaces:

- runtime
- action worker
- run history
- signals
- actions
- candidates
- contradictions
- stale summaries
- review queues
- debug clusters

They should be easy to reach, but not the default product identity.

### 5. Backlinks Become Reading Context

LearnBuffett's strongest small pattern is the "linked to this page" rail. OVP should treat backlinks as a first-class reading context:

- where this object is mentioned
- source excerpt around the mention
- source type and date
- why this mention is meaningful
- "view source" and "open related object" links

This maps directly to existing evidence, page links, and capture summaries.

## Backlog Reconciliation

The older Knowledge System Execution Plan was right to avoid early UI overbuilding. At that time extraction visibility and truth modeling were the risky foundations.

That constraint has changed.

OVP now has enough truth/API/UI substrate that the next highest-leverage backlog slice can be presentation/product shape, not another backend-heavy subsystem.

The recent vault KSR backlog makes the same direction more concrete. The reader-first work should be treated as a projection/product slice that advances KSR and the older product-shell/graph milestones, especially:

| KSR ID | How reader-first product work uses it |
| --- | --- |
| KSR-002 Projection 标注 | Reader pages, dashboard, graph, MOC, briefing must show they are derived projections, not source of truth |
| KSR-015 Dashboard/search hot-path audit | The default home page and reader search must not trigger heavy raw/PDF/Office scans |
| KSR-026 Workflow wiring eval suite | Tests should lock projection labels, source lifecycle routing, dashboard hot paths, and read/write boundaries |
| KSR-001 Evidence span 化 | Object pages and backlink rails need precise source/evidence spans |
| KSR-003 Candidate 风险分层 | Reader pages should show unresolved/risky knowledge without implying it is canonical |
| KSR-014 Article routing preview | Reader-product ingestion should explain where a source will go before changing lifecycle behavior |

### Keep From Existing Roadmap

Keep these as architectural guardrails:

- markdown/canonical-vs-derived boundary
- `knowledge.db` as rebuildable derived store
- evidence-first promotion
- policy-gated review
- pack-owned semantics
- no new graph backend
- no silent agent mutation of accepted-state files
- no UI that hides provenance or review state

Keep these as product commitments:

- orientation brief
- compiled object/topic/event/contradiction sections
- graph exploration
- trusted reuse reporting
- writing prompts and open questions as reuse outputs

### Change The Order

Before pushing deeper into semantic extraction and query feedback, add a reader-facing entry layer that makes existing compiled knowledge understandable.

Recommended ordering:

1. **Reader Home / Knowledge Atlas**
   - `/` becomes a knowledge atlas entry product.
   - Existing operational dashboard moves to `/ops`.
   - Home answers: what is in this corpus, what is important, what changed, what should I read next.

2. **Object Pages v2**
   - Replace database-like object pages with kind-aware reader templates.
   - Use existing object detail, evidence, relations, backlinks, capture summaries, and compiled sections.
   - Person/concept/company pages should be the first three templates.

3. **Visual Graph MVP**
   - Add `/graph` as a reader-facing visual map.
   - Use current graph data; do not introduce a new backend.
   - Start bounded if necessary: top objects, selected pack, or cluster summary graph.
   - Keep debug cluster reports under `/ops/clusters`.

4. **Backlink / Mention Rail**
   - Add a right rail or section to object pages showing mentions with excerpts.
   - This should feel like LearnBuffett's source-linked reading context, not raw link rows.

5. **Reader-Oriented Search**
   - Search results group by object kind and reading intent.
   - Each result shows summary, kind, evidence count, and why it matched.

6. **Operator Shell Reframing**
   - Keep all current maintenance surfaces.
   - Rename or route them as ops/admin surfaces.
   - Avoid making runtime state the product's first impression.

## Near-Term Implementation Slice

Suggested first PR:

**Title:** `Add reader-first knowledge atlas entry surface`

Scope:

- add `/ops` route preserving the current dashboard
- make `/` render a new reader home from existing payloads
- add product navigation groups:
  - `Read`: Objects, Graph, Search, Deep Dives
  - `Understand`: Briefing, Atlas, Clusters
  - `Maintain`: Ops, Actions, Signals, Candidates, Contradictions
- update tests for root and `/ops`
- no new data model
- no graph visualization yet

Suggested second PR:

**Title:** `Render kind-aware object pages`

Scope:

- update object page view model with page kind contract
- add Person / Concept / Company / generic templates
- render evidence and backlinks as reading context
- keep raw audit/provenance collapsible

Suggested third PR:

**Title:** `Add visual graph map MVP`

Scope:

- new `/graph` route
- browser-rendered graph using current truth graph payload
- type legend, search/filter, zoom/recenter, click side panel
- bounded node count and graceful empty state
- keep `/clusters` for analytical route

## Backlog Status

| Item | Priority | Status | Notes |
| --- | --- | --- | --- |
| Reader home / Knowledge Atlas | P0 | proposed next | Highest leverage product-shape correction |
| Move current dashboard to `/ops` | P0 | proposed next | Preserves operator value while changing first impression |
| Kind-aware object pages | P0 | next | Turns extraction objects into user-readable pages |
| Mention/backlink rail | P1 | next | Direct LearnBuffett lesson; evidence-backed reading context |
| Visual `/graph` map | P1 | next | Use current graph data; no new backend |
| Reader-oriented search | P1 | later | Depends on object summaries and evidence counts |
| Trusted reuse loop | P1 | keep | Still core north-star measurement |
| Evidence v2 | P1 | keep | Still needed for long-term trust |
| Policy promotion | P2 | keep | Important, but after product entry is understandable |
| Reviewed semantic extractor | P2 | later | Do not add more graph complexity before graph is readable |
| Query feedback loop | P2 | later | Strong compounding loop, but depends on clearer reader surfaces |

## Non-Goals

- Do not clone LearnBuffett's brand or exact visual design.
- Do not remove operator surfaces.
- Do not make a hosted product.
- Do not introduce a graph database.
- Do not weaken evidence, review, or provenance requirements.
- Do not hide raw audit data; make it secondary instead of primary.

## Decision

The backlog should now treat "reader-first knowledge atlas" as the next product layer.

OVP should continue to be an auditable compiler internally, but the user-facing result should look more like a readable, navigable knowledge base:

- object pages that explain
- graph pages that orient
- backlinks that contextualize
- ops pages that maintain

This is the product bridge between the older knowledge-system roadmap and the current over-engineered dashboard feel.
