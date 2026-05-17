"""Tests for ``ovp-refresh-ops`` — the codified post-absorb
lifecycle refresh that replaces the manual "do I need a full
rebuild?" judgement.

Locks the operator-defined rule:
* candidate/source evidence only → exit 0, "full rebuild NOT
  needed".
* canonical-object evidence (evergreen_auto_promoted /
  promote_concept / evergreen_created) in the window → exit 2,
  explicit WARNING that a heavier rebuild may be warranted.

``sync_audit_events_from_jsonl`` is patched: refresh_ops's job is
orchestration + the decision rule, not re-testing knowledge_index's
audit ingest (which has its own suite).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from ovp_pipeline.commands import refresh_ops
from ovp_pipeline.ops_lifecycle import ALL_STATES

PACK = "research-tech"

_SCHEMA = """
CREATE TABLE audit_events (
    source_log TEXT NOT NULL,
    event_type TEXT NOT NULL,
    slug TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL
);
CREATE TABLE objects (
    pack TEXT NOT NULL, object_id TEXT NOT NULL, object_kind TEXT NOT NULL,
    title TEXT NOT NULL, canonical_path TEXT NOT NULL, source_slug TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '', PRIMARY KEY (pack, object_id)
);
CREATE TABLE graph_clusters (
    pack TEXT NOT NULL, cluster_id TEXT NOT NULL, cluster_kind TEXT NOT NULL,
    label TEXT NOT NULL, center_object_id TEXT NOT NULL,
    member_object_ids_json TEXT NOT NULL, score REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (pack, cluster_id)
);
CREATE TABLE community_crystals (
    pack TEXT NOT NULL, cluster_id TEXT NOT NULL, body_md TEXT NOT NULL,
    source_evergreen_slugs_json TEXT NOT NULL, synthesized_at TEXT NOT NULL,
    llm_model TEXT NOT NULL, prompt_version TEXT NOT NULL,
    superseded_by_synthesized_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (pack, cluster_id, synthesized_at)
);
CREATE TABLE evergreen_revisions (
    pack TEXT NOT NULL, object_id TEXT NOT NULL, version INTEGER NOT NULL,
    content_md TEXT NOT NULL, change_type TEXT NOT NULL,
    changed_by TEXT NOT NULL DEFAULT '', derived_at TEXT NOT NULL,
    change_note TEXT NOT NULL DEFAULT '', PRIMARY KEY (pack, object_id, version)
);
CREATE TABLE truth_projections (
    pack TEXT NOT NULL, owner_pack TEXT NOT NULL DEFAULT '',
    builder_name TEXT NOT NULL DEFAULT '', built_at TEXT NOT NULL
);
"""


def _stamp_rebuild(conn, *, pack, built_at):
    """BL-107: simulate a successful full ovp-knowledge-index
    truth-projection rebuild for ``pack`` at ``built_at``."""
    conn.execute(
        "INSERT INTO truth_projections (pack, owner_pack, "
        "builder_name, built_at) VALUES (?, ?, ?, ?)",
        (pack, pack, "test", built_at),
    )


def _vault(tmp_path: Path) -> Path:
    db = tmp_path / "60-Logs" / "knowledge.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return tmp_path


def _emit(conn, et, *, slug="", ts=None, payload=None):
    if ts is None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO audit_events VALUES (?,?,?,?,?,?)",
        ("pipeline.jsonl", et, slug, "s", ts, json.dumps(payload or {})),
    )


# ── pure helpers ───────────────────────────────────────────────────


def test_canonical_evidence_detects_promote_in_window(tmp_path):
    v = _vault(tmp_path)
    conn = sqlite3.connect(v / "60-Logs" / "knowledge.db")
    _emit(conn, "evergreen_auto_promoted", slug="obj-x")
    conn.commit()
    found = refresh_ops._canonical_evidence_since(conn, 180, PACK)
    assert found.get("evergreen_auto_promoted") == 1


def test_canonical_evidence_ignores_candidates_only(tmp_path):
    v = _vault(tmp_path)
    conn = sqlite3.connect(v / "60-Logs" / "knowledge.db")
    _emit(conn, "candidates_upserted", slug="src-a")
    _emit(conn, "absorb_pending_upsert", slug="src-a")
    _emit(conn, "absorb_route_decision", slug="src-a")
    conn.commit()
    assert refresh_ops._canonical_evidence_since(conn, 180, PACK) == {}


def test_canonical_evidence_respects_window(tmp_path):
    v = _vault(tmp_path)
    conn = sqlite3.connect(v / "60-Logs" / "knowledge.db")
    old = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    _emit(conn, "promote_concept", slug="obj-y", ts=old)
    conn.commit()
    # 180-minute window excludes a 5-day-old promote.
    assert refresh_ops._canonical_evidence_since(conn, 180, PACK) == {}


def test_canonical_evidence_parses_iso_t_timestamps(tmp_path):
    """ISO-8601 ``T``-separated + ``Z`` timestamps (event_emitter.emit
    format) must be parsed, not lexicographically compared.  A naive
    string compare against ``datetime('now')`` (space-separated)
    misclassifies these rows."""
    v = _vault(tmp_path)
    conn = sqlite3.connect(v / "60-Logs" / "knowledge.db")
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_iso = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _emit(conn, "evergreen_auto_promoted", slug="obj-now", ts=now_iso)
    _emit(conn, "promote_concept", slug="obj-old", ts=old_iso)
    conn.commit()
    found = refresh_ops._canonical_evidence_since(conn, 180, PACK)
    assert found.get("evergreen_auto_promoted") == 1
    # 5-day-old ISO promote is outside the 180m window.
    assert "promote_concept" not in found


def test_canonical_evidence_naive_local_recent_detected(tmp_path):
    """PipelineLogger writes ``datetime.now().isoformat()`` — a NAIVE
    *local* timestamp.  A just-emitted promote must be detected
    regardless of the machine's tz offset; labelling naive-local as
    UTC would shift it hours into the past and wrongly skip it."""
    v = _vault(tmp_path)
    conn = sqlite3.connect(v / "60-Logs" / "knowledge.db")
    # naive local, no tz suffix — exactly what PipelineLogger emits
    now_local = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    old_local = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S")
    _emit(conn, "evergreen_auto_promoted", slug="obj-now", ts=now_local)
    _emit(conn, "promote_concept", slug="obj-old", ts=old_local)
    conn.commit()
    found = refresh_ops._canonical_evidence_since(conn, 180, PACK)
    assert found.get("evergreen_auto_promoted") == 1
    assert "promote_concept" not in found


def test_canonical_evidence_scoped_to_pack(tmp_path):
    """A recent promote for a DIFFERENT pack must not be counted as
    evidence for the requested pack; same-pack and pack-less legacy
    rows are counted."""
    v = _vault(tmp_path)
    conn = sqlite3.connect(v / "60-Logs" / "knowledge.db")
    _emit(
        conn,
        "promote_concept",
        slug="obj-other",
        payload={"pack": "some-other-pack"},
    )
    _emit(
        conn,
        "evergreen_auto_promoted",
        slug="obj-mine",
        payload={"pack": PACK},
    )
    # legacy row, no pack recorded → kept (conservative)
    _emit(conn, "evergreen_created", slug="obj-legacy")
    conn.commit()
    found = refresh_ops._canonical_evidence_since(conn, 180, PACK)
    assert found.get("evergreen_auto_promoted") == 1
    assert found.get("evergreen_created") == 1
    assert "promote_concept" not in found


def test_parse_audit_ts_handles_mixed_formats():
    p = refresh_ops._parse_audit_ts
    assert p("2026-05-14T12:30:00Z") is not None
    assert p("2026-05-14 12:30:00") is not None
    assert p("2026-05-14T12:30:00+00:00") is not None
    assert p("2026-05-14T12:30:00.123456Z") is not None
    assert p("2026-05-14") is not None
    assert p("") is None
    assert p("not-a-timestamp") is None
    # explicit-Z → UTC; naive → local. A naive value and the same
    # value with Z differ by the machine's utc offset (0 only if the
    # box itself is UTC).
    naive = p("2026-05-14 12:30:00")
    aware = p("2026-05-14 12:30:00Z")
    assert naive is not None and aware is not None
    assert naive.utcoffset() == datetime.now().astimezone().utcoffset()
    assert aware.utcoffset() == timedelta(0)


def test_state_counts_all_keys_present(tmp_path):
    v = _vault(tmp_path)
    conn = sqlite3.connect(v / "60-Logs" / "knowledge.db")
    conn.executescript(
        "CREATE TABLE ops_state (pack TEXT, item_kind TEXT, "
        "item_id TEXT, state TEXT, sub_state TEXT, "
        "last_evidence_at TEXT, evidence_event_types_json TEXT, "
        "needs_action_reason TEXT, refreshed_at TEXT, "
        "PRIMARY KEY (pack,item_kind,item_id))"
    )
    conn.execute(
        "INSERT INTO ops_state VALUES " "(?,?,?,?,?,?,?,?,?)",
        (PACK, "source", "s1", "Received", None, "", "[]", None, ""),
    )
    conn.commit()
    counts = refresh_ops._state_counts(conn, PACK)
    assert set(counts) == set(ALL_STATES)
    assert counts["Received"] == 1
    assert counts["Accepted"] == 0


# ── main() decision rule ───────────────────────────────────────────


def test_main_missing_db_exits_1(tmp_path):
    rc = refresh_ops.main(["--vault-dir", str(tmp_path)])
    assert rc == 1


def test_main_aborts_when_sync_not_synced(tmp_path, capsys):
    """If audit sync is skipped/failed, refuse to decide on a stale
    audit table — distinct exit 3, no rebuild, no verdict."""
    v = _vault(tmp_path)
    with patch.object(
        refresh_ops,
        "sync_audit_events_from_jsonl",
        return_value={
            "status": "skipped",
            "reason": "knowledge.db incompatible",
        },
    ):
        rc = refresh_ops.main(["--vault-dir", str(v), "--pack", PACK])
    err = capsys.readouterr().err
    assert rc == 3
    assert "did not complete" in err
    assert "knowledge.db incompatible" in err


def test_main_candidates_only_exits_0_and_says_not_needed(tmp_path, capsys):
    v = _vault(tmp_path)
    conn = sqlite3.connect(v / "60-Logs" / "knowledge.db")
    # A source with candidate/source evidence only.
    _emit(conn, "article_intake_only", slug="src-a")
    _emit(conn, "candidates_upserted", slug="src-a")
    conn.commit()
    conn.close()

    with patch.object(
        refresh_ops,
        "sync_audit_events_from_jsonl",
        return_value={"status": "synced"},
    ):
        rc = refresh_ops.main(["--vault-dir", str(v), "--pack", PACK])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Full `ovp-knowledge-index` rebuild NOT needed" in out
    assert "audit sync: synced" in out


def test_main_canonical_evidence_exits_2_and_warns(tmp_path, capsys):
    v = _vault(tmp_path)
    conn = sqlite3.connect(v / "60-Logs" / "knowledge.db")
    _emit(conn, "article_intake_only", slug="src-b")
    _emit(conn, "candidates_upserted", slug="src-b")
    # A real promote → canonical object changed.
    _emit(
        conn,
        "evergreen_auto_promoted",
        slug="src-b",
        payload={"object_id": "obj-b", "concept": "obj-b"},
    )
    conn.execute(
        "INSERT INTO objects VALUES (?,?,?,?,?,?,?)",
        (PACK, "obj-b", "evergreen", "B", "10-Knowledge/Evergreen/B.md", "src-b", ""),
    )
    conn.commit()
    conn.close()

    with patch.object(
        refresh_ops,
        "sync_audit_events_from_jsonl",
        return_value={"status": "synced"},
    ):
        rc = refresh_ops.main(["--vault-dir", str(v), "--pack", PACK])
    out = capsys.readouterr().out
    assert rc == 2
    assert "Canonical-object evidence detected" in out
    assert "evergreen_auto_promoted" in out


def test_main_json_mode_shape(tmp_path, capsys):
    v = _vault(tmp_path)
    conn = sqlite3.connect(v / "60-Logs" / "knowledge.db")
    _emit(conn, "candidates_upserted", slug="src-c")
    conn.commit()
    conn.close()

    with patch.object(
        refresh_ops,
        "sync_audit_events_from_jsonl",
        return_value={"status": "synced"},
    ):
        rc = refresh_ops.main(["--vault-dir", str(v), "--pack", PACK, "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["heavier_rebuild_needed"] is False
    assert set(payload["before"]) == set(ALL_STATES)
    assert set(payload["after"]) == set(ALL_STATES)
    assert payload["before_total"] == sum(payload["before"].values())
    assert "deltas" in payload


def test_main_total_conserved_when_only_state_moves(tmp_path, capsys):
    """The hallmark of a clean candidates-only refresh: items move
    Received→Extracted but the TOTAL is conserved (no phantom
    rows, no double-count) — the exact invariant the recent=3
    real-vault validation showed."""
    v = _vault(tmp_path)
    db = v / "60-Logs" / "knowledge.db"
    conn = sqlite3.connect(db)
    # Two sources: one stays Received, one has extraction evidence
    # → Extracted.  rebuild_ops_state derives both from audit.
    _emit(conn, "article_intake_only", slug="src-recv")
    _emit(conn, "article_intake_only", slug="src-ext")
    _emit(conn, "absorb_route_decision", slug="src-ext")
    _emit(conn, "evergreen_extraction_complete", slug="src-ext")
    _emit(conn, "candidates_upserted", slug="src-ext")
    conn.commit()
    conn.close()

    with patch.object(
        refresh_ops,
        "sync_audit_events_from_jsonl",
        return_value={"status": "synced"},
    ):
        rc = refresh_ops.main(["--vault-dir", str(v), "--pack", PACK, "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    # before=0 (no ops_state yet), after=2 — both sources counted
    # exactly once, total == sum, no double-count across kinds.
    assert payload["after_total"] == sum(payload["after"].values())
    assert payload["after"]["Received"] >= 1
    assert payload["after"]["Extracted"] >= 1


# ── BL-107 / issue #250: rebuild-watermark idempotency ─────────────


def test_watermark_idempotent_promote_before_rebuild_not_flagged(tmp_path):
    """The #250 fix: a promote that a later full rebuild already
    absorbed is OLDER than the watermark → NOT re-flagged, so a
    second candidates-only refresh stops nagging."""
    v = _vault(tmp_path)
    conn = sqlite3.connect(v / "60-Logs" / "knowledge.db")
    _emit(
        conn,
        "promote_concept",
        slug="obj-x",
        ts="2026-05-10T08:00:00+00:00",
        payload={"pack": PACK},
    )
    # operator ran the full rebuild AFTER the promote
    _stamp_rebuild(conn, pack=PACK, built_at="2026-05-10T09:00:00+00:00")
    conn.commit()
    assert refresh_ops._canonical_evidence_since(conn, 180, PACK) == {}


def test_watermark_promote_after_rebuild_is_flagged(tmp_path):
    """A promote NEWER than the last full rebuild is unhandled →
    flagged."""
    v = _vault(tmp_path)
    conn = sqlite3.connect(v / "60-Logs" / "knowledge.db")
    _stamp_rebuild(conn, pack=PACK, built_at="2026-05-10T08:00:00+00:00")
    _emit(
        conn,
        "promote_concept",
        slug="obj-y",
        ts="2026-05-10T09:00:00+00:00",
        payload={"pack": PACK},
    )
    conn.commit()
    found = refresh_ops._canonical_evidence_since(conn, 180, PACK)
    assert found.get("promote_concept") == 1


def test_watermark_beats_window_for_stale_unhandled_promote(tmp_path):
    """Strictly more correct than the old window: a 5-day-old
    promote never rebuilt is still newer than a 6-day-old watermark
    → flagged, where a 180m window would have missed it."""
    v = _vault(tmp_path)
    conn = sqlite3.connect(v / "60-Logs" / "knowledge.db")
    old_rebuild = (datetime.now(timezone.utc) - timedelta(days=6)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )
    stale_promote = (datetime.now(timezone.utc) - timedelta(days=5)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )
    _stamp_rebuild(conn, pack=PACK, built_at=old_rebuild)
    _emit(conn, "evergreen_auto_promoted", slug="obj-z", ts=stale_promote, payload={"pack": PACK})
    conn.commit()
    found = refresh_ops._canonical_evidence_since(conn, 180, PACK)
    assert found.get("evergreen_auto_promoted") == 1


def test_no_watermark_falls_back_to_window(tmp_path):
    """No truth_projections row (never rebuilt) → window heuristic,
    the pre-BL-107 safe default."""
    v = _vault(tmp_path)
    conn = sqlite3.connect(v / "60-Logs" / "knowledge.db")
    recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    _emit(conn, "promote_concept", slug="obj-r", ts=recent, payload={"pack": PACK})
    conn.commit()
    assert refresh_ops._last_rebuild_watermark(conn, PACK) is None
    assert refresh_ops._canonical_evidence_since(conn, 180, PACK).get("promote_concept") == 1


def test_watermark_is_pack_scoped(tmp_path):
    """A different pack's rebuild must NOT suppress this pack's
    unhandled promote."""
    v = _vault(tmp_path)
    conn = sqlite3.connect(v / "60-Logs" / "knowledge.db")
    recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    # other pack rebuilt just now …
    _stamp_rebuild(conn, pack="other-pack", built_at=recent)
    # … but THIS pack has a fresh unhandled promote and no rebuild
    _emit(conn, "promote_concept", slug="obj-p", ts=recent, payload={"pack": PACK})
    conn.commit()
    assert refresh_ops._last_rebuild_watermark(conn, PACK) is None
    assert refresh_ops._canonical_evidence_since(conn, 180, PACK).get("promote_concept") == 1
