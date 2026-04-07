# GBrain Fusion Design

## Goal

Adopt the strongest parts of the GBrain design without breaking the current OpenClaw pipeline's source-of-truth guarantees.

This design answers five concrete questions:

1. Which parts of GBrain should be adopted now?
2. Which parts should explicitly *not* be adopted yet?
3. Where does a local SQLite knowledge index fit in the existing 6-layer model?
4. How does it avoid becoming a second canonical truth source?
5. What is the safe implementation order inside the current repository?

---

## Executive Decision

The recommended path is:

- keep **vault markdown + concept registry** as the canonical truth
- add a new local **knowledge.db** as a **derived/index layer**
- use the database to unify:
  - FTS-style search
  - embeddings / semantic retrieval
  - structured links
  - raw sidecar payloads
  - timeline/event rows
  - ingest/refine audit rows
- do **not** let SQLite write canonical pages directly in v1

In short:

> GBrain's data model is worth adopting.
> GBrain's source-of-truth model is **not** worth adopting yet.

---

## What We Keep

These remain the canonical truth model:

- filesystem markdown under the vault
- `note_id` / registry slug as canonical identity
- `concept-registry.jsonl`
- `alias-index.json`
- canonical Atlas generation from registry

These remain derived/consumer layers:

- graph
- daily delta
- lint
- reporting

This means the current principle still holds:

> If markdown and the registry disagree, the system must reconcile toward the canonical filesystem+registry state.

---

## What We Borrow From GBrain

### 1. Unified local data store

Borrow:

- single-file SQLite database
- no server
- local-only operation
- MCP-friendly access model

But use it as:

- a local cache/index
- a query acceleration layer
- an audit/event layer

Not as:

- the page authoring truth
- the canonical write target for ingest or refine

### 2. Structured sidecar storage

Borrow GBrain's `raw_data` idea almost directly.

Current pain in OpenClaw:

- sidecars and logs are split across filesystem patterns
- enrichment and ingest metadata are hard to query uniformly
- some facts live in page frontmatter, some in pipeline logs, some in ad hoc JSON

Recommendation:

- add structured `raw_data` rows keyed by canonical slug
- persist fetched source payloads by source name
- keep the original raw JSON blobs intact

### 3. Structured timeline / event rows

Borrow GBrain's `timeline_entries` idea, but do **not** replace markdown timeline content.

Recommendation:

- parse timeline-like sections and ingest/refine events into normalized rows
- use these rows for search, briefings, and maintenance
- keep markdown rendering/export as the outward-facing artifact

### 4. Ingest / refine audit table

Borrow GBrain's `ingest_log` spirit.

We already have:

- `pipeline.jsonl`
- `refine-mutations.jsonl`

Recommendation:

- mirror these logs into structured SQLite rows
- preserve JSONL for human inspection and compatibility
- treat SQLite as the queryable index, not the only audit record

### 5. MCP-first knowledge access

Borrow the GBrain principle that the knowledge layer should become an MCP-accessible subsystem.

Recommendation:

- expose search/query/get-stats style tools from the derived DB
- keep write tools conservative in v1
- route canonical writes through existing pipeline commands, not raw DB mutation

---

## What We Explicitly Do Not Borrow Yet

### 1. SQLite as canonical page store

Do **not** move canonical pages into a `pages` table in v1.

Why:

- it would create a new truth source immediately
- export/import parity would become a hard requirement before the system is ready
- Obsidian compatibility would degrade during the transition
- the current refactor just stabilized identity contracts on the markdown side

### 2. Database-native page editing

Do **not** let `put`-style DB writes become the primary authoring path.

All canonical writes should still go through:

- markdown file mutation
- registry mutation
- Atlas regeneration
- derived refresh

### 3. Replacing current resolver logic with semantic search

Do **not** use embeddings to resolve canonical links automatically.

This would violate the current resolver contract in `concept_registry.py`:

- semantic search is for retrieval
- deterministic surface matching is for canonical resolution
- abstain remains the correct answer when exact resolution is unsafe

### 4. Full GBrain repo model

Do **not** pivot the repo into:

- Bun runtime
- new CLI from scratch
- markdown-as-import-export-only

That would be a new product, not a safe evolution of this one.

---

## Placement In The Current 6-Layer Model

The SQLite knowledge index belongs between **Canonical** and **Derived**.

### Updated interpretation of layers

1. `Ingest`
   - raw capture
   - no semantic global truth

2. `Interpret`
   - deep-dive generation

3. `Absorb`
   - concept lifecycle decisions
   - structured mutation output

4. `Refine`
   - cleanup / breakdown decisions
   - controlled canonical writes

5. `Canonical`
   - markdown + registry + Atlas/MOC canonical state

6. `Derived`
   - graph
   - lint
   - daily delta
   - reports
   - **knowledge.db**

That last point is deliberate:

> `knowledge.db` is not Layer 5. It is a Layer 6 artifact built *from* Layer 5.

---

## Proposed SQLite Scope

The first version should be intentionally smaller than GBrain.

### Table set for v1

#### `pages_index`

Purpose:

- searchable metadata cache for canonical pages

Columns:

- `slug`
- `title`
- `note_type`
- `path`
- `day_id`
- `frontmatter_json`
- `compiled_body`
- `derived_from`
- `updated_at`

Notes:

- one row per canonical markdown page
- regenerated from vault scan

#### `page_fts`

Purpose:

- FTS5 search over titles and content

Source:

- generated from `pages_index`

#### `page_embeddings`

Purpose:

- semantic retrieval over page chunks

Source:

- derived from canonical page content

Rules:

- never used as the automatic link resolver

#### `page_links`

Purpose:

- structured link table keyed by canonical slug

Source:

- derived from existing parser + registry-aware resolution

#### `raw_data`

Purpose:

- normalized store for enrichment payloads and imported sidecars

Key:

- `(slug, source_name)`

#### `timeline_events`

Purpose:

- normalized timeline/event rows

Sources:

- parsed page timeline sections
- ingest/refine lifecycle events

#### `audit_events`

Purpose:

- mirror of pipeline/refine lifecycle logs

Sources:

- `pipeline.jsonl`
- `refine-mutations.jsonl`
- future absorb decision logs

---

## Source-of-Truth Guardrails

The project must enforce these rules in code and docs.

### Rule 1

`knowledge.db` may be deleted and rebuilt without data loss.

If deleting it loses knowledge, the architecture is wrong.

### Rule 2

Canonical writes happen before derived refresh.

The order must stay:

1. mutate markdown / registry
2. refresh Atlas / canonical projections
3. refresh `knowledge.db`
4. refresh graph / lint / reports

### Rule 3

Semantic retrieval never replaces deterministic identity resolution.

This means:

- registry resolves identity
- embeddings retrieve context
- they do different jobs

### Rule 4

`knowledge.db` tools may read freely but must write conservatively.

Safe write classes in v1:

- rebuild index
- rebuild embeddings
- rebuild audit rows

Unsafe and forbidden in v1:

- direct canonical page edits from DB rows
- direct alias/slug edits through DB tools

---

## Impact On Existing Modules

### `concept_registry.py`

Keep as-is for canonical resolution.

Future integration:

- export active/candidate entries into `pages_index` / supporting tables
- use registry slug as the DB primary identity

### `graph/*`

Future integration:

- allow graph builder to optionally read from `knowledge.db` for indexed page/link scans
- keep graph node identity based on canonical slug

### `auto_article_processor.py`

Future integration:

- write raw ingest payload references into `raw_data`
- append ingest audit rows

### `auto_evergreen_extractor.py`

Future integration:

- write absorb decision records into `audit_events`
- optionally trigger DB refresh for touched slugs

### `refine.py`

Future integration:

- mirror refine mutations into `audit_events`
- emit affected slugs for selective DB refresh

### `unified_pipeline_enhanced.py`

Future integration:

- add a dedicated derived refresh step for `knowledge.db`
- place it after canonical refresh and before graph/lint

### `autopilot/daemon.py`

Future integration:

- after absorb and MOC/Atlas refresh, trigger DB partial refresh
- do not run cleanup/breakdown inline by default

Reason:

- refine is still editorially riskier than absorb
- autopilot should consume it only after a later policy decision

---

## Recommended Implementation Order

### Phase 1: Add the derived DB skeleton

Goal:

- introduce `knowledge.db` without changing any user-facing behavior

Tasks:

1. add a runtime path for `knowledge.db`
2. add schema initialization
3. add a builder that scans canonical pages and populates:
   - `pages_index`
   - `page_fts`
   - `page_links`
4. add tests proving full rebuild is lossless at the index layer

### Phase 2: Add audit/event mirroring

Goal:

- unify pipeline/refine event querying

Tasks:

1. mirror `pipeline.jsonl`
2. mirror `refine-mutations.jsonl`
3. add structured event query helpers

### Phase 3: Add embeddings

Goal:

- semantic retrieval over canonical content

Tasks:

1. chunk canonical pages
2. store embeddings keyed by slug + chunk index
3. add query helpers
4. explicitly keep this out of auto link resolution

### Phase 4: Add MCP read tools

Goal:

- make the knowledge layer queryable by external agents

Initial tools:

- `knowledge_search`
- `knowledge_query`
- `knowledge_get`
- `knowledge_stats`
- `knowledge_audit_recent`

### Phase 5: Wire main pipeline to rebuild the derived DB

Goal:

- make the new index part of the real daily pipeline

Tasks:

1. `ovp --full` triggers derived refresh after registry/Atlas sync
2. graph/lint may consume DB-backed helpers where useful
3. autopilot triggers partial refresh after successful absorb

---

## Non-Goals For The Next Iteration

These should stay out of scope:

- replacing Obsidian with DB-native authoring
- replatforming to Bun
- replacing the current CLI surface wholesale
- using semantic search for canonical link resolution
- auto-running cleanup/breakdown inside autopilot

---

## Final Recommendation

Adopt GBrain as a **data-model influence**, not as a full product transplant.

The right merge is:

- OpenClaw keeps the workflow and canonical file model
- GBrain contributes the local DB indexing model
- MCP becomes the access surface for the derived knowledge layer

That gives the project the main upside of GBrain:

- scalable local search
- structured auditability
- semantic retrieval
- cleaner data access for agents

without reintroducing the exact problem we just spent time removing:

- multiple competing truth systems
