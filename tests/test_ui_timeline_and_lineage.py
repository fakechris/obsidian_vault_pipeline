"""Tests for the BL-058 follow-up UI surfaces:

* ``build_timeline_payload`` — daily digest of ``audit_events``
  surfaced at ``/ops/timeline``.
* ``_compute_v2_lineage`` (via ``build_note_page_payload``) — raw
  source ↔ evergreens ↔ clusters ↔ crystals chain card on
  ``/note?path=...``.
* ``_render_lineage_card`` — HTML output checks.
* ``_render_timeline_page`` — HTML output checks for empty / data
  / unavailable states.
* ``_ops_nav_items`` — confirms the post-BL-050 nav now exposes the
  routes that previously lived only in URLs.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ovp_pipeline.commands._ui_renderers import (
    _ops_nav_items,
    _render_lineage_card,
    _render_timeline_page,
)
from ovp_pipeline.ui.view_models import (
    build_note_page_payload,
    build_timeline_payload,
)


# ---------------------------------------------------------------------------
# Nav expansion
# ---------------------------------------------------------------------------


class TestOpsNav:
    def test_includes_workbench_pivots_and_browseables(self):
        items = dict(_ops_nav_items(""))
        # Post-BL-053 IA: by-time pivots first, then live + audit,
        # then browseables / queues.  Labels reflect BL-052 vocab
        # cleanup (``Audit`` → ``Events``, ``Candidates`` →
        # ``Concept Candidates``).
        for label in ("Today", "Runs", "Timeline", "Pulse", "Events",
                      "Evergreens", "Concept Candidates",
                      "Actions", "Contradictions", "Signals"):
            assert label in items, f"{label!r} missing from ops nav"
        assert items["Today"] == "/ops/today"
        assert items["Runs"] == "/ops/runs"
        assert items["Timeline"] == "/ops/timeline"
        assert items["Events"] == "/ops/events"
        assert items["Evergreens"] == "/ops/objects"

    def test_research_only_entries_gated(self, monkeypatch):
        # Research-pack items show only when the active pack supports
        # the research shell.  Pre-fix users saw broken links to
        # ``/ops/clusters`` etc. on packs that didn't support graph
        # synthesis.
        from ovp_pipeline.commands import _ui_renderers
        monkeypatch.setattr(
            _ui_renderers, "_shell_supports_research_nav", lambda _p: False,
        )
        items = dict(_ops_nav_items(""))
        assert "Clusters" not in items
        assert "Deep-dives" not in items

        monkeypatch.setattr(
            _ui_renderers, "_shell_supports_research_nav", lambda _p: True,
        )
        items_research = dict(_ops_nav_items(""))
        assert items_research["Clusters"] == "/ops/clusters"
        assert items_research["Deep-dives"] == "/ops/deep-dives"


# ---------------------------------------------------------------------------
# Timeline payload + renderer
# ---------------------------------------------------------------------------


_AUDIT_EVENTS_SCHEMA = """
CREATE TABLE audit_events (
  source_log TEXT NOT NULL,
  event_type TEXT NOT NULL,
  slug TEXT NOT NULL DEFAULT '',
  session_id TEXT NOT NULL DEFAULT '',
  timestamp TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL
);
CREATE INDEX idx_audit_events_log ON audit_events(source_log);
CREATE INDEX idx_audit_events_type ON audit_events(event_type);
"""


def _seed_audit_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "60-Logs" / "knowledge.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_AUDIT_EVENTS_SCHEMA)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    yesterday = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).strftime("%Y-%m-%dT%H:%M:%S")
    rows = [
        # Today: 3 evergreen promotions + 1 absorb error.
        ("pipeline.jsonl", "evergreen_auto_promoted", "", "s1", today,
         json.dumps({"slug": "alpha", "title": "Alpha"})),
        ("pipeline.jsonl", "evergreen_auto_promoted", "", "s1", today,
         json.dumps({"slug": "beta", "title": "Beta"})),
        ("pipeline.jsonl", "evergreen_auto_promoted", "", "s1", today,
         json.dumps({"slug": "gamma", "title": "Gamma"})),
        ("pipeline.jsonl", "absorb_parse_error", "", "s1", today,
         json.dumps({"source": "/path/to/source.md", "error": "JSON decode"})),
        # Yesterday: 2 github intakes.
        ("pipeline.jsonl", "github_intake_completed", "", "s0", yesterday,
         json.dumps({"url": "https://github.com/a/b", "tier": "deepwiki"})),
        ("pipeline.jsonl", "github_intake_completed", "", "s0", yesterday,
         json.dumps({"url": "https://github.com/c/d", "tier": "gitingest"})),
    ]
    conn.executemany(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)", rows,
    )
    conn.commit()
    conn.close()
    return db_path


class TestBuildTimelinePayload:
    def test_empty_db_returns_unavailable(self, tmp_path):
        # No knowledge.db at all → graceful degradation, not a crash.
        payload = build_timeline_payload(tmp_path)
        assert payload["screen"] == "ops/timeline"
        assert payload["available"] is False
        assert payload["days"] == []

    def test_groups_events_by_day(self, tmp_path):
        _seed_audit_db(tmp_path)
        payload = build_timeline_payload(tmp_path, days=7)
        assert payload["available"] is True
        # Today + yesterday — exactly 2 days with activity.
        assert len(payload["days"]) == 2
        # Reverse chronological — today first.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert payload["days"][0]["date"] == today
        assert payload["days"][0]["total"] == 4
        assert payload["days"][0]["by_type"]["evergreen_auto_promoted"] == 3
        assert payload["days"][0]["by_type"]["absorb_parse_error"] == 1

    def test_attaches_evergreen_samples_with_clickable_paths(self, tmp_path):
        _seed_audit_db(tmp_path)
        payload = build_timeline_payload(tmp_path, days=7)
        today = payload["days"][0]
        # 3 promoted today → up to DEFAULT_TIMELINE_SAMPLE_SIZE samples.
        assert {s["slug"] for s in today["samples"]} == {"alpha", "beta", "gamma"}
        # Each sample carries an /note?path= href the renderer can click.
        for s in today["samples"]:
            assert s["note_href"].startswith("/note?path=")
            assert "10-Knowledge%2FEvergreen" in s["note_href"]

    def test_attaches_error_samples(self, tmp_path):
        _seed_audit_db(tmp_path)
        payload = build_timeline_payload(tmp_path, days=7)
        today = payload["days"][0]
        assert any(
            e["event_type"] == "absorb_parse_error" for e in today["errors"]
        )
        # Subject pulled from payload_json.source field
        err = next(e for e in today["errors"] if e["event_type"] == "absorb_parse_error")
        assert "/path/to/source.md" in err["subject"]


class TestRenderTimelinePage:
    def test_renders_unavailable_state(self):
        html = _render_timeline_page({
            "screen": "ops/timeline", "requested_pack": "", "window_days": 14,
            "days": [], "available": False, "reason": "test",
        })
        assert "Timeline unavailable" in html
        assert "ovp-knowledge-index" in html

    def test_renders_empty_window(self):
        html = _render_timeline_page({
            "screen": "ops/timeline", "requested_pack": "", "window_days": 7,
            "days": [], "available": True,
        })
        assert "No events in the last 7 days" in html

    def test_renders_day_card_with_pills_and_samples(self):
        payload = {
            "screen": "ops/timeline",
            "requested_pack": "",
            "window_days": 7,
            "available": True,
            "highlighted_types": ["evergreen_auto_promoted", "absorb_parse_error"],
            "days": [{
                "date": "2026-05-06",
                "total": 5,
                "by_type": {
                    "evergreen_auto_promoted": 3,
                    "absorb_parse_error": 1,
                    "moc_updated": 1,
                },
                "samples": [
                    {"slug": "alpha", "title": "Alpha title",
                     "note_href": "/note?path=10-Knowledge%2FEvergreen%2Falpha.md"},
                ],
                "errors": [
                    {"event_type": "absorb_parse_error",
                     "subject": "bad.md", "snippet": "..."},
                ],
            }],
        }
        html = _render_timeline_page(payload)
        assert "2026-05-06" in html
        assert "5 events" in html
        # Highlighted types render with .highlight / .error CSS class
        assert "highlight" in html  # evergreen_auto_promoted
        assert "error" in html      # absorb_parse_error
        # Sample evergreen link present
        assert "Alpha title" in html
        assert "/note?path=10-Knowledge%2FEvergreen%2Falpha.md" in html
        # Error subject present
        assert "bad.md" in html


# ---------------------------------------------------------------------------
# Lineage card via build_note_page_payload + _render_lineage_card
# ---------------------------------------------------------------------------


_PAGES_INDEX_SCHEMA = """
CREATE TABLE pages_index (
  slug TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  note_type TEXT NOT NULL,
  path TEXT NOT NULL,
  day_id TEXT NOT NULL,
  frontmatter_json TEXT NOT NULL,
  body TEXT NOT NULL
);
CREATE TABLE graph_clusters (
  pack TEXT NOT NULL,
  cluster_id TEXT NOT NULL,
  cluster_kind TEXT NOT NULL,
  label TEXT NOT NULL,
  center_object_id TEXT NOT NULL,
  member_object_ids_json TEXT NOT NULL,
  score REAL NOT NULL DEFAULT 0.0,
  PRIMARY KEY (pack, cluster_id)
);
CREATE TABLE community_crystals (
  pack TEXT NOT NULL, cluster_id TEXT NOT NULL, body_md TEXT NOT NULL,
  source_evergreen_slugs_json TEXT NOT NULL, synthesized_at TEXT NOT NULL,
  llm_model TEXT NOT NULL, prompt_version TEXT NOT NULL,
  superseded_by_synthesized_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, cluster_id, synthesized_at)
);
CREATE TABLE contradiction_crystals (
  pack TEXT NOT NULL, contradiction_id TEXT NOT NULL,
  subject_key TEXT NOT NULL, body_md TEXT NOT NULL,
  positive_claim_ids_json TEXT NOT NULL, negative_claim_ids_json TEXT NOT NULL,
  source_object_ids_json TEXT NOT NULL, synthesized_at TEXT NOT NULL,
  llm_model TEXT NOT NULL, prompt_version TEXT NOT NULL,
  superseded_by_synthesized_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, contradiction_id, synthesized_at)
);
"""


def _seed_lineage_vault(tmp_path: Path) -> Path:
    """Build a minimal vault: 1 raw source + 2 evergreens linking back
    to it + 1 cluster + 1 community crystal."""
    vault = tmp_path / "vault"
    vault.mkdir()
    # Raw source
    raw_dir = vault / "50-Inbox" / "03-Processed" / "2026-04"
    raw_dir.mkdir(parents=True)
    raw_path = raw_dir / "2026-04-28_neuphonic_neutts.md"
    raw_path.write_text(
        "---\nsource_type: github-project\n---\n\n# NeuTTS\n",
        encoding="utf-8",
    )
    # Two evergreens that link back via ## Source
    eg_dir = vault / "10-Knowledge" / "Evergreen"
    eg_dir.mkdir(parents=True)
    for slug, title in [
        ("neutts-perth-watermarking", "NeuTTS Perth watermarking"),
        ("neutts-voice-cloning-3-15s", "NeuTTS voice cloning 3-15s"),
    ]:
        (eg_dir / f"{slug}.md").write_text(
            f"---\nnote_id: {slug}\ntitle: \"{title}\"\n"
            f"extraction_prompt_version: v2\n---\n\n"
            f"# {title}\n\nbody\n\n"
            f"## Source\n\n- [[2026-04-28_neuphonic_neutts]]\n",
            encoding="utf-8",
        )

    db_path = vault / "60-Logs" / "knowledge.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_PAGES_INDEX_SCHEMA)
    # Index the two evergreens — body must contain the wikilink so the
    # LIKE query in _compute_v2_lineage finds them.
    for slug, title in [
        ("neutts-perth-watermarking", "NeuTTS Perth watermarking"),
        ("neutts-voice-cloning-3-15s", "NeuTTS voice cloning 3-15s"),
    ]:
        conn.execute(
            "INSERT INTO pages_index VALUES (?, ?, ?, ?, ?, ?, ?)",
            (slug, title, "evergreen",
             str(eg_dir / f"{slug}.md"), "2026-05-06",
             "{}",
             f"# {title}\n\nbody\n\n## Source\n\n- [[2026-04-28_neuphonic_neutts]]\n"),
        )
    # One cluster containing both evergreens.
    conn.execute(
        "INSERT INTO graph_clusters VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("research-tech", "cluster::tts1", "louvain_community",
         "TTS / voice cloning", "neutts-perth-watermarking",
         json.dumps(["neutts-perth-watermarking", "neutts-voice-cloning-3-15s"]),
         2.0),
    )
    # One community crystal.
    conn.execute(
        "INSERT INTO community_crystals VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("research-tech", "cluster::tts1",
         "## body",
         json.dumps(["neutts-perth-watermarking", "neutts-voice-cloning-3-15s"]),
         "2026-05-06T00:00:00Z", "m", "v1", ""),
    )
    conn.commit()
    conn.close()
    return vault


class TestLineageFromEvergreen:
    def test_evergreen_traces_back_to_raw_source(self, tmp_path, monkeypatch):
        vault = _seed_lineage_vault(tmp_path)
        # Patch out get_note_provenance/traceability/inbound_capture
        # because those want a fully populated knowledge_index — we
        # only care about the new ``lineage`` field.
        from ovp_pipeline.ui import view_models as vm
        monkeypatch.setattr(vm, "get_note_provenance",
                            lambda *a, **k: {"original_source_note": None,
                                              "derived_deep_dives": []})
        monkeypatch.setattr(vm, "get_note_traceability",
                            lambda *a, **k: {
                                "note": {"title": "x", "path": "x"},
                                "source_notes": [], "deep_dives": [],
                                "objects": [], "atlas_pages": [],
                                "counts": {"deep_dives": 0, "objects": 0,
                                            "atlas_pages": 0},
                                "stage_label": "", "chain_status": "",
                                "chain_summary": "", "missing_stages": [],
                            })
        monkeypatch.setattr(vm, "get_note_inbound_capture_summary",
                            lambda *a, **k: {"summary": "", "items": []})

        payload = build_note_page_payload(
            vault,
            note_path="10-Knowledge/Evergreen/neutts-perth-watermarking.md",
        )
        lineage = payload["lineage"]
        assert lineage is not None
        assert lineage["kind"] == "evergreen"
        assert lineage["raw_source"]["slug"] == "2026-04-28_neuphonic_neutts"
        assert lineage["raw_source"]["path"] == \
            "50-Inbox/03-Processed/2026-04/2026-04-28_neuphonic_neutts.md"
        # Sibling evergreens — both notes that link to the same raw source
        slugs = [eg["slug"] for eg in lineage["evergreens"]]
        assert "neutts-perth-watermarking" in slugs
        assert "neutts-voice-cloning-3-15s" in slugs
        # Cluster present
        assert len(lineage["clusters"]) == 1
        assert lineage["clusters"][0]["label"] == "TTS / voice cloning"
        # Crystal present
        assert len(lineage["crystals"]) == 1
        assert lineage["crystals"][0]["kind"] == "community_crystal"

    def test_raw_source_traces_forward_to_evergreens(self, tmp_path, monkeypatch):
        vault = _seed_lineage_vault(tmp_path)
        from ovp_pipeline.ui import view_models as vm
        monkeypatch.setattr(vm, "get_note_provenance",
                            lambda *a, **k: {"original_source_note": None,
                                              "derived_deep_dives": []})
        monkeypatch.setattr(vm, "get_note_traceability",
                            lambda *a, **k: {
                                "note": {"title": "x", "path": "x"},
                                "source_notes": [], "deep_dives": [],
                                "objects": [], "atlas_pages": [],
                                "counts": {"deep_dives": 0, "objects": 0,
                                            "atlas_pages": 0},
                                "stage_label": "", "chain_status": "",
                                "chain_summary": "", "missing_stages": [],
                            })
        monkeypatch.setattr(vm, "get_note_inbound_capture_summary",
                            lambda *a, **k: {"summary": "", "items": []})

        payload = build_note_page_payload(
            vault,
            note_path="50-Inbox/03-Processed/2026-04/2026-04-28_neuphonic_neutts.md",
        )
        lineage = payload["lineage"]
        assert lineage is not None
        assert lineage["kind"] == "raw_source"
        # Raw source row points back at itself
        assert lineage["raw_source"]["slug"] == "2026-04-28_neuphonic_neutts"
        # Forward chain — both evergreens
        assert len(lineage["evergreens"]) == 2
        # Same cluster + crystal
        assert len(lineage["clusters"]) == 1
        assert len(lineage["crystals"]) == 1

    def test_non_evergreen_non_raw_returns_none(self, tmp_path, monkeypatch):
        vault = _seed_lineage_vault(tmp_path)
        # An MOC / atlas page is neither — lineage should be None so the
        # renderer suppresses the card.
        moc_dir = vault / "10-Knowledge" / "Atlas"
        moc_dir.mkdir(parents=True)
        (moc_dir / "moc.md").write_text("# MOC\n", encoding="utf-8")
        from ovp_pipeline.ui import view_models as vm
        monkeypatch.setattr(vm, "get_note_provenance",
                            lambda *a, **k: {"original_source_note": None,
                                              "derived_deep_dives": []})
        monkeypatch.setattr(vm, "get_note_traceability",
                            lambda *a, **k: {
                                "note": {"title": "x", "path": "x"},
                                "source_notes": [], "deep_dives": [],
                                "objects": [], "atlas_pages": [],
                                "counts": {"deep_dives": 0, "objects": 0,
                                            "atlas_pages": 0},
                                "stage_label": "", "chain_status": "",
                                "chain_summary": "", "missing_stages": [],
                            })
        monkeypatch.setattr(vm, "get_note_inbound_capture_summary",
                            lambda *a, **k: {"summary": "", "items": []})

        payload = build_note_page_payload(
            vault, note_path="10-Knowledge/Atlas/moc.md",
        )
        assert payload["lineage"] is None


class TestRenderLineageCard:
    def test_none_returns_empty_string(self):
        assert _render_lineage_card(None) == ""

    def test_evergreen_chain_renders_all_blocks(self):
        html = _render_lineage_card({
            "kind": "evergreen",
            "raw_source": {
                "slug": "2026-04-28_neuphonic_neutts",
                "path": "50-Inbox/03-Processed/2026-04/2026-04-28_neuphonic_neutts.md",
                "note_href": "/note?path=...",
            },
            "evergreens": [
                {"slug": "a", "title": "A", "note_href": "/note?a"},
                {"slug": "b", "title": "B", "note_href": "/note?b"},
            ],
            "clusters": [
                {"cluster_id": "cluster::aa", "label": "Topic A",
                 "member_count": 5, "matched": ["a"],
                 "cluster_href": "/ops/cluster?id=...",
                 "crystal_note_href": "/note?path=40-Resources..."},
            ],
            "crystals": [
                {"kind": "community_crystal", "crystal_id": "cluster::aa",
                 "label": "cluster::aa", "note_href": "/note?path=..."},
            ],
        })
        assert "Lineage" in html
        assert "Raw source" in html
        assert "2026-04-28_neuphonic_neutts" in html
        assert "Evergreens" in html
        assert "Clusters" in html
        assert "Topic A" in html
        assert "Crystals" in html
        assert "produced 2 evergreen(s)" in html
        assert "grouped into 1 cluster(s)" in html
        assert "synthesized into 1 crystal(s)" in html

    def test_archived_raw_source_shows_muted_note(self):
        # When the raw source can't be located on disk (archived) the
        # card still renders the stem with a hint.
        html = _render_lineage_card({
            "kind": "evergreen",
            "raw_source": {"slug": "old-source", "path": "", "note_href": ""},
            "evergreens": [],
            "clusters": [],
            "crystals": [],
        })
        assert "old-source" in html
        assert "archived" in html


# ---------------------------------------------------------------------------
# BL-053: /ops/today, /ops/runs, /ops/runs/<txn_id>
# ---------------------------------------------------------------------------


def _seed_run_db(tmp_path: Path) -> Path:
    """Seed an audit DB with two transactions worth of events.

    Run A (txn-A, completed): clippings_processed + 2
    article_intake_only + 1 absorb_parse_error.

    Run B (txn-B, no completion → running): 1 article_intake_only.
    """
    db_path = tmp_path / "60-Logs" / "knowledge.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_AUDIT_EVENTS_SCHEMA)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    later = (
        datetime.now(timezone.utc) + timedelta(seconds=30)
    ).strftime("%Y-%m-%dT%H:%M:%S")
    rows = [
        # Run A: bracketed by transaction_started + transaction_completed.
        ("pipeline.jsonl", "transaction_started", "", "sess-A", today,
         json.dumps({"txn_id": "txn-A", "type": "article-processing"})),
        ("pipeline.jsonl", "clippings_processed", "", "sess-A", today,
         json.dumps({"scanned": 2, "migrated": 2})),
        ("pipeline.jsonl", "article_intake_only", "", "sess-A", today,
         json.dumps({"file": "alpha.md", "source_url": "https://x.com/a"})),
        ("pipeline.jsonl", "article_intake_only", "", "sess-A", today,
         json.dumps({"file": "beta.md", "source_url": "https://x.com/b"})),
        ("pipeline.jsonl", "absorb_parse_error", "", "sess-A", today,
         json.dumps({"source": "/path/to/broken.md", "error": "JSON decode"})),
        ("pipeline.jsonl", "transaction_completed", "", "sess-A", later,
         json.dumps({"txn_id": "txn-A", "results": {"completed": 2}})),
        # Run B: started but not completed.
        ("pipeline.jsonl", "transaction_started", "", "sess-B", today,
         json.dumps({"txn_id": "txn-B", "type": "github-redo"})),
        ("pipeline.jsonl", "article_intake_only", "", "sess-B", today,
         json.dumps({"file": "gamma.md"})),
    ]
    conn.executemany("INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return db_path


class TestBuildTodayDigestPayload:
    def test_unavailable_when_db_missing(self, tmp_path):
        from ovp_pipeline.ui.view_models import build_today_digest_payload
        payload = build_today_digest_payload(tmp_path)
        assert payload["screen"] == "ops/today"
        assert payload["available"] is False
        assert payload["cards"] == []

    def test_groups_events_into_five_cards(self, tmp_path):
        from ovp_pipeline.ui.view_models import build_today_digest_payload
        _seed_run_db(tmp_path)
        payload = build_today_digest_payload(tmp_path)
        assert payload["available"] is True
        # Five cards in canonical order.
        assert [c["id"] for c in payload["cards"]] == [
            "intake", "absorb", "synthesis", "governance", "failures",
        ]
        # Intake card carries clippings_processed + article_intake_only events.
        intake = payload["cards"][0]
        assert intake["total"] >= 4  # 1 clippings_processed + 3 intake_only
        assert intake["by_type"].get("clippings_processed") == 1
        assert intake["by_type"].get("article_intake_only") == 3
        # Failures card surfaces the absorb_parse_error.
        failures = payload["cards"][-1]
        assert failures["by_type"].get("absorb_parse_error") == 1
        # Each card with non-zero total has at least one sample.
        for card in payload["cards"]:
            if card["total"] > 0:
                assert card["samples"], f"{card['id']} has no samples"

    def test_target_date_overrides_today(self, tmp_path):
        from ovp_pipeline.ui.view_models import build_today_digest_payload
        _seed_run_db(tmp_path)
        # Future date → no events.
        future = "2099-01-01"
        payload = build_today_digest_payload(tmp_path, target_date=future)
        assert payload["date"] == future
        assert all(c["total"] == 0 for c in payload["cards"])


class TestBuildRunsIndexPayload:
    def test_unavailable_when_db_missing(self, tmp_path):
        from ovp_pipeline.ui.view_models import build_runs_index_payload
        payload = build_runs_index_payload(tmp_path)
        assert payload["available"] is False
        assert payload["runs"] == []

    def test_lists_runs_with_status(self, tmp_path):
        from ovp_pipeline.ui.view_models import build_runs_index_payload
        _seed_run_db(tmp_path)
        payload = build_runs_index_payload(tmp_path)
        assert payload["available"] is True
        # Two transactions seeded.
        txn_ids = {r["txn_id"] for r in payload["runs"]}
        assert txn_ids == {"txn-A", "txn-B"}
        # Run A completed; Run B running (started just now, < 6h cutoff).
        by_id = {r["txn_id"]: r for r in payload["runs"]}
        assert by_id["txn-A"]["status"] == "completed"
        assert by_id["txn-B"]["status"] == "running"
        # Workflow type pulled from payload.
        assert by_id["txn-A"]["workflow_type"] == "article-processing"
        assert by_id["txn-B"]["workflow_type"] == "github-redo"
        # Detail href points at /ops/runs/<txn_id>
        assert by_id["txn-A"]["detail_href"].endswith("/ops/runs/txn-A")

    def test_limit_caps_results(self, tmp_path):
        from ovp_pipeline.ui.view_models import build_runs_index_payload
        _seed_run_db(tmp_path)
        payload = build_runs_index_payload(tmp_path, limit=1)
        assert len(payload["runs"]) == 1


class TestBuildRunDetailPayload:
    def test_returns_unavailable_for_unknown_txn(self, tmp_path):
        from ovp_pipeline.ui.view_models import build_run_detail_payload
        _seed_run_db(tmp_path)
        payload = build_run_detail_payload(tmp_path, "txn-does-not-exist")
        assert payload["available"] is False
        assert "no events" in str(payload.get("reason", "")).lower()

    def test_returns_session_scoped_events_for_known_txn(self, tmp_path):
        from ovp_pipeline.ui.view_models import build_run_detail_payload
        _seed_run_db(tmp_path)
        payload = build_run_detail_payload(tmp_path, "txn-A")
        assert payload["available"] is True
        assert payload["txn_id"] == "txn-A"
        assert payload["workflow_type"] == "article-processing"
        # All 6 events for sess-A surface (the session_id JOIN strategy
        # picks them up even though only the bracketing rows tag txn_id).
        event_types = [e["event_type"] for e in payload["events"]]
        assert "transaction_started" in event_types
        assert "clippings_processed" in event_types
        assert event_types.count("article_intake_only") == 2
        assert "absorb_parse_error" in event_types
        assert "transaction_completed" in event_types
        # Chronological order: started first, completed last.
        assert payload["events"][0]["event_type"] == "transaction_started"
        assert payload["events"][-1]["event_type"] == "transaction_completed"

    def test_empty_txn_id_returns_unavailable(self, tmp_path):
        from ovp_pipeline.ui.view_models import build_run_detail_payload
        payload = build_run_detail_payload(tmp_path, "")
        assert payload["available"] is False
        assert payload["txn_id"] == ""


class TestBL053OpsNav:
    """The IA shift surfaces ``Today`` and ``Runs`` ahead of the
    long-window ``Timeline``; ``Audit`` label renamed to
    ``Events`` per BL-052; ``Candidates`` label renamed to
    ``Concept Candidates``.
    """

    def test_nav_includes_today_and_runs(self):
        items = dict(_ops_nav_items(""))
        assert items["Today"] == "/ops/today"
        assert items["Runs"] == "/ops/runs"
        # Timeline still present (multi-day complement).
        assert items["Timeline"] == "/ops/timeline"

    def test_nav_renames_audit_to_events(self):
        items = dict(_ops_nav_items(""))
        assert "Audit" not in items
        assert items["Events"] == "/ops/events"

    def test_nav_renames_candidates_label(self):
        items = dict(_ops_nav_items(""))
        # URL stays the same; label changes.
        assert items.get("Concept Candidates") == "/ops/candidates"
        assert "Candidates" not in items

    def test_nav_ordering_workbench_pivots_first(self):
        items = _ops_nav_items("")
        labels = [label for label, _ in items]
        # The IA shift puts the by-time pivots ahead of the live
        # tail and browseables.  Overview stays as the workbench
        # root link.
        assert labels.index("Overview") < labels.index("Today")
        assert labels.index("Today") < labels.index("Runs")
        assert labels.index("Runs") < labels.index("Timeline")
        assert labels.index("Timeline") < labels.index("Events")
