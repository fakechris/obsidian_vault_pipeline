from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


def test_absorb_help_includes_expected_arguments(capsys):
    from openclaw_pipeline.commands.absorb import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert "--file" in captured.out
    assert "--dir" in captured.out
    assert "--recent" in captured.out
    assert "--auto-promote" in captured.out
    assert "--json" in captured.out


def test_cleanup_help_includes_expected_arguments(capsys):
    from openclaw_pipeline.commands.cleanup import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert "--slug" in captured.out
    assert "--all" in captured.out
    assert "--dry-run" in captured.out
    assert "--json" in captured.out


def test_breakdown_help_includes_expected_arguments(capsys):
    from openclaw_pipeline.commands.breakdown import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert "--slug" in captured.out
    assert "--all" in captured.out
    assert "--dry-run" in captured.out
    assert "--json" in captured.out


def test_cleanup_dry_run_returns_json_proposals(temp_vault, capsys):
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

## 2026-01
Something happened.

## 2026-02
Another event happened.
""",
        encoding="utf-8",
    )

    result = main(["--vault-dir", str(temp_vault), "--slug", "messy-note", "--dry-run", "--json"])
    captured = capsys.readouterr()

    assert result == 0
    payload = json.loads(captured.out)
    assert payload["mode"] == "cleanup"
    assert payload["targets"] == ["messy-note"]


def test_breakdown_dry_run_returns_json_proposals(temp_vault, capsys):
    from openclaw_pipeline.commands.breakdown import main

    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "big-note.md"
    evergreen.write_text(
        """---
note_id: big-note
title: Big Note
type: evergreen
date: 2026-04-07
---

# Big Note

## Part A
Line 1
Line 2
Line 3
Line 4
Line 5
Line 6
Line 7
Line 8
Line 9
Line 10
Line 11
Line 12
Line 13
Line 14
Line 15
Line 16
Line 17
Line 18
Line 19
Line 20
Line 21
Line 22
Line 23
Line 24
Line 25

## Part B
More lines
More lines
More lines
More lines
More lines
More lines
More lines
More lines
More lines
More lines
""",
        encoding="utf-8",
    )

    result = main(["--vault-dir", str(temp_vault), "--slug", "big-note", "--dry-run", "--json"])
    captured = capsys.readouterr()

    assert result == 0
    payload = json.loads(captured.out)
    assert payload["mode"] == "breakdown"
    assert payload["targets"] == ["big-note"]


def test_absorb_dry_run_deep_dive_file_returns_zero(temp_vault):
    from openclaw_pipeline.commands.absorb import main

    source_file = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04-07_Test_深度解读.md"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(
        """---
title: Test
type: deep_dive
date: 2026-04-07
---

# Test
""",
        encoding="utf-8",
    )

    result = main(["--vault-dir", str(temp_vault), "--file", str(source_file), "--dry-run", "--json"])

    assert result == 0


def test_absorb_json_non_dry_run_emits_structured_summary(temp_vault, capsys, monkeypatch):
    from openclaw_pipeline.commands import absorb

    source_file = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04-07_Test_深度解读.md"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(
        """---
title: Test
type: deep_dive
date: 2026-04-07
---

# Test
""",
        encoding="utf-8",
    )

    def fake_run_absorb_workflow(
        vault_dir: Path,
        *,
        file_path: Path | None = None,
        directory: Path | None = None,
        recent: int | None = None,
        dry_run: bool = False,
        auto_promote: bool = False,
        promote_threshold: int = 3,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> dict:
        assert vault_dir == temp_vault
        assert file_path == source_file
        assert directory is None
        assert recent is None
        assert dry_run is False
        assert auto_promote is True
        assert promote_threshold == 4
        assert api_key is None
        assert api_base is None
        return {
            "mode": "absorb",
            "dry_run": False,
            "summary": {
                "files_processed": 1,
                "concepts_extracted": 3,
                "candidates_added": 2,
                "concepts_promoted": 1,
                "concepts_created": 1,
                "concepts_skipped": 0,
                "errors": 0,
            },
        }

    monkeypatch.setattr(absorb, "run_absorb_workflow", fake_run_absorb_workflow)

    result = absorb.main(
        [
            "--vault-dir",
            str(temp_vault),
            "--file",
            str(source_file),
            "--auto-promote",
            "--promote-threshold",
            "4",
            "--json",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    payload = json.loads(captured.out)
    assert payload["mode"] == "absorb"
    assert payload["dry_run"] is False
    assert payload["summary"]["files_processed"] == 1
    assert payload["summary"]["concepts_promoted"] == 1
