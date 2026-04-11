from __future__ import annotations

import json


def test_review_queue_builder_defaults_to_compatibility_pack(temp_vault):
    from openclaw_pipeline.extraction.artifacts import write_run_result
    from openclaw_pipeline.extraction.results import ExtractionRunResult
    from openclaw_pipeline.operations.runtime import _review_queue_items
    from openclaw_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(temp_vault)
    result = ExtractionRunResult(
        pack_name="default-knowledge",
        profile_name="tech/doc_structure",
        source_path="50-Inbox/01-Raw/example.md",
        records=[],
    )
    write_run_result(layout, result)

    items = _review_queue_items(temp_vault)

    assert len(items) == 1
    assert items[0]["queue_name"] == "review"
    assert items[0]["issue_type"] == "extraction-empty"
    assert items[0]["profile"] == "tech/doc_structure"


def test_run_operations_help_mentions_primary_pack(capsys):
    from openclaw_pipeline.commands.run_operations import main

    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0

    output = " ".join(capsys.readouterr().out.split())
    assert "compatibility pack" in output
    assert "research-tech" in output


def test_run_operations_command_writes_frontmatter_review_items(temp_vault):
    from openclaw_pipeline.commands.run_operations import main
    from openclaw_pipeline.runtime import VaultLayout

    note = temp_vault / "10-Knowledge" / "Evergreen" / "Broken.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        """---
note_id: broken
type: evergreen
date: 2026-04-10
---

# Broken
""",
        encoding="utf-8",
    )

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--profile",
            "vault/frontmatter_audit",
        ]
    )

    layout = VaultLayout.from_vault(temp_vault)
    artifacts = sorted(layout.review_queue_dir.rglob("*.json"))

    assert result == 0
    assert artifacts
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["queue_name"] == "frontmatter"
    assert payload["issue_type"] == "missing-title"


def test_run_operations_command_writes_extraction_review_items(temp_vault):
    from openclaw_pipeline.commands.run_operations import main
    from openclaw_pipeline.derived.paths import extraction_run_path
    from openclaw_pipeline.extraction.results import ExtractionRunResult
    from openclaw_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(temp_vault)
    source_path = temp_vault / "50-Inbox" / "01-Raw" / "example.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("# Example\n\nBody.\n", encoding="utf-8")

    artifact = extraction_run_path(
        layout,
        pack_name="default-knowledge",
        profile_name="tech/doc_structure",
        source_path=source_path.relative_to(temp_vault),
    )
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            ExtractionRunResult(
                pack_name="default-knowledge",
                profile_name="tech/doc_structure",
                source_path=str(source_path.relative_to(temp_vault)),
                records=[],
            ).to_dict(),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--profile",
            "vault/review_queue",
        ]
    )

    artifacts = sorted(layout.review_queue_dir.rglob("*.json"))

    assert result == 0
    assert artifacts
    payload = json.loads(artifacts[-1].read_text(encoding="utf-8"))
    assert payload["queue_name"] == "review"
    assert payload["issue_type"] == "extraction-empty"
    assert payload["profile"] == "tech/doc_structure"


def test_run_operations_command_uses_profile_pack_for_extraction_review_items(temp_vault):
    from openclaw_pipeline.commands.run_operations import main
    from openclaw_pipeline.derived.paths import extraction_run_path
    from openclaw_pipeline.extraction.results import ExtractionRunResult
    from openclaw_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(temp_vault)
    source_path = temp_vault / "50-Inbox" / "01-Raw" / "research.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("# Research\n\nBody.\n", encoding="utf-8")

    artifact = extraction_run_path(
        layout,
        pack_name="research-tech",
        profile_name="tech/doc_structure",
        source_path=source_path.relative_to(temp_vault),
    )
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            ExtractionRunResult(
                pack_name="research-tech",
                profile_name="tech/doc_structure",
                source_path=str(source_path.relative_to(temp_vault)),
                records=[],
            ).to_dict(),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "research-tech",
            "--profile",
            "vault/review_queue",
        ]
    )

    artifacts = sorted(layout.review_queue_dir.rglob("*.json"))

    assert result == 0
    assert artifacts
    payload = json.loads(artifacts[-1].read_text(encoding="utf-8"))
    assert payload["queue_name"] == "review"
    assert payload["issue_type"] == "extraction-empty"
    assert payload["profile"] == "tech/doc_structure"
    assert payload["file"] == "50-Inbox/01-Raw/research.md"


def test_run_operations_command_writes_contradiction_review_items(temp_vault):
    from openclaw_pipeline.commands.run_operations import main
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

    one = temp_vault / "10-Knowledge" / "Evergreen" / "One.md"
    two = temp_vault / "10-Knowledge" / "Evergreen" / "Two.md"
    one.write_text(
        """---
note_id: agent-harness-positive
title: Agent Harness Positive
type: evergreen
date: 2026-04-10
---

# Agent Harness Positive

Agent harness supports local-first execution for operators.
""",
        encoding="utf-8",
    )
    two.write_text(
        """---
note_id: agent-harness-negative
title: Agent Harness Negative
type: evergreen
date: 2026-04-10
---

# Agent Harness Negative

Agent harness does not support local-first execution for operators.
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)
    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--profile",
            "truth/contradiction_review",
        ]
    )

    layout = VaultLayout.from_vault(temp_vault)
    artifacts = sorted((layout.review_queue_dir / "contradictions").rglob("*.json"))

    assert result == 0
    assert artifacts
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["queue_name"] == "contradictions"
    assert payload["issue_type"] == "truth-contradiction"
    assert payload["contradiction_id"].startswith("contradiction::")
    assert payload["subject_key"] == "agent harness"


def test_run_operations_command_writes_stale_summary_review_items(temp_vault):
    from openclaw_pipeline.commands.run_operations import main
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

    note = temp_vault / "10-Knowledge" / "Evergreen" / "Thin.md"
    note.write_text(
        """---
note_id: thin-note
title: Thin Note
type: evergreen
date: 2026-04-10
---

# Thin Note

Thin note.
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)
    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--profile",
            "truth/stale_summary_review",
        ]
    )

    layout = VaultLayout.from_vault(temp_vault)
    artifacts = sorted((layout.review_queue_dir / "stale-summaries").rglob("*.json"))

    assert result == 0
    assert artifacts
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["queue_name"] == "stale-summaries"
    assert payload["issue_type"] == "stale-compiled-summary"
    assert payload["object_id"] == "thin-note"


def test_run_operations_command_frontmatter_audit_supports_research_tech_pack(temp_vault):
    from openclaw_pipeline.commands.run_operations import main
    from openclaw_pipeline.runtime import VaultLayout

    note = temp_vault / "10-Knowledge" / "Evergreen" / "Broken.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        """---
note_id: broken
type: evergreen
date: 2026-04-10
---

# Broken
""",
        encoding="utf-8",
    )

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "research-tech",
            "--profile",
            "vault/frontmatter_audit",
        ]
    )

    layout = VaultLayout.from_vault(temp_vault)
    artifacts = sorted(layout.review_queue_dir.rglob("*.json"))

    assert result == 0
    assert artifacts
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["queue_name"] == "frontmatter"
    assert payload["issue_type"] == "missing-title"
