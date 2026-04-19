# OVP Knowledge Architecture Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a unified OVP knowledge architecture that absorbs the right lessons from Hyper-Extract, My-Brain-Is-Full-Crew, and llm_wiki without depending on any of them.

**Architecture:** Keep OVP's current layered pipeline and add three OVP-native subsystems: `Extraction Profiles`, `Knowledge Operations`, and `Compiled Wiki Views`. All three are additive and derived-first. Canonical identity, registry truth, evidence, and `knowledge.db` remain owned by OVP core.

**Tech Stack:** Python 3.10+, dataclasses/Pydantic-style typed specs, existing pack/plugin system, current registry/evidence/discovery/index layers, markdown files as durable content store, SQLite-backed `knowledge.db`.

## Executive Decision

OVP should not depend on any of the three external projects.

Instead:

1. Borrow `Hyper-Extract` for extraction template methodology.
2. Borrow `My-Brain-Is-Full-Crew` for knowledge operations and vault-governance patterns.
3. Borrow `llm_wiki` for persistent wiki-view ideas, source traceability, and query-to-knowledge promotion.
4. Reimplement all borrowed ideas inside OVP-native contracts.

## Final Architecture

### Canonical vs Derived

**Canonical**
- filesystem notes under the current vault layout
- concept registry / object registry truth
- pack-defined object kinds
- promoted evergreen and Atlas state

**Derived**
- extraction runs
- grounded spans and quotes
- review queues
- compiled wiki views
- retrieval indexes and graph summaries
- operational audits and health reports

Derived artifacts may influence canonical decisions, but they never mutate canonical state implicitly.

### Three New Subsystems

#### 1. Extraction Profiles

Purpose:
- turn documents into typed, grounded, mergeable derived records
- support domain-specific extraction without coupling OVP to one ontology

Output examples:
- `media/news_timeline`
- `media/commentary_sentiment`
- `tech/doc_structure`
- `tech/workflow_graph`

Contract:
- pack-scoped
- typed
- grounded
- deterministic merge
- projection-aware
- derived-only

#### 2. Knowledge Operations

Purpose:
- continuously maintain vault quality and editorial integrity
- manage review queues, frontmatter quality, orphan detection, bridge-note suggestions, and structure hygiene

Output examples:
- frontmatter audit report
- review queue item
- MOC/link-health recommendation
- taxonomy/structure warning

Contract:
- operational, not ontological
- mostly deterministic with bounded LLM assist
- safe to run repeatedly
- allowed to propose canonical changes, not perform them silently

#### 3. Compiled Wiki Views

Purpose:
- materialize stable, human-readable wiki outputs from canonical + derived state
- express knowledge-base intent explicitly through purpose/schema/view configuration

Output examples:
- domain overview pages
- topic brief pages
- saved-answer pages
- schema-aware overview pages

Contract:
- reader-facing and product-facing
- traceable back to sources
- rebuildable from canonical + derived inputs
- never treated as identity authority

## Borrowing Matrix

### Hyper-Extract

**Primary sources**
- `README.md`
- `docs/en/concepts/templates-format.md`
- `hyperextract/templates/presets/general/doc_structure.yaml`
- `hyperextract/templates/presets/general/workflow_graph.yaml`
- `hyperextract/templates/presets/finance/event_timeline.yaml`
- `hyperextract/templates/presets/finance/sentiment_model.yaml`
- `hyperextract/templates/presets/general/concept_graph.yaml`
- `hyperextract/templates/presets/industry/equipment_topology.yaml`

**Borrow deeply**
- template contract structure: `output`, `guideline`, `identifiers`, `display`, `options`
- task-first extraction design instead of one universal schema
- explicit identifier and merge policy
- choosing extraction output shape by task

**Borrow partially**
- specific template shapes as blueprints
- useful starting profiles:
  - `general/doc_structure.yaml` -> OVP `tech/doc_structure`
  - `general/workflow_graph.yaml` -> OVP `tech/workflow_graph`
  - `finance/event_timeline.yaml` -> OVP `media/news_timeline`
  - `finance/sentiment_model.yaml` -> OVP `media/commentary_sentiment`
  - `industry/equipment_topology.yaml` -> later OVP `tech/system_topology`

**Do not borrow**
- runtime API
- `AutoType` / `Method` implementation model
- direct YAML compatibility
- `Graph_RAG` / `Hyper_RAG` runtime behavior
- community detection as a first-wave requirement

**Degree of reuse**
- methodology: high
- template field shapes: medium to high
- runtime/design API: none

### My-Brain-Is-Full-Crew

**Primary sources**
- `agents/connector.md`
- `agents/librarian.md`
- `agents/sorter.md`
- `skills/vault-audit/SKILL.md`
- `skills/tag-garden/SKILL.md`
- `hooks/validate-frontmatter.sh`
- `orchestra/README.md`

**Borrow deeply**
- knowledge ops as first-class workflows
- structure/taxonomy source files
- frontmatter and metadata guardrails
- bridge-note / cluster / serendipity maintenance mindset

**Borrow partially**
- role decomposition ideas:
  - connector -> relationship/bridge recommendations
  - librarian -> audit and cleanup workflows
  - sorter -> intake triage
- persistent operational state idea, but implemented through OVP logs/derived artifacts instead of prompt memory files

**Do not borrow**
- prompt text as API
- Obsidian-agent orchestration model
- vault-wikilink topology as the only graph truth
- shell-hook-driven product architecture

**Degree of reuse**
- workflow patterns: medium
- implementation details: low
- prompt system: none

### llm_wiki

**Primary sources**
- `README.md`
- `doc/knowledge-graph-relevance-and-chat-retrieval.md`
- `src/lib/ingest.ts`
- `src/lib/search.ts`
- `src/lib/graph-relevance.ts`
- `src/lib/lint.ts`

**Borrow deeply**
- explicit `purpose.md` and `schema.md` idea
- `sources[]`-style traceability
- save-query-back-to-knowledge flow
- review-queue mindset for human approval
- persistent wiki compilation as a product surface

**Borrow partially**
- lightweight page-to-page relevance heuristics for small local views
- saved-answer and overview-page behaviors

**Do not borrow**
- current local search implementation as platform retrieval
- small-scale graph heuristics as the long-term retrieval core
- Tauri desktop app structure
- treating wiki pages as the only system contract

**Degree of reuse**
- product direction: medium
- retrieval implementation: low
- app architecture: none

## Specific Hyper-Extract Reuse Boundary

This is the exact boundary for Hyper-Extract.

### Reuse Level A: Conceptual Contract

OVP should copy the idea that an extraction task declares:
- what shape it emits
- what rules constrain extraction
- which fields identify duplicates
- how results should be shown or projected

OVP should not attempt to parse or execute Hyper-Extract templates directly.

### Reuse Level B: Manual Template Conversion

Manually convert a small set of template ideas into Python-native profile specs.

First wave:
- `general/doc_structure.yaml`
- `general/workflow_graph.yaml`
- `finance/event_timeline.yaml`
- `finance/sentiment_model.yaml`

Second wave:
- `general/concept_graph.yaml`
- `general/biography_graph.yaml`
- `industry/equipment_topology.yaml`
- `legal/defined_term_set.yaml`

### Reuse Level C: Controlled Field Translation

When converting a template:
- keep task intent
- keep identifier logic if it is domain-neutral
- keep useful field groupings
- rewrite fields that are too domain-opinionated
- map all outputs into OVP object kinds or derived artifact kinds deliberately

Example:
- Hyper `finance/event_timeline` should not be imported as finance schema
- OVP should reinterpret it as a neutral `event timeline` extraction profile with media-oriented fields such as `event_type`, `actors`, `when`, `where`, `claim`, `impact`, `evidence`

### Reuse Level D: Explicit Non-Reuse

Do not preserve:
- Hyper template file format
- Hyper class names
- Hyper runtime method registry
- Hyper merge internals
- Hyper graph post-processing pipeline

## OVP-Native Target Model

### Layer Map

Keep the existing six-layer model and add clearer contracts:

1. `Ingest`
   - raw acquisition
   - no ontology decisions
2. `Interpret`
   - single-document analysis and deep-dive generation
   - still not canonical truth
3. `Extract`
   - new derived extraction profile runtime
   - grounded, typed, document-scoped
4. `Absorb`
   - canonical integration into registry/object system
   - explicit decisions only
5. `Refine + Operations`
   - cleanup, breakdown, review queues, frontmatter health, MOC and structure maintenance
6. `Compiled Views + Discovery`
   - retrieval, evidence, query-to-wiki, overview views, saved answers

### New Core Contracts

#### ExtractionProfileSpec

Must declare:
- `name`
- `pack`
- `input_object_kinds`
- `output_mode`
- `fields`
- `relations`
- `grounding_policy`
- `identifier_fields`
- `merge_policy`
- `projection_target`
- `display_fields`
- `notes`

#### OperationProfileSpec

Must declare:
- `name`
- `pack`
- `scope`
- `triggers`
- `checks`
- `proposal_types`
- `auto_fix_policy`
- `review_required`

#### WikiViewSpec

Must declare:
- `name`
- `pack`
- `purpose_path`
- `schema_path`
- `input_sources`
- `builder`
- `traceability_policy`
- `publish_target`

## Implementation Order

The implementation order matters.

1. Extraction Profiles first.
   Reason: highest leverage and already aligned with existing pack architecture.
2. Knowledge Operations second.
   Reason: natural extension of current lint/cleanup/refine work.
3. Compiled Wiki Views third.
   Reason: product surface should build on the first two, not lead them.

## Task 1: Add the shared derived artifact foundation

**Files:**
- Modify: `src/ovp_pipeline/runtime.py`
- Create: `src/ovp_pipeline/derived/__init__.py`
- Create: `src/ovp_pipeline/derived/paths.py`
- Test: `tests/test_runtime_paths.py`
- Test: `tests/test_derived_paths.py`

**Step 1: Write the failing tests**

Add tests asserting:
- `VaultLayout` exposes stable subdirectories for derived extraction artifacts, review queues, and compiled views
- derived artifact paths live under `60-Logs` or another clearly derived-only subtree
- no canonical note directory is reused as a write target for derived artifacts

**Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_runtime_paths.py tests/test_derived_paths.py`
Expected: FAIL because the new derived path helpers do not exist.

**Step 3: Write minimal implementation**

Add new `VaultLayout` helpers such as:
- `derived_dir`
- `extraction_runs_dir`
- `review_queue_dir`
- `compiled_views_dir`

Add `derived/paths.py` helpers for deterministic artifact naming.

**Step 4: Run tests to verify they pass**

Run: `pytest -q tests/test_runtime_paths.py tests/test_derived_paths.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ovp_pipeline/runtime.py src/ovp_pipeline/derived/__init__.py src/ovp_pipeline/derived/paths.py tests/test_runtime_paths.py tests/test_derived_paths.py
git commit -m "feat: add derived artifact path foundation"
```

## Task 2: Add pack-level extension points for extraction, operations, and wiki views

**Files:**
- Modify: `src/ovp_pipeline/packs/base.py`
- Modify: `src/ovp_pipeline/packs/default_knowledge/pack.py`
- Create: `tests/test_pack_extension_points.py`

**Step 1: Write the failing test**

Add tests asserting:
- `BaseDomainPack` can expose `extraction_profiles()`
- `BaseDomainPack` can expose `operation_profiles()`
- `BaseDomainPack` can expose `wiki_views()`
- `default-knowledge` returns empty or seeded values through stable APIs

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_pack_extension_points.py`
Expected: FAIL because these extension points do not exist.

**Step 3: Write minimal implementation**

Extend `BaseDomainPack` with:
- `_extraction_profiles`
- `_operation_profiles`
- `_wiki_views`
- lookup methods for each

Wire the extension points into `default_knowledge.get_pack()`.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_pack_extension_points.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ovp_pipeline/packs/base.py src/ovp_pipeline/packs/default_knowledge/pack.py tests/test_pack_extension_points.py
git commit -m "feat: add pack extension points for derived subsystems"
```

## Task 3: Implement extraction profile core specs

**Files:**
- Create: `src/ovp_pipeline/extraction/__init__.py`
- Create: `src/ovp_pipeline/extraction/specs.py`
- Test: `tests/test_extraction_specs.py`

**Step 1: Write the failing test**

Add tests asserting that an `ExtractionProfileSpec` can declare:
- output mode
- fields and relations
- grounding policy
- identifier fields
- merge policy
- projection target
- display fields

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_extraction_specs.py`
Expected: FAIL with missing module or symbols.

**Step 3: Write minimal implementation**

Implement:
- `ExtractionFieldSpec`
- `ExtractionRelationSpec`
- `GroundingPolicy`
- `MergePolicy`
- `ProjectionTarget`
- `ExtractionProfileSpec`

Keep everything Python-native. Do not add YAML parsing.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_extraction_specs.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ovp_pipeline/extraction/__init__.py src/ovp_pipeline/extraction/specs.py tests/test_extraction_specs.py
git commit -m "feat: add extraction profile specs"
```

## Task 4: Seed the first four extraction profiles from controlled Hyper-Extract borrowing

**Files:**
- Create: `src/ovp_pipeline/packs/default_knowledge/extraction_profiles.py`
- Modify: `src/ovp_pipeline/packs/default_knowledge/pack.py`
- Test: `tests/test_default_pack_extraction_profiles.py`

**Step 1: Write the failing test**

Add tests asserting the default pack exposes these four profiles:
- `media/news_timeline`
- `media/commentary_sentiment`
- `tech/doc_structure`
- `tech/workflow_graph`

Also assert:
- each profile has identifier fields
- each profile declares projection targets
- each profile uses grounded output

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_default_pack_extraction_profiles.py`
Expected: FAIL because the profiles do not exist.

**Step 3: Write minimal implementation**

Create Python-defined profile specs derived from the external blueprints:
- `tech/doc_structure`: heavily inspired by Hyper `general/doc_structure`
- `tech/workflow_graph`: heavily inspired by Hyper `general/workflow_graph`
- `media/news_timeline`: moderately inspired by Hyper `finance/event_timeline`
- `media/commentary_sentiment`: moderately inspired by Hyper `finance/sentiment_model`

Important:
- copy only the task shape and useful field structure
- rename fields to OVP semantics
- avoid finance-only language where it does not fit

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_default_pack_extraction_profiles.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ovp_pipeline/packs/default_knowledge/extraction_profiles.py src/ovp_pipeline/packs/default_knowledge/pack.py tests/test_default_pack_extraction_profiles.py
git commit -m "feat: seed default extraction profiles"
```

## Task 5: Implement derived extraction runtime and deterministic merge

**Files:**
- Create: `src/ovp_pipeline/extraction/results.py`
- Create: `src/ovp_pipeline/extraction/runtime.py`
- Test: `tests/test_extraction_results.py`
- Test: `tests/test_extraction_runtime_merge.py`

**Step 1: Write the failing tests**

Add tests for:
- grounded spans with `source_path`, `section_title`, `char_start`, `char_end`, `quote`
- deterministic document-local merge by identifier fields
- runtime returning derived artifacts without mutating canonical registry

**Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_extraction_results.py tests/test_extraction_runtime_merge.py`
Expected: FAIL with missing runtime/results modules.

**Step 3: Write minimal implementation**

Implement:
- `ExtractionSpan`
- `ExtractionRecord`
- `ExtractionRelation`
- `ExtractionRunResult`
- chunking plus pluggable typed extraction interface
- deterministic merge
- artifact writing into derived directories

Do not integrate any external extraction library.

**Step 4: Run tests to verify they pass**

Run: `pytest -q tests/test_extraction_results.py tests/test_extraction_runtime_merge.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ovp_pipeline/extraction/results.py src/ovp_pipeline/extraction/runtime.py tests/test_extraction_results.py tests/test_extraction_runtime_merge.py
git commit -m "feat: add derived extraction runtime"
```

## Task 6: Add extraction evidence and projection hooks

**Files:**
- Modify: `src/ovp_pipeline/evidence.py`
- Modify: `src/ovp_pipeline/discovery.py`
- Create: `tests/test_evidence_extraction_channel.py`
- Create: `tests/test_discovery_extraction_projection.py`

**Step 1: Write the failing tests**

Add tests asserting:
- evidence payload can include `extraction_evidence`
- extraction evidence includes profile, object kind, source path, quote, offsets
- discovery can optionally read projected extraction artifacts
- projected extraction artifacts remain filtered by pack/object-kind rules

**Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_evidence_extraction_channel.py tests/test_discovery_extraction_projection.py`
Expected: FAIL because the extraction channel and projection hooks do not exist.

**Step 3: Write minimal implementation**

Extend evidence with a new channel:
- `channel: "extraction"`
- `profile`
- `projection_target`
- `object_kind`
- `source_path`
- `quote`
- `char_start`
- `char_end`

Add discovery integration only as an opt-in reader of derived artifacts.

**Step 4: Run tests to verify they pass**

Run: `pytest -q tests/test_evidence_extraction_channel.py tests/test_discovery_extraction_projection.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ovp_pipeline/evidence.py src/ovp_pipeline/discovery.py tests/test_evidence_extraction_channel.py tests/test_discovery_extraction_projection.py
git commit -m "feat: add extraction evidence and projection hooks"
```

## Task 7: Add extraction CLI for derived profile runs

**Files:**
- Create: `src/ovp_pipeline/commands/extract_profiles.py`
- Modify: `pyproject.toml`
- Test: `tests/test_extract_profiles_command.py`

**Step 1: Write the failing test**

Add a CLI test asserting:
- command accepts `--pack`
- command accepts `--profile`
- command reads a note or document
- command writes derived output
- command does not mutate canonical notes or registry

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_extract_profiles_command.py`
Expected: FAIL because the command does not exist.

**Step 3: Write minimal implementation**

Implement `ovp-extract` command that:
- loads pack
- resolves extraction profile
- runs the extraction runtime
- writes deterministic JSON artifacts under the derived extraction directory

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_extract_profiles_command.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ovp_pipeline/commands/extract_profiles.py pyproject.toml tests/test_extract_profiles_command.py
git commit -m "feat: add extraction profile command"
```

## Task 8: Implement knowledge-operations specs and seed first operations

**Files:**
- Create: `src/ovp_pipeline/operations/__init__.py`
- Create: `src/ovp_pipeline/operations/specs.py`
- Create: `src/ovp_pipeline/packs/default_knowledge/operation_profiles.py`
- Modify: `src/ovp_pipeline/packs/default_knowledge/pack.py`
- Test: `tests/test_operation_profiles.py`

**Step 1: Write the failing test**

Add tests asserting the default pack exposes:
- `vault/frontmatter_audit`
- `vault/review_queue`
- `vault/bridge_recommendations`

Add assertions for:
- scope
- trigger conditions
- proposal types
- whether review is mandatory

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_operation_profiles.py`
Expected: FAIL because operations specs do not exist.

**Step 3: Write minimal implementation**

Implement:
- `OperationCheckSpec`
- `OperationProposalSpec`
- `OperationProfileSpec`

Seed first operation profiles using controlled borrowing:
- `frontmatter_audit` from My-Brain frontmatter validation and vault-audit ideas
- `review_queue` from llm_wiki review queue idea
- `bridge_recommendations` from My-Brain connector/bridge-note idea

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_operation_profiles.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ovp_pipeline/operations/__init__.py src/ovp_pipeline/operations/specs.py src/ovp_pipeline/packs/default_knowledge/operation_profiles.py src/ovp_pipeline/packs/default_knowledge/pack.py tests/test_operation_profiles.py
git commit -m "feat: add operation profile specs and defaults"
```

## Task 9: Integrate knowledge operations into lint and review artifacts

**Files:**
- Modify: `src/ovp_pipeline/lint_checker.py`
- Create: `src/ovp_pipeline/operations/runtime.py`
- Create: `src/ovp_pipeline/commands/run_operations.py`
- Test: `tests/test_lint_operation_profiles.py`
- Test: `tests/test_review_queue_command.py`

**Step 1: Write the failing tests**

Add tests asserting:
- lint can emit structured frontmatter review proposals
- bridge recommendations are written as derived artifacts, not canonical note edits
- review queue command can list pending human-review items

**Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_lint_operation_profiles.py tests/test_review_queue_command.py`
Expected: FAIL because the new runtime and command do not exist.

**Step 3: Write minimal implementation**

Integrate operations with current health tooling:
- add a review-proposal output mode to `lint_checker.py`
- write operations artifacts into the derived review queue directory
- implement a simple `ovp-ops` command to execute one operation profile

Do not automatically modify notes in this task.

**Step 4: Run tests to verify they pass**

Run: `pytest -q tests/test_lint_operation_profiles.py tests/test_review_queue_command.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ovp_pipeline/lint_checker.py src/ovp_pipeline/operations/runtime.py src/ovp_pipeline/commands/run_operations.py tests/test_lint_operation_profiles.py tests/test_review_queue_command.py
git commit -m "feat: add knowledge operations runtime and review queue"
```

## Task 10: Implement compiled wiki-view specs and defaults

**Files:**
- Create: `src/ovp_pipeline/wiki_views/__init__.py`
- Create: `src/ovp_pipeline/wiki_views/specs.py`
- Create: `src/ovp_pipeline/packs/default_knowledge/wiki_views.py`
- Modify: `src/ovp_pipeline/packs/default_knowledge/pack.py`
- Test: `tests/test_wiki_view_specs.py`

**Step 1: Write the failing test**

Add tests asserting the default pack can expose:
- `overview/domain`
- `overview/topic`
- `saved_answer/query`

Each view must declare:
- purpose path
- schema path
- input sources
- publish target
- traceability policy

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_wiki_view_specs.py`
Expected: FAIL because wiki-view specs do not exist.

**Step 3: Write minimal implementation**

Implement:
- `TraceabilityPolicy`
- `WikiViewInputSpec`
- `WikiViewSpec`

Seed default wiki views inspired by llm_wiki:
- one overview-style view
- one topic-focused view
- one saved-answer view

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_wiki_view_specs.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ovp_pipeline/wiki_views/__init__.py src/ovp_pipeline/wiki_views/specs.py src/ovp_pipeline/packs/default_knowledge/wiki_views.py src/ovp_pipeline/packs/default_knowledge/pack.py tests/test_wiki_view_specs.py
git commit -m "feat: add compiled wiki view specs"
```

## Task 11: Add view compilation and query-to-wiki integration

**Files:**
- Modify: `src/ovp_pipeline/query_to_wiki.py`
- Create: `src/ovp_pipeline/wiki_views/runtime.py`
- Create: `src/ovp_pipeline/commands/build_views.py`
- Test: `tests/test_query_to_wiki_traceability.py`
- Test: `tests/test_build_views_command.py`

**Step 1: Write the failing tests**

Add tests asserting:
- saved answers can include explicit traceability metadata
- compiled views can be rebuilt from canonical + derived inputs
- build command writes into compiled view directories without mutating canonical identity unexpectedly

**Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_query_to_wiki_traceability.py tests/test_build_views_command.py`
Expected: FAIL because the runtime and command do not exist.

**Step 3: Write minimal implementation**

Implement:
- a builder that reads view specs and materializes markdown outputs
- traceability blocks in saved-answer pages
- optional integration where `query_to_wiki.py` can route output through a wiki-view builder

Do not replace the current evergreen write path yet; add the new path alongside it.

**Step 4: Run tests to verify they pass**

Run: `pytest -q tests/test_query_to_wiki_traceability.py tests/test_build_views_command.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ovp_pipeline/query_to_wiki.py src/ovp_pipeline/wiki_views/runtime.py src/ovp_pipeline/commands/build_views.py tests/test_query_to_wiki_traceability.py tests/test_build_views_command.py
git commit -m "feat: add compiled wiki view builder"
```

## Task 12: Document the borrowing boundaries and developer guidance

**Files:**
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/plans/2026-04-09-knowledge-architecture-implementation.md`
- Optional: `CONTRIBUTING.md`

**Step 1: Write the failing documentation checklist**

Create a checklist asserting docs explain:
- canonical vs derived boundary
- why OVP does not depend on Hyper-Extract, My-Brain-Is-Full-Crew, or llm_wiki
- which external ideas were borrowed and how
- where new commands write output

**Step 2: Verify current docs are insufficient**

Run: manual review against the checklist
Expected: FAIL because the architecture is not yet documented in developer-facing docs.

**Step 3: Write the documentation**

Update docs to explain:
- new subsystem boundaries
- first-wave profiles
- first-wave operations
- wiki-view purpose
- why direct external dependency is intentionally avoided

**Step 4: Verify docs are clear**

Run: manual review against the checklist
Expected: PASS

**Step 5: Commit**

```bash
git add README.md README_EN.md CONTRIBUTING.md docs/plans/2026-04-09-knowledge-architecture-implementation.md
git commit -m "docs: describe unified knowledge architecture"
```

## First-Wave Scope Lock

Do now:
- four extraction profiles
- three operation profiles
- three wiki view specs
- derived artifact storage
- evidence and traceability integration
- additive CLI commands

Do not do now:
- external template DSL parsing
- GraphRAG-style community summarization
- vector or graph database migration
- separate desktop app
- automatic canonical writes from extraction or operations
- large ontology redesign beyond current object-kind extension points

## Verification Standard

Before calling this architecture implemented:
- all new subsystems must have pack registration tests
- all new commands must have CLI tests
- no canonical mutation may occur without explicit tested code paths
- derived artifacts must be rebuildable and inspectable
- evidence payloads must include source-grounded support for derived outputs

## Why This Plan Is Correct

- It respects OVP's existing pack/plugin and registry direction.
- It borrows only the durable ideas from the three external projects.
- It avoids coupling the platform to unstable external APIs.
- It turns research conclusions into code boundaries, file boundaries, and test boundaries.

## External Source Links

### Hyper-Extract

- https://github.com/yifanfeng97/Hyper-Extract
- https://github.com/yifanfeng97/Hyper-Extract/blob/main/README.md
- https://github.com/yifanfeng97/Hyper-Extract/blob/main/docs/en/concepts/templates-format.md
- https://github.com/yifanfeng97/Hyper-Extract/blob/main/hyperextract/templates/presets/general/doc_structure.yaml
- https://github.com/yifanfeng97/Hyper-Extract/blob/main/hyperextract/templates/presets/general/workflow_graph.yaml
- https://github.com/yifanfeng97/Hyper-Extract/blob/main/hyperextract/templates/presets/finance/event_timeline.yaml
- https://github.com/yifanfeng97/Hyper-Extract/blob/main/hyperextract/templates/presets/finance/sentiment_model.yaml
- https://github.com/yifanfeng97/Hyper-Extract/blob/main/hyperextract/templates/presets/general/concept_graph.yaml
- https://github.com/yifanfeng97/Hyper-Extract/blob/main/hyperextract/templates/presets/industry/equipment_topology.yaml

### My-Brain-Is-Full-Crew

- https://github.com/gnekt/My-Brain-Is-Full-Crew
- https://github.com/gnekt/My-Brain-Is-Full-Crew/blob/main/agents/connector.md
- https://github.com/gnekt/My-Brain-Is-Full-Crew/blob/main/agents/librarian.md
- https://github.com/gnekt/My-Brain-Is-Full-Crew/blob/main/agents/sorter.md
- https://github.com/gnekt/My-Brain-Is-Full-Crew/blob/main/skills/vault-audit/SKILL.md
- https://github.com/gnekt/My-Brain-Is-Full-Crew/blob/main/skills/tag-garden/SKILL.md
- https://github.com/gnekt/My-Brain-Is-Full-Crew/blob/main/hooks/validate-frontmatter.sh
- https://github.com/gnekt/My-Brain-Is-Full-Crew/blob/main/orchestra/README.md

### llm_wiki

- https://github.com/nashsu/llm_wiki
- https://github.com/nashsu/llm_wiki/blob/main/README.md
- https://github.com/nashsu/llm_wiki/blob/main/doc/knowledge-graph-relevance-and-chat-retrieval.md
- https://github.com/nashsu/llm_wiki/blob/main/src/lib/ingest.ts
- https://github.com/nashsu/llm_wiki/blob/main/src/lib/search.ts
- https://github.com/nashsu/llm_wiki/blob/main/src/lib/graph-relevance.ts
- https://github.com/nashsu/llm_wiki/blob/main/src/lib/lint.ts
