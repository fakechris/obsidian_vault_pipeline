from __future__ import annotations


def test_build_views_command_writes_compiled_markdown(temp_vault):
    from openclaw_pipeline.commands.build_views import main
    from openclaw_pipeline.runtime import VaultLayout

    note = temp_vault / "10-Knowledge" / "Evergreen" / "Agent-Harness.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        """---
note_id: agent-harness
title: Agent Harness
type: evergreen
date: 2026-04-10
---

# Agent Harness
""",
        encoding="utf-8",
    )

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--view",
            "overview/domain",
        ]
    )

    layout = VaultLayout.from_vault(temp_vault)
    artifacts = sorted(layout.compiled_views_dir.rglob("*.md"))

    assert result == 0
    assert artifacts
    content = artifacts[0].read_text(encoding="utf-8")
    assert "# overview/domain" in content
    assert "Agent Harness" in content


def test_build_views_command_respects_input_source_kinds(temp_vault):
    from openclaw_pipeline.commands.build_views import main
    from openclaw_pipeline.runtime import VaultLayout

    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "Evergreen-Only.md"
    evergreen.parent.mkdir(parents=True, exist_ok=True)
    evergreen.write_text(
        """---
title: Evergreen Only
type: evergreen
---
""",
        encoding="utf-8",
    )

    query = temp_vault / "20-Areas" / "Queries" / "Saved-Answer.md"
    query.parent.mkdir(parents=True, exist_ok=True)
    query.write_text(
        """---
title: Saved Answer
type: query
---
""",
        encoding="utf-8",
    )

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--view",
            "saved_answer/query",
        ]
    )

    layout = VaultLayout.from_vault(temp_vault)
    artifacts = sorted(layout.compiled_views_dir.rglob("*.md"))

    assert result == 0
    assert artifacts
    content = artifacts[-1].read_text(encoding="utf-8")
    assert "Saved Answer" in content
    assert "Evergreen Only" not in content
