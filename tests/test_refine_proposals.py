from openclaw_pipeline.auto_evergreen_extractor import build_extraction_summary
from openclaw_pipeline.refine import analyze_breakdown, analyze_cleanup


def test_analyze_cleanup_flags_diary_driven_sections(temp_vault):
    note = temp_vault / "10-Knowledge" / "Evergreen" / "messy-note.md"
    note.write_text(
        """---
note_id: messy-note
title: Messy Note
type: evergreen
date: 2026-04-07
---

# Messy Note

## 2026-01
Something happened.

## 2026-02
Another event happened.
""",
        encoding="utf-8",
    )

    proposal = analyze_cleanup(note)

    assert proposal["decision_type"] == "rewrite_decision"
    assert proposal["action"] == "cleanup_rewrite"
    assert any("date-driven" in reason for reason in proposal["reasons"])


def test_analyze_breakdown_derives_child_slugs_from_headings(temp_vault):
    note = temp_vault / "10-Knowledge" / "Evergreen" / "agent-harness.md"
    note.write_text(
        """---
note_id: agent-harness
title: Agent Harness
type: evergreen
date: 2026-04-07
---

# Agent Harness

## Architecture
One
Two
Three
Four
Five
Six
Seven
Eight
Nine
Ten
Eleven
Twelve
Thirteen
Fourteen
Fifteen
Sixteen
Seventeen
Eighteen
Nineteen
Twenty

## Usage Patterns
One
Two
Three
Four
Five
Six
Seven
Eight
Nine
Ten
Eleven
Twelve
Thirteen
Fourteen
Fifteen
Sixteen
Seventeen
Eighteen
Nineteen
Twenty
""",
        encoding="utf-8",
    )

    proposal = analyze_breakdown(note)

    assert proposal["decision_type"] == "split_decision"
    assert proposal["action"] == "split"
    assert proposal["proposed_children"] == [
        "agent-harness-architecture",
        "agent-harness-usage-patterns",
    ]


def test_build_extraction_summary_aggregates_counts():
    payload = build_extraction_summary(
        [
            {
                "file": "a.md",
                "concepts_extracted": 2,
                "candidates_added": 1,
                "concepts_promoted": 1,
                "concepts_created": 1,
                "concepts_skipped": 0,
                "concepts": [{"name": "one"}],
            },
            {
                "file": "b.md",
                "concepts_extracted": 1,
                "candidates_added": 0,
                "concepts_promoted": 0,
                "concepts_created": 0,
                "concepts_skipped": 1,
                "error": "failed",
                "concepts": [],
            },
        ],
        dry_run=False,
        auto_promote=True,
        promote_threshold=4,
        source_scope={"file": None, "dir": None, "recent": 7},
    )

    assert payload["mode"] == "absorb"
    assert payload["dry_run"] is False
    assert payload["auto_promote"] is True
    assert payload["promote_threshold"] == 4
    assert payload["summary"] == {
        "files_processed": 2,
        "concepts_extracted": 3,
        "candidates_added": 1,
        "concepts_promoted": 1,
        "concepts_created": 1,
        "concepts_skipped": 1,
        "errors": 1,
    }
