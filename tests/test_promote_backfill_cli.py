"""Tests for ``ovp-promote-backfill`` (Phase 38.C)."""

from __future__ import annotations

import json
from pathlib import Path

from ovp_pipeline.commands.promote_backfill import main as backfill_main
from ovp_pipeline.promotion_backlinks import list_promotions


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _write_pipeline_log(vault: Path, events: list[dict]) -> None:
    log_path = vault / "60-Logs" / "pipeline.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
        encoding="utf-8",
    )


def test_backfill_writes_block_into_source(tmp_path: Path):
    vault = tmp_path / "vault"
    src = vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "topic.md"
    _write(src, "# Topic\n\nbody\n")
    _write(vault / "10-Knowledge" / "Evergreen" / "Concept-A.md", "evergreen\n")

    _write_pipeline_log(
        vault,
        [
            {
                "event_type": "evergreen_auto_promoted",
                "concept": "Concept-A",
                "source": "topic.md",
            }
        ],
    )

    rc = backfill_main(["--vault-dir", str(vault)])
    assert rc == 0
    assert list_promotions(src.read_text(encoding="utf-8")) == ["Concept-A"]


def test_backfill_dry_run_does_not_write(tmp_path: Path):
    vault = tmp_path / "vault"
    src = vault / "20-Areas" / "topic.md"
    _write(src, "body\n")
    _write(vault / "10-Knowledge" / "Evergreen" / "X.md", "x\n")
    _write_pipeline_log(
        vault,
        [{"event_type": "evergreen_auto_promoted", "concept": "X", "source": "topic.md"}],
    )

    rc = backfill_main(["--vault-dir", str(vault), "--dry-run"])
    assert rc == 0
    assert list_promotions(src.read_text(encoding="utf-8")) == []


def test_backfill_groups_concepts_per_source(tmp_path: Path):
    vault = tmp_path / "vault"
    src = vault / "20-Areas" / "topic.md"
    _write(src, "body\n")
    _write_pipeline_log(
        vault,
        [
            {"event_type": "evergreen_auto_promoted", "concept": "A", "source": "topic.md"},
            {"event_type": "evergreen_auto_promoted", "concept": "B", "source": "topic.md"},
            {"event_type": "evergreen_created", "concept": "C", "source": "topic.md"},
        ],
    )

    rc = backfill_main(["--vault-dir", str(vault)])
    assert rc == 0
    slugs = list_promotions(src.read_text(encoding="utf-8"))
    assert sorted(slugs) == ["A", "B", "C"]


def test_backfill_handles_missing_source(tmp_path: Path):
    vault = tmp_path / "vault"
    (vault / "20-Areas").mkdir(parents=True)
    _write_pipeline_log(
        vault,
        [
            {
                "event_type": "evergreen_auto_promoted",
                "concept": "X",
                "source": "nonexistent.md",
            }
        ],
    )
    # Should still exit 0; missing-source is reported, not raised.
    rc = backfill_main(["--vault-dir", str(vault)])
    assert rc == 0


def test_backfill_idempotent(tmp_path: Path):
    vault = tmp_path / "vault"
    src = vault / "20-Areas" / "topic.md"
    _write(src, "body\n")
    _write_pipeline_log(
        vault,
        [{"event_type": "evergreen_auto_promoted", "concept": "X", "source": "topic.md"}],
    )

    backfill_main(["--vault-dir", str(vault)])
    after_first = src.read_text(encoding="utf-8")
    backfill_main(["--vault-dir", str(vault)])
    after_second = src.read_text(encoding="utf-8")
    assert after_first == after_second
