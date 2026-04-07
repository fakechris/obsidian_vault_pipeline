from __future__ import annotations

import json
from pathlib import Path


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_cleanup_write_restructures_diary_sections(temp_vault, capsys):
    from openclaw_pipeline.commands.cleanup import main

    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "messy-note.md"
    evergreen.write_text(
        """---
note_id: messy-note
title: Messy Note
type: evergreen
date: 2026-04-07
---

# Messy Note

Intro paragraph.

## 2026-01
Something happened.

## 2026-02
Another event happened.
""",
        encoding="utf-8",
    )

    result = main(["--vault-dir", str(temp_vault), "--slug", "messy-note", "--write", "--json"])
    captured = capsys.readouterr()

    assert result == 0
    payload = json.loads(captured.out)
    assert payload["mode"] == "cleanup"
    assert payload["write"] is True
    assert payload["applied_count"] == 1

    updated = evergreen.read_text(encoding="utf-8")
    assert "## Historical Notes" in updated
    assert "### 2026-01" in updated
    assert "### 2026-02" in updated

    refine_log = _read_jsonl(temp_vault / "60-Logs" / "refine-mutations.jsonl")
    pipeline_log = _read_jsonl(temp_vault / "60-Logs" / "pipeline.jsonl")
    assert refine_log[-1]["event_type"] == "refine_mutation_applied"
    assert refine_log[-1]["mode"] == "cleanup"
    assert refine_log[-1]["slug"] == "messy-note"
    assert pipeline_log[-1]["event_type"] == "refine_run_completed"
    assert pipeline_log[-1]["mode"] == "cleanup"
    assert pipeline_log[-1]["applied_count"] == 1
    assert pipeline_log[-1]["canonical_refreshed"] is True

    registry_path = temp_vault / "10-Knowledge" / "Atlas" / "concept-registry.jsonl"
    atlas_index = temp_vault / "10-Knowledge" / "Atlas" / "Atlas-Index.md"
    assert registry_path.exists()
    assert atlas_index.exists()
    assert "[[messy-note|Messy Note]]" in atlas_index.read_text(encoding="utf-8")


def test_breakdown_write_creates_child_notes_and_updates_parent(temp_vault, capsys):
    from openclaw_pipeline.commands.breakdown import main

    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "agent-harness.md"
    evergreen.write_text(
        """---
note_id: agent-harness
title: Agent Harness
type: evergreen
date: 2026-04-07
---

# Agent Harness

## Architecture
Architecture details line 1.
Architecture details line 2.
Architecture details line 3.
Architecture details line 4.
Architecture details line 5.
Architecture details line 6.
Architecture details line 7.
Architecture details line 8.
Architecture details line 9.
Architecture details line 10.
Architecture details line 11.
Architecture details line 12.
Architecture details line 13.
Architecture details line 14.
Architecture details line 15.
Architecture details line 16.
Architecture details line 17.
Architecture details line 18.
Architecture details line 19.
Architecture details line 20.

## Usage Patterns
Usage details line 1.
Usage details line 2.
Usage details line 3.
Usage details line 4.
Usage details line 5.
Usage details line 6.
Usage details line 7.
Usage details line 8.
Usage details line 9.
Usage details line 10.
Usage details line 11.
Usage details line 12.
Usage details line 13.
Usage details line 14.
Usage details line 15.
Usage details line 16.
Usage details line 17.
Usage details line 18.
Usage details line 19.
Usage details line 20.
""",
        encoding="utf-8",
    )

    result = main(["--vault-dir", str(temp_vault), "--slug", "agent-harness", "--write", "--json"])
    captured = capsys.readouterr()

    assert result == 0
    payload = json.loads(captured.out)
    assert payload["mode"] == "breakdown"
    assert payload["write"] is True
    assert payload["applied_count"] == 1

    child_a = temp_vault / "10-Knowledge" / "Evergreen" / "agent-harness-architecture.md"
    child_b = temp_vault / "10-Knowledge" / "Evergreen" / "agent-harness-usage-patterns.md"
    assert child_a.exists()
    assert child_b.exists()

    child_text = child_a.read_text(encoding="utf-8")
    assert "note_id: agent-harness-architecture" in child_text
    assert "derived_from: agent-harness" in child_text
    assert "# Architecture" in child_text

    parent_text = evergreen.read_text(encoding="utf-8")
    assert "## Derived Notes" in parent_text
    assert "[[agent-harness-architecture]]" in parent_text
    assert "[[agent-harness-usage-patterns]]" in parent_text

    refine_log = _read_jsonl(temp_vault / "60-Logs" / "refine-mutations.jsonl")
    pipeline_log = _read_jsonl(temp_vault / "60-Logs" / "pipeline.jsonl")
    assert refine_log[-1]["event_type"] == "refine_mutation_applied"
    assert refine_log[-1]["mode"] == "breakdown"
    assert refine_log[-1]["slug"] == "agent-harness"
    assert pipeline_log[-1]["event_type"] == "refine_run_completed"
    assert pipeline_log[-1]["mode"] == "breakdown"
    assert pipeline_log[-1]["applied_count"] == 1
    assert pipeline_log[-1]["canonical_refreshed"] is True

    registry_path = temp_vault / "10-Knowledge" / "Atlas" / "concept-registry.jsonl"
    atlas_index = temp_vault / "10-Knowledge" / "Atlas" / "Atlas-Index.md"
    assert registry_path.exists()
    assert atlas_index.exists()
    atlas_text = atlas_index.read_text(encoding="utf-8")
    assert "[[agent-harness-architecture|Architecture]]" in atlas_text
    assert "[[agent-harness-usage-patterns|Usage Patterns]]" in atlas_text
