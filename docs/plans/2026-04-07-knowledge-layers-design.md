# Knowledge Layers Design

## Goal

Define the final layer model for the OpenClaw knowledge pipeline before further implementation.

This design answers four questions:

1. What are the final system layers?
2. What is the contract of each layer?
3. Where should `absorb`, `cleanup`, and `breakdown` live?
4. Which decisions should be made by LLMs, and which must remain deterministic workflow steps?

The intended outcome is one stable architecture where:

- the filesystem remains the durable content store
- the registry slug remains the canonical concept identity
- graph / lint / Atlas stay derived or canonical consumers rather than competing truth systems
- LLM-driven knowledge work is explicit, auditable, and bounded

---

## Final Layer Model

The recommended model is **6 layers**, not more.

### Layer 1: Ingest

**Purpose**

Convert external inputs into normalized raw entries without performing semantic judgment.

**Inputs**

- URLs
- Pinboard bookmarks
- Clippings exports
- local markdown / plain text / imported files

**Outputs**

- `50-Inbox/01-Raw/*`
- `50-Inbox/02-Pinboard/*`
- attachments / sidecars / manifests

**Rules**

- mechanical and idempotent
- no concept creation
- no graph mutation
- do not infer knowledge structure

**Current modules**

- `auto_article_processor.py` (raw handling and sidecars)
- `auto_github_processor.py`
- `auto_paper_processor.py`
- `clippings_processor.py`
- `pinboard` steps in `unified_pipeline_enhanced.py`

**LLM usage**

- ideally none
- only permitted for bounded extraction where a deterministic parser is impossible, and the result must still land as raw material, not canonical knowledge

---

### Layer 2: Interpret

**Purpose**

Turn raw material into structured deep-dive notes.

**Inputs**

- normalized raw entries from Layer 1

**Outputs**

- `20-Areas/.../*_深度解读.md`

**Rules**

- LLM-heavy is acceptable
- output schema must be constrained
- success means a durable deep-dive file exists with parseable frontmatter and body
- this layer may classify content, but must not decide canonical concept truth

**Current modules**

- `auto_article_processor.py`
- `auto_github_processor.py`
- `auto_paper_processor.py`
- `batch_quality_checker.py` as a quality gate on interpret outputs

**LLM usage**

- primary
- but judgment remains local to a single document
- no global ontology decisions here

---

### Layer 3: Absorb

**Purpose**

Compile new deep-dive knowledge into the existing knowledge base.

This is the correct place for the Farzaa-style `absorb` concept.

**Inputs**

- deep-dive notes
- current Evergreen notes
- registry entries
- Atlas / MOC context

**Outputs**

- candidate concepts
- promoted active concepts
- enriched Evergreen content
- merge / alias proposals
- structured mutation records

**Rules**

- this layer performs the first real semantic integration step
- every decision must produce a structured result, not only text
- abstain is valid
- this layer may propose:
  - enrich existing page
  - create candidate
  - promote directly
  - merge as alias
- this layer must not directly mutate graph artifacts

**Current modules**

- `auto_evergreen_extractor.py`
- `promote_candidates.py`
- concept write-back parts of `query_to_wiki.py`

**What is missing today**

- absorb is still too “concept extraction oriented”
- it does not yet robustly distinguish:
  - enrich existing page
  - split into new page
  - candidate only
- it needs a first-class decision protocol

**LLM usage**

- allowed and central
- but only through explicit decision objects

**Required decision contract**

Every absorb action should emit something like:

```json
{
  "decision_type": "absorb_match",
  "source_note": "2026-04-07_xxx_深度解读.md",
  "subject_surface": "Promoted Concept",
  "action": "promote|candidate|enrich_existing|merge_alias|abstain",
  "target_slug": "promoted-concept",
  "confidence": 0.91,
  "reasons": ["exact surface match", "new evidence", "same concept"],
  "requires_review": false
}
```

---

### Layer 4: Refine

**Purpose**

Continuously improve the shape of the existing knowledge base.

This is where `cleanup` and `breakdown` belong.

**Inputs**

- Evergreen files
- Atlas / MOC files
- registry metadata
- graph and lint findings

**Outputs**

- rewritten Evergreen pages
- split proposals
- new candidate pages derived from oversized or mixed pages
- improved links and structure

**Rules**

- this layer is not about ingestion
- this layer is not about raw extraction
- it is editorial restructuring of existing knowledge
- every rewrite must preserve identity unless an explicit split/merge action is emitted

**Two sub-modes**

- `cleanup`
  - improve structure, coherence, linking, sectioning, and density
  - remove diary-driven or append-only growth
- `breakdown`
  - detect pages that should split into multiple knowledge objects
  - create structured split proposals or direct candidate pages

These are two commands, but one layer.

**Why this is one layer**

- both operate on existing wiki pages
- both depend on semantic editorial judgment
- both reshape knowledge, not ingest it

**LLM usage**

- allowed, often required
- but rewrites must be scoped and auditable

**Required decision contract**

Examples:

```json
{
  "decision_type": "split_decision",
  "source_slug": "agent-harness",
  "action": "split",
  "proposed_children": ["agent-harness-architecture", "agent-harness-usage-patterns"],
  "confidence": 0.88,
  "reasons": ["page mixes multiple stable subtopics", "line count too large"]
}
```

```json
{
  "decision_type": "rewrite_decision",
  "source_slug": "context-engineering",
  "action": "cleanup_rewrite",
  "scope": "theme_restructure",
  "confidence": 0.83
}
```

---

### Layer 5: Canonical

**Purpose**

Maintain the canonical truth model for knowledge identity and navigation.

**Inputs**

- accepted mutations from Absorb / Refine
- current filesystem state

**Outputs**

- `concept-registry.jsonl`
- `alias-index.json`
- `Atlas-Index.md`
- area MOCs
- candidate lifecycle state

**Rules**

- this layer should be as deterministic as possible
- it is the boundary where semantic proposals become official state
- no silent heuristics
- no hidden side effects

**Current modules**

- `concept_registry.py`
- `promote_candidates.py`
- `auto_moc_updater.py`
- `rebuild_registry.py`

**LLM usage**

- generally no
- only to evaluate human-like decisions before state transition, but final mutation application should be deterministic

**Canonical truth**

- concept identity: registry slug
- file durability: vault filesystem
- navigation truth: Atlas / MOC generated from canonical state

---

### Layer 6: Derived

**Purpose**

Produce read models, diagnostics, and visualizations from canonical state.

**Inputs**

- filesystem content
- canonical note identity
- registry / Atlas / MOC state

**Outputs**

- graph JSON
- daily delta
- lint reports
- migration reports
- quality reports

**Rules**

- read-only with respect to knowledge truth
- must not create canonical concepts
- may surface problems, never redefine identity

**Current modules**

- `graph/frontmatter.py`
- `graph/link_parser.py`
- `graph/graph_builder.py`
- `graph/daily_delta.py`
- `graph_cli.py`
- `lint_checker.py`
- `migrate_broken_links.py`

**LLM usage**

- optional only for repair suggestions or ranking
- not for base graph construction or identity resolution

---

## Where Absorb / Cleanup / Breakdown Belong

### Absorb

Belongs in **Layer 3: Absorb**, directly after deep-dive generation and before canonical state writes.

It should not live in:

- Ingest: too early, raw content is not yet interpreted
- Graph: too late, graph should consume accepted state
- Registry: too deterministic, absorb requires semantic judgment first

### Cleanup

Belongs in **Layer 4: Refine**.

It is an editorial maintenance action on existing knowledge pages.

### Breakdown

Also belongs in **Layer 4: Refine**.

It is a structural refactoring action derived from page shape and accumulated meaning.

### Conclusion

Do not add three new layers.

Instead:

- `absorb` becomes the core Layer 3 operation
- `cleanup` and `breakdown` become two modes of Layer 4

---

## Layer-to-Layer Contracts

### Contract A: Ingest -> Interpret

**Guarantee**

- every item has a durable source artifact
- metadata is preserved
- content remains reversible to the original source

**Interpret may assume**

- the source exists
- the raw entry is stable

**Interpret may not assume**

- any concept identity
- any registry membership

### Contract B: Interpret -> Absorb

**Guarantee**

- deep-dive note exists
- frontmatter is parseable
- date / source context are durable

**Absorb may assume**

- the note is a coherent interpretation unit

**Absorb may not assume**

- extracted concepts should all become pages
- title or filename equals canonical concept

### Contract C: Absorb -> Canonical

**Guarantee**

- every mutation request is structured
- every mutation names the affected slug or proposed slug
- abstain is explicit

**Canonical may assume**

- proposed state transitions are explicit

**Canonical may not assume**

- free-form prose is enough to mutate truth

### Contract D: Canonical -> Derived

**Guarantee**

- canonical ids are stable
- Atlas / MOC / registry are synchronized enough to serve as read models

**Derived may assume**

- canonical slug is the identity key

**Derived may not assume**

- raw filename alone is the identity

---

## LLM Decision Boundary

The system should explicitly separate **LLM semantic judgment** from **workflow state mutation**.

### Mode 1: In-context LLM judgment

Use this when:

- the scope is small
- the target is one document or one concept
- the result can be discarded or reviewed cheaply

Examples:

- concept extraction from one deep-dive
- deciding whether a paragraph enriches an existing Evergreen
- deciding whether a page is diary-driven

This mode is skill-like and fast.

### Mode 2: Workflow-mediated LLM judgment

Use this when:

- the scope is large
- the action changes canonical truth
- the work must be resumable, auditable, and idempotent

Examples:

- absorb 200 deep-dives
- cleanup a whole area
- breakdown oversized pages across the vault

This mode is the stable production mode.

### Rule

If a decision can mutate registry, aliases, Atlas, MOC, or canonical Evergreen identity, it should go through the workflow mode.

---

## Major Decision Types

The following decision types should become explicit structured objects in future implementation:

- `absorb_match`
  - enrich existing
  - create candidate
  - promote direct
  - merge alias
  - abstain

- `merge_decision`
  - same concept
  - alias
  - near duplicate but keep separate

- `split_decision`
  - keep single page
  - split into children
  - candidate child pages

- `rewrite_decision`
  - no change
  - cleanup
  - full restructure

- `link_decision`
  - safe link
  - unsafe, abstain
  - alias redirect

These decisions should be serializable and testable.

---

## Current Module Mapping

### Mostly correct today

- `unified_pipeline_enhanced.py`
  - orchestration across layers, though still too operational and not yet centered on absorb/refine concepts

- `concept_registry.py`
  - canonical identity layer

- `graph/*`
  - derived layer

- `auto_moc_updater.py`
  - canonical navigation writer

### Needs reframing

- `auto_evergreen_extractor.py`
  - currently mixes extraction, candidate lifecycle, and partial promote logic
  - should become a Layer 3 absorb worker, not just “extract concepts”

- `query_to_wiki.py`
  - should be treated as a single-item absorb entrypoint, not a separate mini-pipeline

- `query_tool.py`
  - should remain read/query, but if it writes back, it should call absorb/canonical contracts explicitly

- `lint_checker.py`
  - belongs to Derived, but should increasingly validate against Canonical contracts rather than filesystem guesses

---

## Recommended Next Implementation Order

### Step 1

Refactor `auto_evergreen_extractor.py` into an explicit Layer 3 absorb worker.

### Step 2

Add a structured absorb decision schema and make `query_to_wiki.py` call the same contract.

### Step 3

Introduce Layer 4 commands:

- `ovp-cleanup`
- `ovp-breakdown`

Both should emit structured proposals before state mutation.

### Step 4

Move `promote/merge/reject` to consume those structured proposals instead of ad-hoc command arguments only.

### Step 5

Make orchestration in `unified_pipeline_enhanced.py` reflect the real layer order:

- ingest
- interpret
- absorb
- canonical
- derived

Refine should run separately or as a maintenance mode, not automatically on every ingest.

---

## Final Recommendation

The system should not become “Farzaa wiki skill plus more scripts.”

It should become:

- a stable automation pipeline at the bottom
- a writer/editor knowledge model in the middle
- deterministic canonical and derived views on top

That means:

- Farzaa’s strongest ideas belong in Layer 3 and Layer 4
- our current strengths remain in Layer 5 and Layer 6
- the architecture should keep semantic judgment and state mutation separate

This is the cleanest path to a system that is both:

- intellectually useful like a real personal wiki compiler
- operationally reliable like a production pipeline
