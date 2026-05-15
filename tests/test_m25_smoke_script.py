"""Regression tests for the M25.6 smoke script.

Codex review on PR #241 caught three functional bugs in the
acceptance harness — the script itself could pass when the
contracts it's supposed to verify were broken.  These tests lock
the three guards so a future edit that re-introduces the
false-positive paths fails CI.

The script's subprocess paths (``ovp-producer-audit``,
``ovp-ops-state --rebuild``) are exercised by the realistic
fixture's own assertions; here we just verify the in-process
checker functions handle the edge cases correctly.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture
def smoke_module():
    """Import the script as a module so we can call its helpers
    directly.  ``importlib`` because the script lives in
    ``scripts/`` (not on sys.path).  The module must be registered
    in ``sys.modules`` BEFORE exec so ``@dataclass`` decorators
    inside the script can resolve their owning module via
    ``cls.__module__``.
    """
    root = Path(__file__).resolve().parent.parent
    script_path = root / "scripts" / "smoke_m25_control_plane.py"
    spec = importlib.util.spec_from_file_location(
        "smoke_m25_control_plane", script_path,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["smoke_m25_control_plane"] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop("smoke_m25_control_plane", None)


def _make_db(tmp_path: Path) -> Path:
    """Minimum schema the contract checker reads from."""
    db_path = tmp_path / "60-Logs" / "knowledge.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE audit_events (
            source_log TEXT NOT NULL,
            event_type TEXT NOT NULL,
            slug TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            timestamp TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL
        );
        CREATE TABLE objects (
            pack TEXT NOT NULL,
            object_id TEXT NOT NULL,
            object_kind TEXT NOT NULL,
            title TEXT NOT NULL,
            canonical_path TEXT NOT NULL,
            source_slug TEXT NOT NULL,
            source_url TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (pack, object_id)
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
            pack TEXT NOT NULL,
            cluster_id TEXT NOT NULL,
            body_md TEXT NOT NULL,
            source_evergreen_slugs_json TEXT NOT NULL,
            synthesized_at TEXT NOT NULL,
            llm_model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            superseded_by_synthesized_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (pack, cluster_id, synthesized_at)
        );
        CREATE TABLE evergreen_revisions (
            pack TEXT NOT NULL,
            object_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            content_md TEXT NOT NULL,
            change_type TEXT NOT NULL,
            changed_by TEXT NOT NULL DEFAULT '',
            derived_at TEXT NOT NULL,
            change_note TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (pack, object_id, version)
        );
        """
    )
    conn.commit()
    conn.close()
    return db_path


# ── Codex fix #3: PYTHONPATH propagation to subprocesses ──────────


def test_subprocess_env_propagates_src_path(smoke_module):
    """The src/ path must be in the PYTHONPATH of subprocesses so
    they can ``import ovp_pipeline`` without an editable install."""
    env = smoke_module._subprocess_env()
    assert "PYTHONPATH" in env
    src_path = str(
        Path(smoke_module.__file__).resolve().parent.parent / "src"
    )
    # The src path is FIRST in the colon-separated list so it
    # takes precedence.
    assert env["PYTHONPATH"].split(":")[0] == src_path


def test_subprocess_env_preserves_existing_pythonpath(smoke_module, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/existing/path")
    env = smoke_module._subprocess_env()
    parts = env["PYTHONPATH"].split(":")
    assert "/existing/path" in parts
    src_path = str(
        Path(smoke_module.__file__).resolve().parent.parent / "src"
    )
    # src/ stays in front so smoke imports the working tree.
    assert parts.index(src_path) < parts.index("/existing/path")


# ── Codex fix #1: missing lifecycle states fail loudly ───────────


def test_contract_check_fails_when_a_state_is_missing(smoke_module, monkeypatch, tmp_path):
    """If a regression drops a state from M25_LIFECYCLE_CARD_DEFS,
    the smoke must NOT pass.  Pre-codex-fix the loop only walked
    cards that were present, so a four-card payload looked clean."""
    _make_db(tmp_path)
    # Monkey-patch the payload builder to return only four cards.
    import ovp_pipeline.ui.view_models as vm
    original = vm.build_today_digest_payload

    def _build(*args, **kwargs):
        payload = original(*args, **kwargs)
        # Drop NeedsAction.
        payload["cards"] = [
            c for c in payload["cards"] if c["id"] != "NeedsAction"
        ]
        return payload

    monkeypatch.setattr(vm, "build_today_digest_payload", _build)
    # The smoke module imported the original symbol at module-load
    # time, so patch its local reference too.
    monkeypatch.setattr(smoke_module, "build_today_digest_payload", _build)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result, table = smoke_module._check_card_n_equals_drilldown_n(
        tmp_path, "research-tech", today,
    )
    assert result.ok is False
    assert "missing_states" in result.detail
    assert "NeedsAction" in result.detail


# ── Codex fix #2: unavailable items don't count as 0 == 0 ────────


def test_contract_check_fails_when_items_unavailable(smoke_module, tmp_path):
    """Run the smoke against a vault where ``ops_state`` table
    DOES NOT exist.  ``build_items_list_payload`` returns
    ``available=False`` + ``total=0``; the card payload also
    returns ``primary_count=0`` (because the kernel can't read).
    Pre-codex-fix the smoke compared 0 == 0 and passed; today
    it must surface the missing projection."""
    _make_db(tmp_path)  # audit_events etc. — but no ops_state
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result, table = smoke_module._check_card_n_equals_drilldown_n(
        tmp_path, "research-tech", today,
    )
    assert result.ok is False
    # Every row reports the items drilldown as unavailable.
    assert all(row["items_available"] is False for row in table)
    assert all(row["primary_match"] is False for row in table)


def test_contract_check_passes_when_data_is_consistent(smoke_module, tmp_path):
    """Positive control — when ops_state IS built and counts
    match, the contract check passes."""
    db_path = _make_db(tmp_path)
    # Seed one Received source so the projection has something
    # the cards' Received primary_count can match.
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
        ("pipeline.jsonl", "article_intake_only", "src-x",
         "fixture", today_iso, "{}"),
    )
    conn.commit()
    from ovp_pipeline.ops_state import rebuild
    rebuild(conn, pack="research-tech")
    conn.close()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result, table = smoke_module._check_card_n_equals_drilldown_n(
        tmp_path, "research-tech", today,
    )
    assert result.ok is True
    received_row = next(r for r in table if r["state"] == "Received")
    assert received_row["primary_match"] is True
    assert received_row["audit_match"] is True
