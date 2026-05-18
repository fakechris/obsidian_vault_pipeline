"""PR4 — pipeline / autopilot knowledge_index full-rebuild guard.

The default pipeline + autopilot no longer unconditionally run the
heavy ``rebuild_knowledge_index``.  ``decide_knowledge_refresh`` runs
the lightweight audit-sync + ops_state rebuild and only escalates to
a full rebuild on canonical-object evidence or an unknown /
untrustworthy state.  Conservative by design: *unknown ⇒ full*.

Locks the operator-defined rule (5 required cases):
1. no canonical evidence            → audit_sync_only, no full rebuild
2. promote/object/relation evidence → full_rebuild
3. audit sync error                 → full_rebuild (never silent skip)
4. db / metadata / schema / force   → full_rebuild
5. pipeline AND autopilot use the same decision
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ovp_pipeline.commands import refresh_ops
from ovp_pipeline.commands.refresh_ops import decide_knowledge_refresh
from ovp_pipeline.knowledge_index import (
    KNOWLEDGE_DB_PROJECTION_KIND,
    KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION,
)

PACK = "research-tech"

_SCHEMA = """
CREATE TABLE audit_events (
    source_log TEXT NOT NULL, event_type TEXT NOT NULL,
    slug TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL
);
CREATE TABLE truth_projections (
    pack TEXT NOT NULL, owner_pack TEXT NOT NULL DEFAULT '',
    builder_name TEXT NOT NULL DEFAULT '', built_at TEXT NOT NULL
);
CREATE TABLE projection_metadata (
    projection_kind TEXT PRIMARY KEY,
    authority_schema_version INTEGER NOT NULL,
    projection_schema_version INTEGER NOT NULL,
    built_at TEXT NOT NULL
);
CREATE TABLE ops_state (
    pack TEXT NOT NULL, item_kind TEXT NOT NULL, item_id TEXT NOT NULL,
    state TEXT NOT NULL, sub_state TEXT, last_evidence_at TEXT,
    evidence_event_types_json TEXT NOT NULL DEFAULT '[]',
    needs_action_reason TEXT, refreshed_at TEXT NOT NULL,
    PRIMARY KEY (pack, item_kind, item_id)
);
"""


def _vault(tmp_path: Path, *, healthy: bool = True) -> Path:
    db = tmp_path / "60-Logs" / "knowledge.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    if healthy:
        conn.execute(
            "INSERT INTO projection_metadata VALUES (?, 1, ?, ?)",
            (
                KNOWLEDGE_DB_PROJECTION_KIND,
                KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION,
                "2026-05-17T00:00:00Z",
            ),
        )
    conn.commit()
    conn.close()
    return tmp_path


def _emit(vault: Path, et: str, *, ts: str | None = None, payload=None) -> None:
    if ts is None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    db = vault / "60-Logs" / "knowledge.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO audit_events VALUES (?,?,?,?,?,?)",
            ("pipeline.jsonl", et, "", "s", ts, json.dumps(payload or {})),
        )
        conn.commit()


def _synced(_vault_dir):
    return {"status": "synced"}


# 1 ────────────────────────────────────────────────────────────────
def test_no_canonical_evidence_takes_audit_sync_only(tmp_path):
    v = _vault(tmp_path)
    _emit(v, "candidates_upserted")
    with patch.object(refresh_ops, "sync_audit_events_from_jsonl", _synced), patch.object(
        refresh_ops, "rebuild_ops_state", return_value={}
    ):
        d = decide_knowledge_refresh(v, PACK)
    assert d.refresh_mode == "audit_sync_only"
    assert d.is_full is False
    assert d.reason == "no_canonical_evidence"
    assert d.canonical_evidence_count == 0


# 2 ────────────────────────────────────────────────────────────────
def test_canonical_evidence_forces_full_rebuild(tmp_path):
    v = _vault(tmp_path)
    _emit(v, "evergreen_auto_promoted", payload={"pack": PACK})
    with patch.object(refresh_ops, "sync_audit_events_from_jsonl", _synced), patch.object(
        refresh_ops, "rebuild_ops_state", return_value={}
    ):
        d = decide_knowledge_refresh(v, PACK)
    assert d.refresh_mode == "full_rebuild"
    assert d.is_full is True
    assert d.reason == "canonical_object_evidence"
    assert d.canonical_evidence_count == 1


# 3 ────────────────────────────────────────────────────────────────
def test_audit_sync_failure_forces_full_rebuild(tmp_path):
    v = _vault(tmp_path)
    with patch.object(
        refresh_ops,
        "sync_audit_events_from_jsonl",
        lambda _v: {"status": "stale", "reason": "jsonl ahead"},
    ):
        d = decide_knowledge_refresh(v, PACK)
    assert d.refresh_mode == "full_rebuild"
    assert d.reason == "audit_sync_stale"
    assert d.audit_sync_status == "stale"


# 4 ────────────────────────────────────────────────────────────────
def test_missing_db_forces_full_rebuild(tmp_path):
    d = decide_knowledge_refresh(tmp_path, PACK)
    assert d.refresh_mode == "full_rebuild"
    assert d.reason == "knowledge_db_missing"


def test_missing_projection_metadata_forces_full_rebuild(tmp_path):
    v = _vault(tmp_path, healthy=False)  # no projection_metadata row
    d = decide_knowledge_refresh(v, PACK)
    assert d.refresh_mode == "full_rebuild"
    assert d.reason == "projection_metadata_missing"


def test_schema_mismatch_forces_full_rebuild(tmp_path):
    v = _vault(tmp_path, healthy=False)
    db = v / "60-Logs" / "knowledge.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO projection_metadata VALUES (?, 1, ?, ?)",
            (KNOWLEDGE_DB_PROJECTION_KIND, 1, "2026-05-17T00:00:00Z"),
        )
        conn.commit()
    d = decide_knowledge_refresh(v, PACK)
    assert d.refresh_mode == "full_rebuild"
    assert d.reason.startswith("projection_schema_mismatch")


def test_force_full_index_short_circuits(tmp_path):
    v = _vault(tmp_path)
    # force_full must NOT even run the lightweight work
    with patch.object(
        refresh_ops, "sync_audit_events_from_jsonl"
    ) as sync_mock:
        d = decide_knowledge_refresh(v, PACK, force_full=True)
    assert d.refresh_mode == "full_rebuild"
    assert d.reason == "force_full_index"
    sync_mock.assert_not_called()


# 5 ────────────────────────────────────────────────────────────────
def test_pipeline_and_autopilot_share_one_decision(tmp_path):
    """Both call sites import the SAME decide_knowledge_refresh —
    asserting identity prevents a future pipeline/autopilot fork."""
    from ovp_pipeline import unified_pipeline_enhanced as upe  # noqa: F401
    import ovp_pipeline.autopilot.daemon as daemon  # noqa: F401

    # Both modules resolve the helper from commands.refresh_ops.
    from ovp_pipeline.commands.refresh_ops import (
        decide_knowledge_refresh as canonical,
    )

    src_pipeline = Path(upe.__file__).read_text(encoding="utf-8")
    src_daemon = Path(daemon.__file__).read_text(encoding="utf-8")
    assert "from .commands.refresh_ops import decide_knowledge_refresh" in src_pipeline
    assert "from ..commands.refresh_ops import decide_knowledge_refresh" in src_daemon
    assert canonical is refresh_ops.decide_knowledge_refresh
