# BL-003/004 Hot Path And Wiring Safety Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Protect the reader-first default UI by proving reader, dashboard, and search routes do not rebuild or rescan raw source content, and by adding no-LLM wiring evals for critical workflow boundaries.

**Architecture:** PR #75 made `/` the reader-first Knowledge Library and moved the operator dashboard to `/ops`. The next implementation slice should make that route split mechanically safe: hot GET routes read from derived state only, while write/review routes keep going through governance APIs that emit audit events. These checks are architectural fitness functions, not product features.

**Tech Stack:** Python, pytest, `http.client`, `ThreadingHTTPServer`, `ovp_pipeline.commands.ui_server`, `ovp_pipeline.ui.view_models`, `ovp_pipeline.truth_api`, `ovp_pipeline.knowledge_index`. No network, no LLM calls, no `ovp` CLI run.

## Current Context

- `BACKLOG.md` now treats `BL-003` and `BL-004` as the active safety slice after the reader shell route split.
- `src/ovp_pipeline/commands/ui_server.py` owns the HTTP route table. Key read routes are `/`, `/ops`, `/search`, `/api/search`, `/objects`, and `/api/objects`.
- `src/ovp_pipeline/ui/view_models.py` builds reader/dashboard/search payloads.
- `src/ovp_pipeline/truth_api.py` reads `knowledge.db` projections through `ensure_knowledge_db_current()`.
- `src/ovp_pipeline/knowledge_index.py::rebuild_knowledge_index()` is the heavy derived rebuild path and scans vault content. It must not run during normal hot GET routes when `knowledge.db` already exists and has the expected schema.
- Existing tests already cover basic UI route behavior in `tests/test_ui_server.py`; this plan adds focused architectural tests instead of expanding those smoke tests indefinitely.

## Product Constraint

Default user-facing reader routes should remain simple:

- No source ingestion language in reader pages.
- No workflow jargon in `/` unless the user opens `/ops`.
- No heavy rebuild, raw/PDF/Office scan, embedding, or LLM call during a normal GET route.
- Mutating review routes may trigger rebuild or repair work only after an explicit POST action, and only through governance/truth APIs.

## Out Of Scope

- Do not implement `BL-002` projection labels except where a minimal route metadata field is necessary for an eval.
- Do not redesign object pages, graph pages, backlinks, or search UX in this slice.
- Do not introduce LanceDB, semantic search backend changes, or projection repair markers.
- Do not run the local OVP app or user vault workflow.

## Task 1: Add Hot Path Guard Tests

**Files:**

- Modify: `tests/conftest.py`
- Create: `tests/test_ui_hot_paths.py`
- Reuse helper patterns from: `tests/test_ui_server.py`

**Step 1: Add shared HTTP and seed fixtures**

Add fixtures to `tests/conftest.py` so the hot-path, wiring, and architecture fitness tests do not import private helpers from another test module.

```python
import threading
from http.client import HTTPConnection

import pytest


@pytest.fixture
def fetch_ui():
    def _fetch(temp_vault, path: str) -> tuple[int, str, str]:
        from ovp_pipeline.commands.ui_server import create_server

        server = create_server(temp_vault, host="127.0.0.1", port=0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", path)
            response = conn.getresponse()
            body = response.read().decode("utf-8")
            content_type = response.getheader("Content-Type") or ""
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        return response.status, body, content_type

    return _fetch


@pytest.fixture
def post_ui():
    def _post(temp_vault, path: str, body: str) -> tuple[int, str]:
        from ovp_pipeline.commands.ui_server import create_server

        server = create_server(temp_vault, host="127.0.0.1", port=0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                path,
                body=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response = conn.getresponse()
            payload = response.read().decode("utf-8")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        return response.status, payload

    return _post


@pytest.fixture
def seed_hot_path_vault():
    def _seed(temp_vault) -> None:
        from ovp_pipeline.knowledge_index import rebuild_knowledge_index

        note = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
        note.write_text(
            """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-30
---

# Alpha

Alpha supports reader-first local knowledge reuse.
""",
            encoding="utf-8",
        )
        raw_dir = temp_vault / "00-Capture" / "Raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "heavy.pdf").write_bytes(b"%PDF-1.4 sentinel")
        (raw_dir / "heavy.docx").write_bytes(b"PK sentinel")
        rebuild_knowledge_index(temp_vault)

    return _seed
```

**Step 2: Write the failing hot-path test scaffold**

Create `tests/test_ui_hot_paths.py`.

```python
from __future__ import annotations

import pytest
```

**Step 3: Add the guard monkeypatch**

Patch all rebuild entry points that a hot GET route could accidentally call after the seed rebuild has completed.

```python
def _forbid_rebuilds(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("hot GET route must not rebuild knowledge.db")

    import ovp_pipeline.knowledge_index as knowledge_index
    import ovp_pipeline.truth_api as truth_api

    monkeypatch.setattr(knowledge_index, "rebuild_knowledge_index", fail)
    monkeypatch.setattr(truth_api, "rebuild_knowledge_index", fail)
```

**Step 4: Add route coverage**

Start with routes that are now default reader/product entry points plus operator dashboard/search surfaces.

```python
@pytest.mark.parametrize(
    ("path", "expected_text"),
    [
        ("/", "Knowledge Library"),
        ("/ops", "OVP Truth UI"),
        ("/objects", "Alpha"),
        ("/api/objects", '"object_id": "alpha"'),
        ("/search?q=alpha", "Alpha"),
        ("/api/search?q=alpha", '"query": "alpha"'),
    ],
)
def test_ui_hot_get_routes_do_not_rebuild_knowledge_db(
    temp_vault,
    monkeypatch,
    fetch_ui,
    seed_hot_path_vault,
    path,
    expected_text,
):
    seed_hot_path_vault(temp_vault)
    _forbid_rebuilds(monkeypatch)

    status, body, _content_type = fetch_ui(temp_vault, path)

    assert status == 200
    assert expected_text in body
```

**Step 5: Run and confirm the expected state**

Run:

```bash
PYTHONPATH=src python -m pytest tests/test_ui_hot_paths.py -q
```

Expected before implementation: fail if any route rebuilds `knowledge.db`; pass if current code already respects the boundary.

**Step 6: Minimal implementation if the test fails**

If a route rebuilds despite a current `knowledge.db`, inspect the stack and fix the smallest boundary:

- Prefer making `ensure_knowledge_db_current()` a schema/existence check only on hot read paths.
- Do not add route-specific caches.
- Do not skip schema validation.
- Do not silence rebuild failures globally.

Likely files if a fix is needed:

- Modify: `src/ovp_pipeline/truth_api.py`
- Modify only if necessary: `src/ovp_pipeline/knowledge_index.py`

**Step 7: Commit after Task 1**

```bash
git add tests/conftest.py tests/test_ui_hot_paths.py src/ovp_pipeline/truth_api.py src/ovp_pipeline/knowledge_index.py
git commit -m "test: guard ui hot paths from rebuilds"
```

If no source files changed, commit only `tests/conftest.py` and `tests/test_ui_hot_paths.py`.

## Task 2: Add Workflow Wiring Fitness Tests

**Files:**

- Create: `tests/test_workflow_wiring.py`
- Modify if needed: `src/ovp_pipeline/commands/ui_server.py`
- Modify if needed: `src/ovp_pipeline/truth_api.py`

Add this header to `tests/test_workflow_wiring.py`:

```python
from __future__ import annotations

import json

import pytest
```

**Step 1: Test reader/operator route dispatch**

Lock the route split so future edits do not accidentally make `/` operator-first again.

```python
def test_root_and_ops_dispatch_to_distinct_renderers(temp_vault, monkeypatch, fetch_ui):
    import ovp_pipeline.commands.ui_server as ui_server

    calls = []
    monkeypatch.setattr(
        ui_server,
        "_build_runtime_home_payload_from_query",
        lambda vault_dir, query: {"screen": "runtime/home", "requested_pack": ""},
    )
    monkeypatch.setattr(
        ui_server,
        "_render_library_home",
        lambda payload: calls.append("library") or "<html>library</html>",
    )
    monkeypatch.setattr(
        ui_server,
        "_render_dashboard",
        lambda payload: calls.append("dashboard") or "<html>dashboard</html>",
    )

    assert fetch_ui(temp_vault, "/")[0] == 200
    assert fetch_ui(temp_vault, "/ops")[0] == 200
    assert calls == ["library", "dashboard"]
```

**Step 2: Test read/write boundary for candidate promotion**

Patch the governance/truth API seam imported by `ui_server.py`. The route should call `review_candidate_concept()` and should not mutate `ConceptRegistry` directly from the route handler.

```python
def test_candidate_review_route_uses_truth_api_governance_seam(temp_vault, monkeypatch, post_ui):
    import ovp_pipeline.commands.ui_server as ui_server

    calls = []

    def fake_review_candidate_concept(*args, **kwargs):
        calls.append(kwargs.get("action") or args)
        return {
            "action": "promote",
            "mutation": {"action": "promote"},
            "knowledge_index_rebuilt": False,
            "next_path": "/candidates",
        }

    monkeypatch.setattr(ui_server, "review_candidate_concept", fake_review_candidate_concept)

    status, payload = post_ui(
        temp_vault,
        "/api/candidates/review",
        "slug=alpha-candidate&action=promote",
    )

    assert status == 200
    assert json.loads(payload)["action"] == "promote"
    assert calls
```

**Step 3: Test research-only route guard before mutation**

Generalize the existing contradiction guard coverage to the other research mutation routes. The guard should return 409 before running the mutation function.

```python
@pytest.mark.parametrize(
    ("path", "body", "patched_name"),
    [
        ("/api/evolution/review", "candidate_id=evo-1&action=accept&pack=media-editorial", "review_evolution_candidate"),
        ("/api/candidates/review", "slug=alpha&action=promote&pack=media-editorial", "review_candidate_concept"),
        ("/api/summaries/rebuild", "object_id=alpha&pack=media-editorial", "rebuild_compiled_summaries"),
    ],
)
def test_research_mutation_routes_guard_non_research_pack_before_work(
    temp_vault,
    monkeypatch,
    post_ui,
    path,
    body,
    patched_name,
):
    import ovp_pipeline.commands.ui_server as ui_server

    monkeypatch.setattr(
        ui_server,
        patched_name,
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("mutation should not run")),
    )

    status, payload = post_ui(temp_vault, path, body)

    parsed = json.loads(payload)
    assert status == 409
    assert parsed["status"] == "unsupported_pack"
```

**Step 4: Test action queue routes use explicit action APIs**

Patch `enqueue_signal_action()` and `run_next_action_queue_item()` on `ui_server.py`. These are workflow operations and must stay out of renderer/view-model code.

```python
def test_action_enqueue_route_uses_truth_api_action_queue_seam(temp_vault, monkeypatch, post_ui):
    import ovp_pipeline.commands.ui_server as ui_server

    calls = []

    def fake_enqueue(*args, **kwargs):
        calls.append(kwargs)
        return {"status": "queued", "next_path": "/signals"}

    monkeypatch.setattr(ui_server, "enqueue_signal_action", fake_enqueue)

    status, payload = post_ui(
        temp_vault,
        "/api/actions/enqueue",
        "signal_id=sig-1&action_kind=deep_dive_workflow",
    )

    assert status == 200
    assert json.loads(payload)["status"] == "queued"
    assert calls
```

**Step 5: Run the wiring suite**

Run:

```bash
PYTHONPATH=src python -m pytest tests/test_workflow_wiring.py -q
```

Expected before implementation: fail only where the route currently bypasses the intended seam.

**Step 6: Minimal implementation if tests fail**

- Keep route handlers as dispatchers.
- Move direct mutation logic into `truth_api.py` or the existing governance/action API.
- Keep renderer functions pure: payload in, HTML out.
- Keep pack guard checks before any mutation or rebuild call.

**Step 7: Commit after Task 2**

```bash
git add tests/test_workflow_wiring.py src/ovp_pipeline/commands/ui_server.py src/ovp_pipeline/truth_api.py
git commit -m "test: lock workflow wiring boundaries"
```

## Task 3: Add Naming And Product-Surface Fitness Checks

**Files:**

- Create: `tests/test_architecture_fitness.py`
- Modify if needed: `src/ovp_pipeline/commands/ui_server.py`

Add this header to `tests/test_architecture_fitness.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]
```

**Step 1: Add a small banned-term check for reader routes**

Reader routes should not show operator/workflow language on the first screen. Keep the banned list small and concrete.

```python
@pytest.mark.parametrize("path", ["/", "/search?q=alpha", "/objects"])
def test_reader_routes_do_not_expose_operator_jargon(
    temp_vault,
    fetch_ui,
    seed_hot_path_vault,
    path,
):
    seed_hot_path_vault(temp_vault)

    status, body, _content_type = fetch_ui(temp_vault, path)

    assert status == 200
    for banned in ["Workflow Map", "Compile gate", "Projection lifecycle", "source of truth"]:
        assert banned not in body
```

**Step 2: Add naming discipline check for public docs**

This is a cheap guard until a real lint script exists. Exclude `ARCHITECTURE.md` from the simple banned-term check because the architecture document intentionally contains do/don't examples for naming discipline.

```python
def test_readme_and_milestone_avoid_source_of_truth_language(repo_root):
    docs = [
        repo_root / "README.md",
        repo_root / "MILESTONE.md",
    ]
    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert "source of truth" not in text.lower()
```

**Step 3: Run the fitness check**

```bash
PYTHONPATH=src python -m pytest tests/test_architecture_fitness.py -q
```

Expected: pass after any terminology cleanup.

**Step 4: Commit after Task 3**

```bash
git add tests/test_architecture_fitness.py README.md ARCHITECTURE.md MILESTONE.md
git commit -m "test: add lightweight architecture fitness checks"
```

## Task 4: Consolidate Validation

**Files:**

- Modify only if useful: `Makefile`, `pyproject.toml`, or no files.

**Step 1: Run focused checks**

```bash
PYTHONPATH=src python -m pytest \
  tests/test_ui_hot_paths.py \
  tests/test_workflow_wiring.py \
  tests/test_architecture_fitness.py \
  -q
```

Expected: all tests pass.

**Step 2: Run nearby regression tests**

```bash
PYTHONPATH=src python -m pytest \
  tests/test_ui_server.py \
  tests/test_ui_view_models.py \
  tests/test_truth_api.py \
  tests/test_promotion_policy.py \
  -q
```

Expected: all tests pass.

**Step 3: Confirm no product route started invoking heavy work**

```bash
PYTHONPATH=src python -m pytest tests/test_ui_hot_paths.py::test_ui_hot_get_routes_do_not_rebuild_knowledge_db -q
```

Expected: pass.

**Step 4: Commit validation harness changes if any**

If Task 4 adds a command alias:

```bash
git add Makefile pyproject.toml
git commit -m "chore: expose architecture fitness checks"
```

Skip this commit if no files changed.

## Task 5: PR Scope And Review Checklist

Before opening the implementation PR:

- Confirm all changed files are tests or minimal route/API boundary changes.
- Confirm no user vault files changed.
- Confirm no `ovp` CLI workflow was run.
- Confirm reader routes still use user-facing language and `/ops` remains the operator surface.
- Confirm any route that mutates Layer 1 state goes through a governance/truth API seam.
- Confirm every failed hot-path test was fixed by removing accidental heavy work, not by weakening the test.

Final commands:

```bash
git status -sb
git diff --check
PYTHONPATH=src python -m pytest \
  tests/test_ui_hot_paths.py \
  tests/test_workflow_wiring.py \
  tests/test_architecture_fitness.py \
  tests/test_ui_server.py \
  tests/test_ui_view_models.py \
  tests/test_truth_api.py \
  tests/test_promotion_policy.py \
  -q
```

Open a normal implementation PR after these pass. Keep the PR title explicit, for example:

```text
[codex] Add hot-path and workflow wiring fitness checks
```
