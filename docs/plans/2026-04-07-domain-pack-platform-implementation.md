# Domain Pack Platform Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor the current repository into a platform with a first-class `default-knowledge` pack, pack-aware workflow profiles, and a plugin interface that allows external domain packs such as media or medical systems.

**Architecture:** Keep the current runtime, registry, audit, and derived layers in core. Extract the current domain semantics into an in-repo `default-knowledge` pack, then add pack manifests, plugin loading, and workflow-profile registration without breaking existing CLI aliases.

**Tech Stack:** Python CLI, setuptools entry points or plugin manifest loading, Markdown templates/prompts, pytest.

---

## Phase 0: Freeze Current Semantics As Baseline

### Task 1: Write baseline contract tests for current default behavior

**Files:**
- Create: `tests/test_default_pack_compat.py`
- Reference: `src/openclaw_pipeline/unified_pipeline_enhanced.py`
- Reference: `src/openclaw_pipeline/autopilot/daemon.py`

**Step 1: Write the failing test**

Add tests asserting:

- default pipeline still resolves to the current `absorb -> canonical -> derive` semantics
- current CLI aliases still map to the same pack behavior
- no pack selection still behaves like current `default-knowledge`

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_default_pack_compat.py`

Expected: missing pack abstraction failures.

**Step 3: Write minimal implementation**

Do not change behavior yet. Only add the compatibility scaffolding needed for the tests to pass.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_default_pack_compat.py`

Expected: PASS

---

## Phase 1: Introduce Core Pack Interfaces

### Task 2: Create pack manifests and runtime interfaces

**Files:**
- Create: `src/openclaw_pipeline/packs/__init__.py`
- Create: `src/openclaw_pipeline/packs/base.py`
- Create: `src/openclaw_pipeline/packs/loader.py`
- Test: `tests/test_pack_loader.py`

**Step 1: Write the failing test**

Add tests asserting:

- a pack has `name`, `version`, `object_kinds`, `workflow_profiles`
- the loader can load the in-repo default pack
- invalid pack objects fail clearly

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_pack_loader.py`

Expected: import / loader failures.

**Step 3: Write minimal implementation**

Create:

- `BaseDomainPack`
- `WorkflowProfile`
- `StageHandlerSpec`
- `load_pack(name)`
- `load_default_pack()`

Keep this deliberately small. Do not implement external installation yet.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_pack_loader.py`

Expected: PASS

---

## Phase 2: Formalize `default-knowledge` As The First Pack

### Task 3: Move current domain metadata into `default-knowledge`

**Files:**
- Create: `src/openclaw_pipeline/packs/default_knowledge/__init__.py`
- Create: `src/openclaw_pipeline/packs/default_knowledge/pack.py`
- Create: `src/openclaw_pipeline/packs/default_knowledge/schemas.py`
- Create: `src/openclaw_pipeline/packs/default_knowledge/profiles.py`
- Test: `tests/test_default_knowledge_pack.py`

**Step 1: Write the failing test**

Add tests asserting:

- `default-knowledge` exposes object kinds compatible with current behavior
- it registers at least `full` and `autopilot` profiles
- it declares current core stages in the expected order

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_default_knowledge_pack.py`

Expected: missing pack failures.

**Step 3: Write minimal implementation**

Do not move all logic yet. Only formalize metadata and profile declarations.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_default_knowledge_pack.py`

Expected: PASS

---

## Phase 3: Make Workflow Resolution Pack-Aware

### Task 4: Add `--pack` and `--profile` runtime selection

**Files:**
- Modify: `src/openclaw_pipeline/unified_pipeline_enhanced.py`
- Modify: `src/openclaw_pipeline/autopilot/daemon.py`
- Test: `tests/test_pack_profiles.py`

**Step 1: Write the failing test**

Add tests asserting:

- `ovp --pack default-knowledge --profile full` resolves to current full plan
- `ovp-autopilot --pack default-knowledge --profile autopilot` resolves to current autopilot plan
- omitting pack/profile preserves old behavior

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_pack_profiles.py`

Expected: argument parsing / execution-plan mismatch.

**Step 3: Write minimal implementation**

Wire workflow resolution through pack profiles, but preserve:

- existing default behavior
- existing legacy aliases
- `--with-refine`

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_pack_profiles.py`

Expected: PASS

---

## Phase 4: Generalize Registry Toward Object Registry

### Task 5: Introduce pack-aware object metadata without breaking concept registry

**Files:**
- Create: `src/openclaw_pipeline/object_registry.py`
- Modify: `src/openclaw_pipeline/concept_registry.py`
- Test: `tests/test_object_registry.py`

**Step 1: Write the failing test**

Add tests asserting:

- core object records have `id`, `kind`, `pack`, `title`, `status`
- `concept_registry` can project into the object registry model
- current concept behavior still works unchanged

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_object_registry.py tests/test_concept_registry.py`

Expected: missing object registry failures.

**Step 3: Write minimal implementation**

Keep `concept_registry.py` as compatibility surface.
Do not yet migrate all storage.
Only add the abstraction layer needed for pack-aware object kinds.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_object_registry.py tests/test_concept_registry.py`

Expected: PASS

---

## Phase 5: Pack-Aware Discovery And Evidence

### Task 6: Make discovery hooks pack-aware

**Files:**
- Modify: `src/openclaw_pipeline/discovery.py`
- Modify: `src/openclaw_pipeline/evidence.py`
- Modify: `src/openclaw_pipeline/concept_registry.py`
- Test: `tests/test_pack_discovery_hooks.py`

**Step 1: Write the failing test**

Add tests asserting:

- the default pack still returns current discovery behavior
- pack hooks can alter which object kinds are discoverable
- evidence payloads carry `pack` and `object_kind` context

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_pack_discovery_hooks.py`

Expected: no pack hook failures.

**Step 3: Write minimal implementation**

Extend discovery/evidence contracts with pack awareness without changing default ranking semantics.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_pack_discovery_hooks.py`

Expected: PASS

---

## Phase 6: Plugin Installation Surface

### Task 7: Add plugin manifest discovery and installation hooks

**Files:**
- Create: `src/openclaw_pipeline/plugins.py`
- Modify: `pyproject.toml`
- Modify: `src/openclaw_pipeline/commands/...` as needed
- Test: `tests/test_plugin_installation.py`

**Step 1: Write the failing test**

Add tests asserting:

- external packs can be discovered through a manifest or entry point
- plugin metadata is validated
- missing or incompatible API versions fail clearly

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_plugin_installation.py`

Expected: plugin discovery failures.

**Step 3: Write minimal implementation**

Start with read-only plugin discovery and loading.
Do not build a full marketplace.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_plugin_installation.py`

Expected: PASS

---

## Phase 7: Documentation And Migration Notes

### Task 8: Rewrite docs around platform/core/pack/profile model

**Files:**
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `CLAUDE.md`
- Create: `docs/architecture/domain-packs.md` or keep under `docs/plans/`

**Step 1: Write doc checklist**

Document:

- what core owns
- what a pack owns
- why `default-knowledge` is first
- why media is external
- how plugin installation is intended to work

**Step 2: Update docs**

Replace single-domain framing with platform framing while keeping examples grounded in current behavior.

**Step 3: Verify docs reference real commands**

Run:

```bash
rg "pack|profile|default-knowledge|plugin" README.md README_EN.md CLAUDE.md
```

Expected: docs reflect the new model consistently.

---

## Phase 8: Full Verification

### Task 9: Run full platform verification

**Files:**
- Test: full suite

**Step 1: Run focused tests**

```bash
pytest -q tests/test_default_pack_compat.py tests/test_pack_loader.py tests/test_default_knowledge_pack.py tests/test_pack_profiles.py tests/test_object_registry.py tests/test_pack_discovery_hooks.py tests/test_plugin_installation.py
```

Expected: PASS

**Step 2: Run full suite**

```bash
pytest -q
```

Expected: PASS

**Step 3: Run compile verification**

```bash
python3 -m compileall src/openclaw_pipeline
```

Expected: exit 0

**Step 4: Commit**

```bash
git add .
git commit -m "feat: introduce domain pack platform architecture"
```

---

## Notes For The Implementer

- Do not build media support in this repo during the first platform extraction.
- Do not let plugin packs bypass audit/runtime hooks.
- Do not break existing CLI semantics while introducing `--pack` and `--profile`.
- Keep `default-knowledge` behavior identical before generalizing further.
- The success criterion is not “media works”.
- The success criterion is:

> the current system works unchanged as `default-knowledge`, and the core is now structurally ready to host external packs.
