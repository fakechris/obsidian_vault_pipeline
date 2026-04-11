from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


def _capture_json(main_fn, argv: list[str]) -> dict[str, object]:
    buffer = StringIO()
    with redirect_stdout(buffer):
        result = main_fn(argv)
    assert result == 0
    return json.loads(buffer.getvalue())


def _write_note(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _seed_truth_notes(vault: Path) -> None:
    evergreen_dir = vault / "10-Knowledge" / "Evergreen"
    _write_note(
        evergreen_dir / "Harness-Positive.md",
        """---
note_id: harness-positive
title: Harness Positive
type: evergreen
date: 2026-04-10
---

# Harness Positive

Agent harness supports local-first execution for operators.

Links to [[runtime-target]].
""",
    )
    _write_note(
        evergreen_dir / "Harness-Negative.md",
        """---
note_id: harness-negative
title: Harness Negative
type: evergreen
date: 2026-04-10
---

# Harness Negative

Agent harness does not support local-first execution for operators.
""",
    )
    _write_note(
        evergreen_dir / "Runtime-Target.md",
        """---
note_id: runtime-target
title: Runtime Target
type: evergreen
date: 2026-04-10
---

# Runtime Target

Runtime target captures downstream execution effects.
""",
    )
    _write_note(
        evergreen_dir / "Thin-Summary.md",
        """---
note_id: thin-summary
title: Thin Summary
type: evergreen
date: 2026-04-10
---

# Thin Summary

Tiny note.
""",
    )


def _seed_raw_source(vault: Path, *, name: str = "runtime-graph.md") -> Path:
    return _write_note(
        vault / "50-Inbox" / "01-Raw" / name,
        """---
title: Runtime Graph
type: raw
date: 2026-04-10
---

# Runtime Graph

## Architecture

The runtime architecture coordinates [[harness-positive]] and [[runtime-target]].

## Workflow

- Load source material
- Build derived truth artifacts
- Materialize knowledge surfaces
""",
    )


def test_research_tech_pack_e2e_runtime(temp_vault):
    from openclaw_pipeline.commands.build_views import main as build_views_main
    from openclaw_pipeline.commands.extract_preview import main as extract_preview_main
    from openclaw_pipeline.commands.extract_profiles import main as extract_main
    from openclaw_pipeline.commands.extraction_dashboard import main as dashboard_main
    from openclaw_pipeline.commands.rebuild_summaries import main as rebuild_summaries_main
    from openclaw_pipeline.commands.resolve_contradictions import main as resolve_contradictions_main
    from openclaw_pipeline.commands.run_operations import main as run_operations_main
    from openclaw_pipeline.knowledge_index import knowledge_index_stats, rebuild_knowledge_index, search_truth_store
    from openclaw_pipeline.query_tool import VaultQuerier
    from openclaw_pipeline.runtime import VaultLayout

    _seed_truth_notes(temp_vault)
    source = _seed_raw_source(temp_vault)
    layout = VaultLayout.from_vault(temp_vault)

    extract_payload = _capture_json(
        extract_main,
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "research-tech",
            "--profile",
            "tech/workflow_graph",
            "--source",
            str(source),
        ],
    )
    artifact_path = Path(str(extract_payload["artifact_path"]))
    assert artifact_path.exists()

    preview_payload = _capture_json(
        extract_preview_main,
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "research-tech",
            "--profile",
            "tech/workflow_graph",
            "--source",
            str(source),
        ],
    )
    assert preview_payload["pack"] == "research-tech"
    assert preview_payload["run_count"] == 1
    assert preview_payload["record_count"] >= 3

    dashboard_payload = _capture_json(
        dashboard_main,
        ["--vault-dir", str(temp_vault), "--pack", "research-tech"],
    )
    assert dashboard_payload["pack"] == "research-tech"
    assert dashboard_payload["total_runs"] == 1
    assert "tech/workflow_graph" in dashboard_payload["profiles"]

    rebuild_knowledge_index(temp_vault)
    stats = knowledge_index_stats(temp_vault)
    assert stats["objects"] == 4
    assert stats["relations"] == 1
    assert stats["contradictions"] == 1

    extract_view_payload = _capture_json(
        build_views_main,
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "research-tech",
            "--view",
            "overview/extraction",
        ],
    )
    object_view_payload = _capture_json(
        build_views_main,
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "research-tech",
            "--view",
            "object/page",
            "--object-id",
            "harness-positive",
        ],
    )
    topic_view_payload = _capture_json(
        build_views_main,
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "research-tech",
            "--view",
            "overview/topic",
        ],
    )
    event_view_payload = _capture_json(
        build_views_main,
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "research-tech",
            "--view",
            "event/dossier",
        ],
    )
    contradiction_view_payload = _capture_json(
        build_views_main,
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "research-tech",
            "--view",
            "truth/contradictions",
        ],
    )

    assert Path(str(extract_view_payload["output_path"])).exists()
    object_view = Path(str(object_view_payload["output_path"]))
    assert object_view.exists()
    assert "Contradictions" in object_view.read_text(encoding="utf-8")
    assert Path(str(topic_view_payload["output_path"])).exists()
    assert Path(str(event_view_payload["output_path"])).exists()
    contradiction_view = Path(str(contradiction_view_payload["output_path"]))
    assert contradiction_view.exists()
    assert "agent harness" in contradiction_view.read_text(encoding="utf-8")

    contradiction_queue = _capture_json(
        run_operations_main,
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "research-tech",
            "--profile",
            "truth/contradiction_review",
        ],
    )
    stale_queue = _capture_json(
        run_operations_main,
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "research-tech",
            "--profile",
            "truth/stale_summary_review",
        ],
    )

    assert contradiction_queue["written"]
    assert stale_queue["written"]
    contradiction_artifact = Path(str(contradiction_queue["written"][0]))
    stale_artifact = Path(str(stale_queue["written"][0]))
    assert contradiction_artifact.exists()
    assert stale_artifact.exists()
    assert contradiction_artifact.parent == layout.review_queue_dir / "contradictions"
    assert stale_artifact.parent == layout.review_queue_dir / "stale-summaries"

    resolve_payload = _capture_json(
        resolve_contradictions_main,
        [
            "--vault-dir",
            str(temp_vault),
            "--from-queue",
            "contradictions",
            "--status",
            "resolved_keep_positive",
            "--note",
            "Keep positive research-tech claim.",
            "--rebuild-summaries",
            "--json",
        ],
    )
    assert resolve_payload["resolved_count"] == 1
    assert resolve_payload["rebuilt_summary_count"] >= 2
    assert resolve_payload["cleared_queue_files"]
    assert not contradiction_artifact.exists()

    rebuilt_object_view_payload = _capture_json(
        build_views_main,
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "research-tech",
            "--view",
            "object/page",
            "--object-id",
            "harness-positive",
        ],
    )
    rebuilt_object_view = Path(str(rebuilt_object_view_payload["output_path"]))
    rebuilt_object_content = rebuilt_object_view.read_text(encoding="utf-8")
    assert "[resolved_keep_positive]" in rebuilt_object_content
    assert "Keep positive research-tech claim." in rebuilt_object_content

    stale_rebuild_payload = _capture_json(
        rebuild_summaries_main,
        [
            "--vault-dir",
            str(temp_vault),
            "--from-queue",
            "stale-summaries",
            "--json",
        ],
    )
    assert stale_rebuild_payload["objects_rebuilt"] >= 1
    assert "thin-summary" in stale_rebuild_payload["object_ids"]

    truth_hits = search_truth_store(temp_vault, "local-first", limit=5)
    assert any(hit["object_id"] == "harness-positive" for hit in truth_hits)

    results = VaultQuerier(temp_vault, pack="research-tech").search("runtime architecture", top_k=5, engine="knowledge")
    assert results
    assert any(result.title in {"Harness Positive", "Runtime Target"} for result in results)


def test_default_knowledge_pack_e2e_compatibility(temp_vault):
    from openclaw_pipeline.commands.build_views import main as build_views_main
    from openclaw_pipeline.commands.extract_preview import main as extract_preview_main
    from openclaw_pipeline.commands.extract_profiles import main as extract_main
    from openclaw_pipeline.commands.extraction_dashboard import main as dashboard_main
    from openclaw_pipeline.commands.run_operations import main as run_operations_main
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

    _seed_truth_notes(temp_vault)
    source = _seed_raw_source(temp_vault, name="compat-graph.md")
    layout = VaultLayout.from_vault(temp_vault)

    extract_payload = _capture_json(
        extract_main,
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--profile",
            "tech/doc_structure",
            "--source",
            str(source),
        ],
    )
    assert Path(str(extract_payload["artifact_path"])).exists()

    preview_payload = _capture_json(
        extract_preview_main,
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--profile",
            "tech/doc_structure",
            "--source",
            str(source),
        ],
    )
    dashboard_payload = _capture_json(
        dashboard_main,
        ["--vault-dir", str(temp_vault), "--pack", "default-knowledge"],
    )
    assert preview_payload["pack"] == "default-knowledge"
    assert dashboard_payload["pack"] == "default-knowledge"

    rebuild_knowledge_index(temp_vault)

    extract_view_payload = _capture_json(
        build_views_main,
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--view",
            "overview/extraction",
        ],
    )
    object_view_payload = _capture_json(
        build_views_main,
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--view",
            "object/page",
            "--object-id",
            "harness-positive",
        ],
    )
    queue_payload = _capture_json(
        run_operations_main,
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--profile",
            "vault/review_queue",
        ],
    )

    assert Path(str(extract_view_payload["output_path"])).exists()
    assert Path(str(object_view_payload["output_path"])).exists()
    assert queue_payload["written"]
    assert Path(str(queue_payload["written"][0])).exists()
    assert (layout.compiled_views_dir / "default-knowledge").exists()
    assert (layout.extraction_runs_dir / "default-knowledge").exists()
