# Extraction Profile Architecture Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an OVP-native extraction profile layer for media and tech domains without depending on external extraction frameworks.

**Architecture:** Add a new profile layer between pack metadata and downstream canonical knowledge flows. Profiles will declare typed extraction tasks, grounding policy, identifiers, merge policy, and projection targets. Runtime execution remains derived-only: extract into sidecars / derived rows first, then let existing absorb, registry, evidence, and knowledge-index layers consume that data deliberately.

**Tech Stack:** Python 3.11+, dataclasses/Pydantic, existing OVP pack/plugin system, existing evidence and discovery layers, current LLM client integration.

### Task 1: Define extraction profile core types

**Files:**
- Create: `src/ovp_pipeline/extraction/__init__.py`
- Create: `src/ovp_pipeline/extraction/specs.py`
- Test: `tests/test_extraction_specs.py`

**Step 1: Write the failing test**

Add tests asserting that a profile can declare:
- `name`
- `input_object_kinds`
- `output_mode`
- `schema`
- `grounding_policy`
- `identifier_fields`
- `merge_policy`
- `projection_target`

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_extraction_specs.py`
Expected: FAIL with missing module or symbols.

**Step 3: Write minimal implementation**

Implement core types in `src/ovp_pipeline/extraction/specs.py`:
- `ExtractionFieldSpec`
- `ExtractionRelationSpec`
- `GroundingPolicy`
- `MergePolicy`
- `ProjectionTarget`
- `ExtractionProfileSpec`

Use Python-native typed specs first. Do not add YAML parsing yet.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_extraction_specs.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ovp_pipeline/extraction/__init__.py src/ovp_pipeline/extraction/specs.py tests/test_extraction_specs.py
git commit -m "feat: add extraction profile core specs"
```

### Task 2: Extend pack API to expose extraction profiles

**Files:**
- Modify: `src/ovp_pipeline/packs/base.py`
- Modify: `src/ovp_pipeline/packs/default_knowledge/pack.py`
- Create: `src/ovp_pipeline/packs/default_knowledge/extraction_profiles.py`
- Test: `tests/test_pack_extraction_profiles.py`

**Step 1: Write the failing test**

Add tests asserting:
- `BaseDomainPack` can return registered extraction profiles
- `default-knowledge` exposes the first four profiles:
  - `media/news_timeline`
  - `media/commentary_sentiment`
  - `tech/doc_structure`
  - `tech/workflow_graph`

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_pack_extraction_profiles.py`
Expected: FAIL because packs do not expose extraction profiles.

**Step 3: Write minimal implementation**

Modify `src/ovp_pipeline/packs/base.py` to add:
- `_extraction_profiles: list[ExtractionProfileSpec]`
- `extraction_profiles()`
- `extraction_profile(name: str)`

Create `src/ovp_pipeline/packs/default_knowledge/extraction_profiles.py` with Python-defined profile specs, not prompts yet. Focus on shape and metadata.

Wire these profiles into `src/ovp_pipeline/packs/default_knowledge/pack.py`.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_pack_extraction_profiles.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ovp_pipeline/packs/base.py src/ovp_pipeline/packs/default_knowledge/pack.py src/ovp_pipeline/packs/default_knowledge/extraction_profiles.py tests/test_pack_extraction_profiles.py
git commit -m "feat: register extraction profiles in domain packs"
```

### Task 3: Add derived extraction result and grounding model

**Files:**
- Create: `src/ovp_pipeline/extraction/results.py`
- Create: `src/ovp_pipeline/extraction/runtime.py`
- Test: `tests/test_extraction_results.py`
- Test: `tests/test_extraction_runtime_merge.py`

**Step 1: Write the failing test**

Add tests for:
- grounded extraction item with `source_path`, `section_title`, `char_start`, `char_end`, `quote`
- deterministic document-local merge by identifier fields
- runtime returning derived extraction results without mutating canonical registry

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_extraction_results.py tests/test_extraction_runtime_merge.py`
Expected: FAIL with missing result/runtime modules.

**Step 3: Write minimal implementation**

Create:
- `ExtractionSpan`
- `ExtractionRecord`
- `ExtractionRelation`
- `ExtractionRunResult`

In `runtime.py`, implement only:
- chunk input text
- call a pluggable typed extractor interface
- attach grounding metadata
- merge results deterministically by configured identifier fields

Do not integrate any external framework here.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_extraction_results.py tests/test_extraction_runtime_merge.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ovp_pipeline/extraction/results.py src/ovp_pipeline/extraction/runtime.py tests/test_extraction_results.py tests/test_extraction_runtime_merge.py
git commit -m "feat: add derived extraction result runtime"
```

### Task 4: Add evidence channel for extraction outputs

**Files:**
- Modify: `src/ovp_pipeline/evidence.py`
- Test: `tests/test_evidence_extraction_channel.py`

**Step 1: Write the failing test**

Add tests asserting `build_evidence_payload()` can optionally include:
- `extraction_evidence`
- grounded snippets / spans
- profile name and projection target

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_evidence_extraction_channel.py`
Expected: FAIL because extraction evidence channel does not exist.

**Step 3: Write minimal implementation**

Extend `src/ovp_pipeline/evidence.py` with a new derived evidence channel:
- `channel: "extraction"`
- `profile`
- `object_kind`
- `source_path`
- `quote`
- `char_start`
- `char_end`

This should read only derived extraction artifacts. It must not write canonical state.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_evidence_extraction_channel.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ovp_pipeline/evidence.py tests/test_evidence_extraction_channel.py
git commit -m "feat: add extraction evidence channel"
```

### Task 5: Add a pack-aware extraction command for derived artifacts

**Files:**
- Create: `src/ovp_pipeline/commands/extract_profiles.py`
- Modify: `pyproject.toml`
- Test: `tests/test_extract_profiles_command.py`

**Step 1: Write the failing test**

Add a CLI test asserting:
- command accepts `--pack`
- command accepts `--profile`
- command reads a document and emits derived JSON output
- command does not modify canonical notes or registry

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_extract_profiles_command.py`
Expected: FAIL because command does not exist.

**Step 3: Write minimal implementation**

Implement a new command that:
- loads a pack
- resolves one extraction profile
- runs the extraction runtime against a document
- writes derived output to a stable artifact path under logs or sidecar output

Do not wire it into autopilot yet.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_extract_profiles_command.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ovp_pipeline/commands/extract_profiles.py pyproject.toml tests/test_extract_profiles_command.py
git commit -m "feat: add extraction profile command"
```

### Task 6: Integrate profile outputs into discovery and indexing intentionally

**Files:**
- Modify: `src/ovp_pipeline/discovery.py`
- Modify: `src/ovp_pipeline/knowledge_index.py`
- Test: `tests/test_discovery_extraction_rows.py`
- Test: `tests/test_knowledge_index_extraction_rows.py`

**Step 1: Write the failing test**

Add tests asserting:
- derived extraction rows can be queried separately from canonical pages
- discovery can surface extracted records with stable row shape
- canonical search behavior stays unchanged when no extraction artifacts exist

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_discovery_extraction_rows.py tests/test_knowledge_index_extraction_rows.py`
Expected: FAIL because extraction rows are not indexed.

**Step 3: Write minimal implementation**

Extend indexing with a clearly separate derived table for extraction artifacts.

Rules:
- never treat extraction rows as source of truth
- keep rebuildability from files/sidecars
- preserve current default discovery behavior unless explicitly requested

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_discovery_extraction_rows.py tests/test_knowledge_index_extraction_rows.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ovp_pipeline/discovery.py src/ovp_pipeline/knowledge_index.py tests/test_discovery_extraction_rows.py tests/test_knowledge_index_extraction_rows.py
git commit -m "feat: index derived extraction artifacts"
```

### Task 7: Document the architecture and operational boundaries

**Files:**
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `CLAUDE.md`
- Create: `docs/extraction-profiles.md`

**Step 1: Write the failing doc checklist**

Document these guarantees:
- extraction artifacts are derived, rebuildable, and disposable
- canonical notes and registry remain the source of truth
- packs own profile definitions
- runtime is self-owned and provider-agnostic

**Step 2: Update docs**

Add:
- architecture overview
- profile authoring guide
- first-party media and tech profiles
- operational boundary between extraction, absorb, and knowledge index

**Step 3: Verify docs references**

Run:

```bash
rg -n "extraction profile|derived artifact|source of truth|pack" README.md README_EN.md CLAUDE.md docs/extraction-profiles.md
```

Expected: matching architecture language across all docs.

**Step 4: Commit**

```bash
git add README.md README_EN.md CLAUDE.md docs/extraction-profiles.md
git commit -m "docs: describe extraction profile architecture"
```

## Acceptance Criteria

- OVP has a self-owned extraction profile abstraction.
- Default pack defines first-party media and tech extraction profiles.
- Runtime produces grounded, derived extraction artifacts only.
- Evidence and discovery can consume extraction artifacts without turning them into source of truth.
- No dependency on Hyper-Extract, GraphRAG, LangExtract, Kor, or Instructor is required for core operation.
