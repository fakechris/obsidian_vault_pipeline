from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_absorb_help_includes_expected_arguments(capsys):
    from ovp_pipeline.commands.absorb import main

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
    from ovp_pipeline.commands.cleanup import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert "--slug" in captured.out
    assert "--all" in captured.out
    assert "--dry-run" in captured.out
    assert "--json" in captured.out


def test_breakdown_help_includes_expected_arguments(capsys):
    from ovp_pipeline.commands.breakdown import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert "--slug" in captured.out
    assert "--all" in captured.out
    assert "--dry-run" in captured.out
    assert "--json" in captured.out


def test_cleanup_dry_run_returns_json_proposals(temp_vault, capsys):
    from ovp_pipeline.commands.cleanup import main

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
    from ovp_pipeline.commands.breakdown import main

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
    from ovp_pipeline.commands.absorb import main

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
    from ovp_pipeline.commands import absorb

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


def test_absorb_file_under_clippings_runs_source_lifecycle_before_absorb(temp_vault, capsys, monkeypatch):
    from ovp_pipeline.commands import absorb

    clipping = temp_vault / "Clippings" / "Raw Clip.md"
    clipping.parent.mkdir(parents=True, exist_ok=True)
    clipping.write_text("# Raw Clip\n", encoding="utf-8")
    deep_dive = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Raw Clip_深度解读.md"

    def fake_run_source_lifecycle_for_absorb_targets(
        vault_dir: Path,
        targets: list[Path],
        *,
        dry_run: bool,
        failures: list[dict[str, str]] | None = None,
    ) -> list[Path]:
        assert vault_dir == temp_vault
        assert targets == [clipping]
        assert dry_run is False
        assert failures == []
        return [deep_dive]

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
        assert file_path == deep_dive
        assert directory is None
        assert recent is None
        assert dry_run is False
        return {
            "mode": "absorb",
            "dry_run": False,
            "summary": {
                "files_processed": 1,
                "concepts_extracted": 1,
                "candidates_added": 1,
                "concepts_promoted": 0,
                "concepts_created": 0,
                "concepts_skipped": 0,
                "errors": 0,
            },
            "results": [],
        }

    monkeypatch.setattr(
        absorb,
        "run_source_lifecycle_for_absorb_targets",
        fake_run_source_lifecycle_for_absorb_targets,
    )
    monkeypatch.setattr(absorb, "run_absorb_workflow", fake_run_absorb_workflow)

    result = absorb.main(["--vault-dir", str(temp_vault), "--file", str(clipping), "--json"])
    captured = capsys.readouterr()

    assert result == 0
    payload = json.loads(captured.out)
    assert payload["source_lifecycle"]["source_targets"] == [str(clipping)]
    assert payload["source_lifecycle"]["absorb_targets"] == [str(deep_dive)]


def test_move_clipping_to_raw_waits_for_real_destination(temp_vault):
    from ovp_pipeline.commands.absorb import _move_clipping_to_raw
    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(temp_vault)
    layout.clippings_dir.mkdir(parents=True, exist_ok=True)
    layout.raw_dir.mkdir(parents=True, exist_ok=True)
    clipping = layout.clippings_dir / "Async Clip.md"
    clipping.write_text("# Async Clip\n", encoding="utf-8")

    class FakeProcessor:
        def sanitize_filename(self, value: str) -> str:
            return value.replace(" ", "_")

        def obsidian_move(self, source: Path, dest_dir: Path, new_name: str | None = None) -> bool:
            return True

    moved = _move_clipping_to_raw(
        layout,
        FakeProcessor(),  # type: ignore[arg-type]
        clipping,
        settle_timeout_s=0.01,
    )

    assert moved.exists()
    assert moved.parent == layout.raw_dir
    assert not clipping.exists()


def test_absorb_dry_run_under_clippings_reports_source_lifecycle_plan(temp_vault, capsys):
    from ovp_pipeline.commands import absorb

    clipping = temp_vault / "Clippings" / "Raw Clip.md"
    clipping.parent.mkdir(parents=True, exist_ok=True)
    clipping.write_text("# Raw Clip\n", encoding="utf-8")

    result = absorb.main(["--vault-dir", str(temp_vault), "--file", str(clipping), "--dry-run", "--json"])
    captured = capsys.readouterr()

    assert result == 0
    payload = json.loads(captured.out)
    assert payload["source_lifecycle"]["required"] is True
    assert payload["source_lifecycle"]["source_targets"] == [str(clipping)]
    preview = payload["source_lifecycle"]["routing_preview"]
    assert preview["preview_schema_version"] == 1
    assert len(preview["items"]) == 1
    route = preview["items"][0]
    assert route["source"] == str(clipping)
    assert route["source_zone"] == "clippings"
    assert route["route"] == "clippings_to_raw_to_processing_to_deep_dive_absorb"
    assert route["processor"] == "auto_article_processor"
    assert route["will_mutate_on_execute"] is True
    assert [step["action"] for step in route["planned_actions"]] == [
        "move_to_raw",
        "stage_for_processing",
        "process_article",
        "archive_source_to_processed",
        "absorb_generated_deep_dive",
    ]
    assert route["planned_actions"][0]["target"].endswith("_Raw_Clip.md")
    assert route["planned_actions"][1]["target"].endswith("_Raw_Clip.md")
    assert route["planned_actions"][2]["target"] == "generated_deep_dive"
    assert clipping.exists()


def test_source_lifecycle_routing_preview_explains_raw_and_processing_routes(temp_vault):
    from ovp_pipeline.commands.absorb import build_source_lifecycle_routing_preview
    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(temp_vault)
    layout.raw_dir.mkdir(parents=True, exist_ok=True)
    layout.processing_dir.mkdir(parents=True, exist_ok=True)
    raw_source = layout.raw_dir / "2026-04-30_Raw_Article.md"
    processing_source = layout.processing_dir / "2026-04-29_Working_Article.md"
    raw_source.write_text("# Raw Article\n", encoding="utf-8")
    processing_source.write_text("# Working Article\n", encoding="utf-8")

    preview = build_source_lifecycle_routing_preview(layout, [raw_source, processing_source])

    routes = {item["source_zone"]: item for item in preview["items"]}
    assert routes["raw"]["route"] == "raw_to_processing_to_deep_dive_absorb"
    assert [step["action"] for step in routes["raw"]["planned_actions"]] == [
        "stage_for_processing",
        "process_article",
        "archive_source_to_processed",
        "absorb_generated_deep_dive",
    ]
    assert routes["raw"]["planned_actions"][0]["target"] == str(layout.processing_dir / raw_source.name)
    assert routes["processing"]["route"] == "processing_to_deep_dive_absorb"
    assert [step["action"] for step in routes["processing"]["planned_actions"]] == [
        "process_article",
        "archive_source_to_processed",
        "absorb_generated_deep_dive",
    ]
    assert raw_source.exists()
    assert processing_source.exists()


def test_source_lifecycle_routing_preview_reserves_duplicate_clipping_destinations(temp_vault):
    from ovp_pipeline.commands.absorb import build_source_lifecycle_routing_preview
    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(temp_vault)
    clipping_a = layout.clippings_dir / "a" / "Same Clip.md"
    clipping_b = layout.clippings_dir / "b" / "Same Clip.md"
    clipping_a.parent.mkdir(parents=True, exist_ok=True)
    clipping_b.parent.mkdir(parents=True, exist_ok=True)
    clipping_a.write_text("# Same Clip A\n", encoding="utf-8")
    clipping_b.write_text("# Same Clip B\n", encoding="utf-8")

    preview = build_source_lifecycle_routing_preview(layout, [layout.clippings_dir])

    raw_targets = [
        item["planned_actions"][0]["target"]
        for item in preview["items"]
        if item["source_zone"] == "clippings"
    ]
    assert len(raw_targets) == 2
    assert len(set(raw_targets)) == 2
    assert raw_targets[0].endswith("_Same_Clip.md")
    assert raw_targets[1].endswith("_Same_Clip-2.md")
    assert clipping_a.exists()
    assert clipping_b.exists()


def test_source_lifecycle_routing_preview_dedupes_overlapping_targets(temp_vault):
    from ovp_pipeline.commands.absorb import build_source_lifecycle_routing_preview
    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(temp_vault)
    clipping = layout.clippings_dir / "Overlap Clip.md"
    clipping.parent.mkdir(parents=True, exist_ok=True)
    clipping.write_text("# Overlap Clip\n", encoding="utf-8")

    preview = build_source_lifecycle_routing_preview(layout, [layout.clippings_dir, clipping])

    assert [item["source"] for item in preview["items"]] == [str(clipping)]
    assert clipping.exists()


def test_run_source_lifecycle_dry_run_does_not_move_clippings(temp_vault):
    from ovp_pipeline.commands.absorb import run_source_lifecycle_for_absorb_targets
    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(temp_vault)
    layout.clippings_dir.mkdir(parents=True, exist_ok=True)
    clipping = layout.clippings_dir / "Dry Run Clip.md"
    clipping.write_text("# Dry Run Clip\n", encoding="utf-8")

    targets = run_source_lifecycle_for_absorb_targets(temp_vault, [clipping], dry_run=True)

    assert targets == []
    assert clipping.exists()
    assert not any(layout.raw_dir.glob("*.md"))


def test_absorb_mixed_lifecycle_failure_keeps_direct_targets(temp_vault, capsys, monkeypatch):
    from ovp_pipeline.commands import absorb

    clipping = temp_vault / "Clippings" / "Raw Clip.md"
    clipping.parent.mkdir(parents=True, exist_ok=True)
    clipping.write_text("# Raw Clip\n", encoding="utf-8")
    topic_dir = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04"
    topic_dir.mkdir(parents=True, exist_ok=True)
    deep_dive = topic_dir / "Direct_深度解读.md"
    deep_dive.write_text("# Direct\n", encoding="utf-8")

    def fake_run_source_lifecycle_for_absorb_targets(
        vault_dir: Path,
        targets: list[Path],
        *,
        dry_run: bool,
        failures: list[dict[str, str]] | None = None,
    ) -> list[Path]:
        assert vault_dir == temp_vault
        assert targets == [clipping]
        assert dry_run is False
        if failures is not None:
            failures.append({"source": str(clipping), "stage": "process", "error": "failed"})
        return []

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
        assert file_path == deep_dive
        assert directory is None
        assert recent is None
        assert dry_run is False
        assert api_key is None
        assert api_base is None
        return {
            "mode": "absorb",
            "dry_run": False,
            "summary": {
                "files_processed": 1,
                "concepts_extracted": 1,
                "candidates_added": 0,
                "concepts_promoted": 0,
                "concepts_created": 0,
                "concepts_skipped": 0,
                "errors": 0,
            },
            "results": [],
        }

    monkeypatch.setattr(absorb, "run_source_lifecycle_for_absorb_targets", fake_run_source_lifecycle_for_absorb_targets)
    monkeypatch.setattr(absorb, "run_absorb_workflow", fake_run_absorb_workflow)

    result = absorb.main(
        [
            "--vault-dir",
            str(temp_vault),
            "--file",
            str(clipping),
            "--dir",
            str(topic_dir),
            "--json",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    payload = json.loads(captured.out)
    assert payload["source_lifecycle"]["absorb_targets"] == []
    assert payload["source_lifecycle"]["failures"][0]["stage"] == "process"


def test_absorb_file_and_dir_filters_direct_dir_to_deduped_deep_dives(temp_vault, monkeypatch):
    from ovp_pipeline.commands import absorb

    topic_dir = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04"
    topic_dir.mkdir(parents=True, exist_ok=True)
    deep_dive = topic_dir / "Direct_深度解读.md"
    ordinary_note = topic_dir / "ordinary.md"
    nested_deep_dive = topic_dir / "nested" / "Nested_深度解读.md"
    nested_deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text("# Direct\n", encoding="utf-8")
    ordinary_note.write_text("# Ordinary\n", encoding="utf-8")
    nested_deep_dive.write_text("# Nested\n", encoding="utf-8")
    calls: list[Path] = []

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
        assert file_path is not None
        assert directory is None
        assert recent is None
        assert dry_run is False
        assert api_key is None
        assert api_base is None
        calls.append(file_path)
        return {
            "mode": "absorb",
            "dry_run": False,
            "summary": {
                "files_processed": 1,
                "concepts_extracted": 1,
                "candidates_added": 0,
                "concepts_promoted": 0,
                "concepts_created": 0,
                "concepts_skipped": 0,
                "errors": 0,
            },
            "results": [],
        }

    monkeypatch.setattr(absorb, "run_absorb_workflow", fake_run_absorb_workflow)

    result = absorb.main(["--vault-dir", str(temp_vault), "--file", str(deep_dive), "--dir", str(topic_dir)])

    assert result == 0
    assert calls == [deep_dive]


def test_source_lifecycle_post_bl029_uses_archived_path_as_absorb_target(temp_vault, monkeypatch):
    """Post-BL-029 ``AutoArticleProcessor`` is intake-only:
    ``process_single_source`` returns ``status='intake_only'`` with
    the archived 03-Processed path in ``source_path``.  The lifecycle
    wrapper must pass that path through as the absorb target (no
    deep-dive synthesis between)."""
    from ovp_pipeline.commands import absorb
    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(temp_vault)
    layout.raw_dir.mkdir(parents=True, exist_ok=True)
    source = layout.raw_dir / "Article.md"
    source.write_text("# Article\n", encoding="utf-8")
    archived = layout.processed_dir / "2026-05" / "Article.md"

    class FakeAutoArticleProcessor:
        def __init__(self, vault_dir: Path, logger, txn):
            self.layout = VaultLayout.from_vault(vault_dir)
            self.logger = logger

        def process_single_source(self, file_path: Path, *, dry_run: bool) -> dict:
            assert file_path == source
            assert dry_run is False
            return {
                "status": "intake_only",
                "output_path": None,
                "source_path": str(archived),
            }

    monkeypatch.setattr(absorb, "AutoArticleProcessor", FakeAutoArticleProcessor)
    failures: list[dict[str, str]] = []

    targets = absorb.run_source_lifecycle_for_absorb_targets(
        temp_vault,
        [source],
        dry_run=False,
        failures=failures,
    )

    assert targets == [archived]
    assert failures == []


def test_source_lifecycle_records_failure_when_no_archive(temp_vault, monkeypatch):
    """When intake fails (e.g. url-dedup skip, or processor error
    leaves no archive), the source is reported in ``failures`` rather
    than silently dropped — caller surfaces partial results."""
    from ovp_pipeline.commands import absorb
    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(temp_vault)
    layout.raw_dir.mkdir(parents=True, exist_ok=True)
    source = layout.raw_dir / "Article.md"
    source.write_text("# Article\n", encoding="utf-8")

    class FakeAutoArticleProcessor:
        def __init__(self, vault_dir: Path, logger, txn):
            self.layout = VaultLayout.from_vault(vault_dir)
            self.logger = logger

        def process_single_source(self, file_path: Path, *, dry_run: bool) -> dict:
            return {
                "status": "skipped",
                "output_path": None,
                "source_path": None,
                "error": "url-dedup matched existing source",
            }

    monkeypatch.setattr(absorb, "AutoArticleProcessor", FakeAutoArticleProcessor)
    failures: list[dict[str, str]] = []

    targets = absorb.run_source_lifecycle_for_absorb_targets(
        temp_vault,
        [source],
        dry_run=False,
        failures=failures,
    )

    assert targets == []
    assert len(failures) == 1
    assert failures[0]["source"] == str(source)
    assert failures[0]["status"] == "skipped"


def test_source_lifecycle_passes_through_already_processed_files(temp_vault, monkeypatch):
    """A file already in 03-Processed needs no lifecycle move — it
    becomes the absorb target directly.  This is the common path
    after ``ovp --incremental`` has staged sources but absorb hasn't
    been run yet."""
    from ovp_pipeline.commands import absorb
    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(temp_vault)
    processed = layout.processed_dir / "2026-05"
    processed.mkdir(parents=True, exist_ok=True)
    source = processed / "Article.md"
    source.write_text("# Article\n", encoding="utf-8")

    class FakeAutoArticleProcessor:
        def __init__(self, vault_dir: Path, logger, txn):
            self.layout = VaultLayout.from_vault(vault_dir)
            self.logger = logger

        def process_single_source(self, file_path: Path, *, dry_run: bool) -> dict:
            raise AssertionError(
                "already-processed sources must not re-run the intake lifecycle"
            )

    monkeypatch.setattr(absorb, "AutoArticleProcessor", FakeAutoArticleProcessor)
    failures: list[dict[str, str]] = []

    targets = absorb.run_source_lifecycle_for_absorb_targets(
        temp_vault,
        [source],
        dry_run=False,
        failures=failures,
    )

    assert targets == [source]
    assert failures == []
