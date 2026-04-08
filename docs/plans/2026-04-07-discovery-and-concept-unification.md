# Discovery And Concept Unification Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Unify search, concept discovery, and LLM retrieval context so the system has one default discovery stack, one canonical identity resolver, and one evidence contract for downstream model decisions.

**Architecture:** Keep canonical identity resolution deterministic and registry-first. Move default discovery to `knowledge.db` (FTS + embeddings), demote QMD to an optional external discovery adapter, and expose a shared evidence schema with separate identity, retrieval, graph, and audit channels for LLM workflows.

**Tech Stack:** Python CLI, SQLite FTS5, local deterministic embeddings, optional QMD adapter, pytest.

---

## Why This Plan Exists

The repository currently has three overlapping retrieval systems:

1. `ConceptRegistry.resolve_mention()` and `search()` for deterministic registry resolution.
2. `query_tool.py`, which prefers `qmd search` and otherwise falls back to a weak built-in lexical scorer.
3. `knowledge_index.py`, which already implements local FTS5 BM25 retrieval plus deterministic chunk embeddings in `knowledge.db`.

These systems are not aligned:

- automatic link resolution correctly avoids semantic retrieval
- user-facing discovery still routes through QMD or a legacy fallback
- LLM workflows receive mixed result types without a stable evidence schema

The result is inconsistent ranking, duplicated logic, and avoidable drift between search, concept discovery, and downstream LLM reasoning.

---

## Target State

### 1. Canonical Resolution

**Single rule:** automatic mention resolution remains registry-first and deterministic.

- `ConceptRegistry.resolve_mention()` stays authoritative
- QMD and vector similarity never auto-link notes
- abstain remains valid behavior

### 2. Default Discovery

**Single default runtime:** `knowledge.db`

- lexical discovery: `search_knowledge_index()`
- semantic discovery: `query_knowledge_index()`
- future hybrid discovery: combine the two in one local reranker

QMD becomes an explicit optional engine, not the default.

### 3. Concept Discovery

There should be one shared “related concept discovery” entry point used by:

- candidate generation
- ambiguous concept review
- surface conflict review
- query-time “related notes”
- refine-time context expansion

This entry point should return typed evidence, not ad hoc result lists.

### 4. LLM Evidence Schema

LLM-facing workflows should receive four separate evidence buckets:

- `identity_evidence`: registry matches / abstains / ambiguities
- `retrieval_evidence`: knowledge.db lexical/semantic results
- `graph_evidence`: graph neighbors / Atlas / MOC context
- `audit_evidence`: recency, mutations, pipeline or refine events

No workflow should infer identity from retrieval alone.

---

## Recommended Product Decisions

### A. Usage Scenarios

| Scenario | Default engine | QMD allowed? | Notes |
|---|---|---|---|
| Auto-linking / note resolution | Registry only | No | Deterministic only |
| Candidate creation | Registry + discovery helper | Yes, as auxiliary only | Retrieval may suggest related context |
| Query / search | knowledge.db | Yes, explicit engine switch only | knowledge.db should be the platform default |
| Conflict review / duplicate review | Registry + discovery helper | Yes | QMD can remain a reviewer signal |
| Refine (`cleanup` / `breakdown`) | knowledge.db + graph | Yes, optional | Retrieval should inform rewrite/split context |

### B. Knowledge Discovery

`ovp-query` should no longer prefer QMD automatically.

Default behavior:

- `ovp-query --engine knowledge` or no engine flag: use `knowledge.db`
- `ovp-query --engine qmd`: use QMD explicitly
- if `knowledge.db` missing: rebuild on demand
- if QMD missing and explicitly requested: fail clearly, do not silently change ranking semantics

### C. LLM Handling

Major LLM workflows must consume structured evidence rather than flat search results.

Examples:

- `absorb`: identity evidence first, then retrieval evidence for enrichment context
- `promote/merge/reject`: identity + retrieval + graph evidence
- `cleanup/breakdown`: retrieval + graph + audit evidence
- `query`: retrieval + graph evidence, optionally identity hits if concept mentions are recognized

---

## Concrete Refactor Plan

### Task 1: Introduce a unified discovery contract

**Files:**
- Create: `src/openclaw_pipeline/discovery.py`
- Modify: `src/openclaw_pipeline/knowledge_index.py`
- Test: `tests/test_discovery.py`

**Step 1: Write the failing test**

Add tests for:

- `discover_related()` returning typed results from `knowledge.db`
- stable result shape with `engine`, `kind`, `slug`, `title`, `score`, `snippet`
- default engine being `knowledge`

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_discovery.py`

Expected: missing module / helper failures.

**Step 3: Write minimal implementation**

Create a small discovery facade:

- `discover_related(vault_dir, query, *, engine="knowledge", limit=10)`
- `discover_identity_context(registry, mention)`
- `discover_query_context(vault_dir, query, *, limit=10)`

Back it with:

- `search_knowledge_index()`
- `query_knowledge_index()`
- optional `qmd` adapter

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_discovery.py`

Expected: PASS

### Task 2: Make `ovp-query` knowledge.db-first

**Files:**
- Modify: `src/openclaw_pipeline/query_tool.py`
- Test: `tests/test_query_tool.py`

**Step 1: Write the failing test**

Add tests asserting:

- default search engine uses `knowledge.db`
- `--engine qmd` is explicit
- missing QMD does not silently override an explicit `qmd` request

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_query_tool.py`

Expected: default engine behavior mismatch.

**Step 3: Write minimal implementation**

- add `--engine {knowledge,qmd}` if not already present
- default to `knowledge`
- use discovery helpers instead of direct `qmd` subprocess fallback
- only use the old builtin lexical scorer as a final internal emergency fallback, or remove it entirely

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_query_tool.py`

Expected: PASS

### Task 3: Unify concept discovery hooks

**Files:**
- Modify: `src/openclaw_pipeline/concept_registry.py`
- Modify: `src/openclaw_pipeline/concept_resolver.py`
- Test: `tests/test_concept_discovery.py`

**Step 1: Write the failing test**

Add tests asserting:

- candidate creation receives typed related context from the discovery facade
- `fix_surface_conflicts()` uses discovery evidence only for review signals, not as the sole automatic merge trigger
- registry `search()` remains lexical/deterministic for compatibility

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_concept_discovery.py`

Expected: current QMD-only auxiliary behavior mismatch.

**Step 3: Write minimal implementation**

- replace direct `_qmd_related_context()` calls in candidate/review paths with a shared discovery helper
- keep `_qmd_related_context()` only as a provider implementation, not the public concept-discovery API
- revise conflict analysis so QMD/semantic similarity contributes to `review_needed`, while automatic merge still requires deterministic overlap or explicit review execution

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_concept_discovery.py`

Expected: PASS

### Task 4: Add LLM evidence schema

**Files:**
- Create: `src/openclaw_pipeline/evidence.py`
- Modify: `src/openclaw_pipeline/commands/absorb.py`
- Modify: `src/openclaw_pipeline/refine.py`
- Modify: `src/openclaw_pipeline/query_tool.py`
- Test: `tests/test_evidence_schema.py`

**Step 1: Write the failing test**

Add tests asserting:

- evidence payloads contain separate `identity_evidence`, `retrieval_evidence`, `graph_evidence`, `audit_evidence`
- no retrieval-only payload is labeled as identity

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_evidence_schema.py`

Expected: missing schema failures.

**Step 3: Write minimal implementation**

Create helpers such as:

- `build_identity_evidence()`
- `build_retrieval_evidence()`
- `build_graph_evidence()`
- `build_audit_evidence()`

Use them where structured LLM-facing payloads are assembled.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_evidence_schema.py`

Expected: PASS

### Task 5: Rationalize docs and CLI guidance

**Files:**
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `CLAUDE.md`
- Modify: `skills/daily-ingestion.md`

**Step 1: Write the failing doc checklist**

Create a checklist:

- QMD is documented as optional, not default
- `knowledge.db` is documented as the default retrieval layer
- registry remains canonical for identity
- concept discovery is documented as distinct from automatic linking

**Step 2: Update docs**

- remove stale wording that suggests “use qmd by default”
- explain explicit `--engine qmd`
- explain the new discovery contract

**Step 3: Verify docs and help**

Run:

- `ovp-query --help`
- `ovp-knowledge-index --help`
- `rg -n "qmd|knowledge.db|source of truth|engine" README.md README_EN.md CLAUDE.md skills/daily-ingestion.md`

Expected: docs align with the new contract.

### Task 6: Full verification

**Files:**
- Modify: tests as needed

**Step 1: Run full verification**

Run:

- `python3 -m compileall src/openclaw_pipeline`
- `pytest -q`

Expected: all pass.

**Step 2: Commit**

```bash
git add src/openclaw_pipeline/discovery.py src/openclaw_pipeline/evidence.py src/openclaw_pipeline/query_tool.py src/openclaw_pipeline/concept_registry.py src/openclaw_pipeline/concept_resolver.py tests/test_discovery.py tests/test_query_tool.py tests/test_concept_discovery.py tests/test_evidence_schema.py README.md README_EN.md CLAUDE.md skills/daily-ingestion.md
git commit -m "feat: unify discovery and concept retrieval contracts"
```

---

## Recommended Implementation Order

1. Discovery facade
2. `ovp-query` default engine change
3. Concept discovery hook unification
4. LLM evidence schema
5. Docs
6. Full verification

---

## Non-Goals

- Do not let QMD decide automatic wikilinks
- Do not make `knowledge.db` a second source of truth
- Do not replace registry lexical search with semantic matching
- Do not add freeform LLM rewriting to the resolver
