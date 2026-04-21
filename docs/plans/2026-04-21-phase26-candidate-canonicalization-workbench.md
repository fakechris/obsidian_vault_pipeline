# Phase 26: Candidate Canonicalization Workbench Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make candidate concepts visible, reviewable, and safely promotable from the OVP UI/API without inventing a second canonicalization path.

**Architecture:** `ConceptRegistry` remains the source of truth for candidate/active/rejected state. The workbench adds a read payload over existing registry candidates, then routes promote/merge/reject UI/API actions through the existing `promote_candidates.py` lifecycle functions. Successful mutations write a review audit event and refresh derived knowledge state when the mutation changes active Evergreen content or links.

**Tech Stack:** Python stdlib HTTP server, existing OVP truth API/view-model layer, `ConceptRegistry`, `promote_candidates.py`, pytest.

### Task 1: Candidate Browser Truth API

**Files:**
- Modify: `src/ovp_pipeline/truth_api.py`
- Test: `tests/test_truth_api.py`

**Step 1: Write failing tests**

Add tests that seed a candidate registry entry and assert:
- `list_candidate_concepts()` returns candidate metadata, suggested action, candidate note path, and similar active concepts.
- `review_candidate_concept(action="promote")` promotes the candidate, deletes the candidate note, creates the Evergreen note, records a review action, and reports whether the knowledge index was rebuilt.

**Step 2: Run RED tests**

Run: `pytest tests/test_truth_api.py::test_truth_api_lists_candidate_concepts tests/test_truth_api.py::test_truth_api_review_candidate_concept_promotes_and_records_audit -q`

Expected: fail because the new truth API functions do not exist.

**Step 3: Implement truth API**

Add:
- `list_candidate_concepts(vault_dir, query="", limit=100, offset=0) -> dict`
- `review_candidate_concept(vault_dir, slug, action, target_slug=None, note="", pack_name=None) -> dict`

Implementation notes:
- Load `ConceptRegistry(resolve_vault_dir(vault_dir)).load()`.
- Use `review_candidates(registry)` for suggested actions and similar concepts.
- Use relative candidate path `10-Knowledge/Evergreen/_Candidates/<slug>.md` when it exists.
- Use existing `promote_candidate`, `merge_candidate`, and `reject_candidate` for lifecycle side effects.
- Rebuild `knowledge.db` after `promote` and `merge` so UI surfaces reflect active content/link changes.
- Record `ui_candidate_reviewed` through `record_review_action`.

**Step 4: Run GREEN tests**

Run the same two tests. Expected: pass.

### Task 2: Candidate Browser View Model

**Files:**
- Modify: `src/ovp_pipeline/ui/view_models.py`
- Test: `tests/test_truth_api.py`

**Step 1: Write failing test**

Add a test asserting `build_candidate_browser_payload()` returns:
- `screen == "candidates/browser"`
- requested pack/query metadata
- candidate items from the truth API
- operator rail links back to briefing, signals, actions, and objects.

**Step 2: Run RED test**

Run: `pytest tests/test_truth_api.py::test_candidate_browser_payload_exposes_operator_context -q`

Expected: fail because the view model does not exist.

**Step 3: Implement view model**

Add `build_candidate_browser_payload(vault_dir, pack_name=None, query="")` that wraps `list_candidate_concepts()` and exposes a small operator rail. Do not add new persistence or business logic here.

**Step 4: Run GREEN test**

Run the same test. Expected: pass.

### Task 3: Candidate UI/API Routes

**Files:**
- Modify: `src/ovp_pipeline/commands/ui_server.py`
- Test: `tests/test_ui_server.py`

**Step 1: Write failing tests**

Add tests that assert:
- `GET /api/candidates` returns `screen == "candidates/browser"` and candidate items.
- `GET /candidates` renders candidate title plus promote/merge/reject controls.
- `POST /api/candidates/review` can promote a candidate and returns the lifecycle mutation.

**Step 2: Run RED tests**

Run: `pytest tests/test_ui_server.py::test_ui_server_candidates_endpoint_returns_payload tests/test_ui_server.py::test_ui_server_candidates_page_renders_review_controls tests/test_ui_server.py::test_ui_server_can_promote_candidate_via_api -q`

Expected: fail because candidate routes do not exist.

**Step 3: Implement routes and HTML**

Add:
- Shell nav item `Candidates` for research-compatible shells.
- `GET /api/candidates` and `GET /candidates`.
- `POST /api/candidates/review` and `POST /candidates/review`.
- `_render_candidates_page(payload)` and `_render_candidate_items(...)`.
- `_review_candidate_action(form)` that delegates to `review_candidate_concept()`.

Use `_guard_research_route()` for the new surface, matching other research-only review surfaces.

**Step 4: Run GREEN tests**

Run the same three tests. Expected: pass.

### Task 4: Documentation and Roadmap Closeout

**Files:**
- Modify: `docs/plans/2026-04-17-ovp-architecture-mapping.md`
- Modify: current roadmap/progress docs that mention stale milestone status
- Modify: `docs/plans/2026-04-21-phase26-candidate-canonicalization-workbench.md`

**Step 1: Update docs**

Document:
- Candidate workbench uses registry candidates as source of truth.
- Promote/merge/reject are operator review actions, not automatic semantic assertions.
- Candidate links are not typed graph triples; they are canonical Obsidian wikilink targets plus lifecycle state.
- Phase 26 completion criteria.

**Step 2: Run docs sanity checks**

Run: `rg -n "Phase 26|Candidate|Milestone 8\\+|not started" docs`

Expected: Phase 26 appears in plan/progress docs and stale milestone text is corrected or explicitly contextualized.

### Task 5: Verification, Commit, PR

**Files:**
- Modified files from Tasks 1-4

**Step 1: Run targeted tests**

Run:
- `pytest tests/test_truth_api.py::test_truth_api_lists_candidate_concepts tests/test_truth_api.py::test_truth_api_review_candidate_concept_promotes_and_records_audit tests/test_truth_api.py::test_candidate_browser_payload_exposes_operator_context -q`
- `pytest tests/test_ui_server.py::test_ui_server_candidates_endpoint_returns_payload tests/test_ui_server.py::test_ui_server_candidates_page_renders_review_controls tests/test_ui_server.py::test_ui_server_can_promote_candidate_via_api -q`

**Step 2: Run broader safety suite**

Run:
- `pytest tests/test_promote_candidates.py tests/test_truth_api.py tests/test_ui_server.py -q`
- `git diff --check`

**Step 3: Commit and PR**

Commit with a Phase 26 message, push `feat/phase26-candidate-workbench`, open a PR, inspect review feedback, fix actionable bugs, and merge only if review/CI are clean.

## Closeout Notes

Phase 26 is complete when the PR lands with these properties:

- Candidate concepts are visible through `/candidates` and `/api/candidates`.
- The workbench reads candidates from `ConceptRegistry`; it does not create a second candidate store.
- `promote`, `merge`, and `reject` actions route through `promote_candidates.py`.
- Every UI/API review action writes a `ui_candidate_reviewed` event.
- `promote` and `merge` rebuild the knowledge index so active object/link state does not remain stale after an operator action.
- Candidate canonicalization remains distinct from typed semantic evolution links. A candidate wikilink is an identity/lifecycle concern; typed links like `replaces`, `enriches`, `confirms`, and `challenges` remain evolution/relation concerns.

Verification completed during implementation:

- `pytest tests/test_truth_api.py::test_truth_api_lists_candidate_concepts tests/test_truth_api.py::test_truth_api_review_candidate_concept_promotes_and_records_audit tests/test_truth_api.py::test_candidate_browser_payload_exposes_operator_context tests/test_ui_server.py::test_ui_server_candidates_endpoint_returns_payload tests/test_ui_server.py::test_ui_server_candidates_page_renders_review_controls tests/test_ui_server.py::test_ui_server_can_promote_candidate_via_api -q`
- `pytest tests/test_truth_api.py::test_truth_api_review_candidate_concept_merges_into_existing_and_rebuilds tests/test_truth_api.py::test_truth_api_review_candidate_concept_rejects_without_rebuilding_index -q`
- `pytest tests/test_promote_candidates.py tests/test_truth_api.py tests/test_ui_server.py -q`
