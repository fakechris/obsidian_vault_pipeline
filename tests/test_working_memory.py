"""Phase 38 — Working Memory daily distill.

Coverage:
* empty vault → file exists with all five section headers and (none) bodies
* date override writes to the correct path
* fresh Crystals appear in the Fresh Crystals section
* Top of Mind respects citation_count from page_metrics
* EVOLVES Today groups events by subtype within the lookback window
* Pulse Highlights counts pipeline.jsonl events in the window
* CLI runs end-to-end on an empty vault
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from ovp_pipeline.commands.working_memory import (
    WORKING_MEMORY_DIR,
    build_working_memory,
    main as working_memory_main,
)
from ovp_pipeline.knowledge_index import rebuild_knowledge_index
from ovp_pipeline.runtime import VaultLayout


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_empty_vault_renders_all_section_headers(temp_vault):
    output = build_working_memory(temp_vault, target_date=date(2026, 4, 24))
    text = output.read_text(encoding="utf-8")

    assert output.parent == temp_vault.joinpath(*WORKING_MEMORY_DIR)
    assert output.name == "2026-04-24.md"
    assert text.startswith("---\n")
    assert "projection_kind: context_pack_projection" in text
    assert "projection_surface: working_memory" in text
    assert "projection_layer: Layer 3" in text
    assert "# Working Memory — 2026-04-24" in text
    for section in (
        "## Top of Mind",
        "## Fresh Crystals",
        "## Pending Decisions",
        "## EVOLVES Today",
        "## Pulse Highlights",
    ):
        assert section in text
    # All five sections empty on a clean vault → five "(none)" markers.
    assert text.count("- (none)") == 5


def test_date_override_writes_to_correct_path(temp_vault):
    output = build_working_memory(temp_vault, target_date=date(2025, 12, 31))
    assert output.name == "2025-12-31.md"


def test_fresh_crystals_section_lists_recent_files(temp_vault):
    crystals_dir = temp_vault / "40-Resources" / "Crystals"
    crystals_dir.mkdir(parents=True)
    crystal = crystals_dir / "crystal-2026-04-24-abcd1234.md"
    crystal.write_text(
        "---\ncrystal_id: crystal-2026-04-24-abcd1234\ndate: 2026-04-24\n---\n\n# Crystal\n",
        encoding="utf-8",
    )

    now = datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)
    output = build_working_memory(temp_vault, target_date=now.date(), now=now)
    text = output.read_text(encoding="utf-8")
    assert "[[crystal-2026-04-24-abcd1234]]" in text


def test_top_of_mind_uses_page_metrics(temp_vault):
    """Seed page_metrics so we can assert the ordering deterministically."""
    eg = temp_vault / "10-Knowledge" / "Evergreen"
    (eg / "Hot.md").write_text(
        "---\nnote_id: hot\ntitle: Hot\ntype: evergreen\ndate: 2026-04-24\n---\n# Hot\n",
        encoding="utf-8",
    )
    (eg / "Cold.md").write_text(
        "---\nnote_id: cold\ntitle: Cold\ntype: evergreen\ndate: 2026-04-24\n---\n# Cold\n",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    now = datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)
    db = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO page_metrics "
            "(slug, last_seen_ts, reuse_count, citation_count) VALUES (?, ?, ?, ?)",
            [
                ("hot", int(now.timestamp()), 5, 12),
                ("cold", int(now.timestamp()), 1, 1),
            ],
        )
        conn.commit()

    output = build_working_memory(temp_vault, target_date=now.date(), now=now)
    text = output.read_text(encoding="utf-8")
    # Hot (12 citations) must appear before Cold (1 citation) in the section.
    hot_idx = text.find("[[hot]]")
    cold_idx = text.find("[[cold]]")
    assert hot_idx != -1 and cold_idx != -1
    assert hot_idx < cold_idx


def test_evolves_today_groups_by_subtype(temp_vault):
    """Two recent and one stale relation_promoted; only the recent ones land
    in the section, grouped by subtype."""
    layout = VaultLayout.from_vault(temp_vault)
    layout.logs_dir.mkdir(parents=True, exist_ok=True)
    log = layout.logs_dir / "relation-promotions.jsonl"

    now = datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)
    recent_ts = _ts(now - timedelta(hours=2))
    stale_ts = _ts(now - timedelta(days=5))

    log.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {
                    "ts": recent_ts,
                    "event_type": "relation_promoted",
                    "relation_type": "evolves:replaces",
                    "source_object_id": "rag",
                    "target_object_id": "vanilla-retrieval",
                },
                {
                    "ts": recent_ts,
                    "event_type": "relation_promoted",
                    "relation_type": "evolves:enriches",
                    "source_object_id": "agent",
                    "target_object_id": "rag",
                },
                {
                    "ts": stale_ts,
                    "event_type": "relation_promoted",
                    "relation_type": "evolves:replaces",
                    "source_object_id": "old",
                    "target_object_id": "older",
                },
                {
                    "ts": recent_ts,
                    "event_type": "relation_promoted",
                    "relation_type": "supports",  # not an evolves, ignored
                    "source_object_id": "x",
                    "target_object_id": "y",
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output = build_working_memory(temp_vault, target_date=now.date(), now=now)
    text = output.read_text(encoding="utf-8")

    assert "### replaces (1)" in text
    assert "### enriches (1)" in text
    assert "[[rag]] → [[vanilla-retrieval]]" in text
    assert "[[agent]] → [[rag]]" in text
    # Stale 5-days-ago event must NOT appear.
    assert "[[old]] → [[older]]" not in text
    # Non-evolves event must NOT appear.
    assert "[[x]] → [[y]]" not in text


def test_pulse_highlights_counts_recent_events(temp_vault):
    layout = VaultLayout.from_vault(temp_vault)
    layout.logs_dir.mkdir(parents=True, exist_ok=True)

    now = datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)
    recent_ts = _ts(now - timedelta(hours=1))
    stale_ts = _ts(now - timedelta(days=3))

    layout.pipeline_log.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {"ts": recent_ts, "event_type": "article_processed"},
                {"ts": recent_ts, "event_type": "article_processed"},
                {"ts": recent_ts, "event_type": "evergreen_absorbed"},
                {"ts": stale_ts, "event_type": "article_processed"},  # outside window
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output = build_working_memory(temp_vault, target_date=now.date(), now=now)
    text = output.read_text(encoding="utf-8")

    assert "| article_processed | 2 |" in text
    assert "| evergreen_absorbed | 1 |" in text


def test_cli_runs_end_to_end_on_empty_vault(temp_vault, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["ovp-working-memory", "--vault-dir", str(temp_vault), "--date", "2026-04-24", "--json"],
    )
    rc = working_memory_main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "2026-04-24.md" in out
    assert (temp_vault / "60-Logs" / "working-memory" / "2026-04-24.md").exists()
