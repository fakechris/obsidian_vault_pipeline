"""Tests for ovp_pipeline.evidence_replay — event collapse and SQL replay."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ovp_pipeline.evidence_replay import (
    _latest_per_key,
    emit_evidence_verified,
    replay_evidence_verifications,
)
from ovp_pipeline.runtime import VaultLayout


@pytest.fixture()
def replay_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "60-Logs").mkdir(parents=True)
    return vault


@pytest.fixture()
def replay_db(replay_vault: Path) -> sqlite3.Connection:
    db_path = replay_vault / "knowledge.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE claim_evidence (
            pack TEXT,
            claim_id TEXT,
            source_slug TEXT,
            evidence_kind TEXT,
            locator TEXT DEFAULT '',
            content_hash TEXT DEFAULT '',
            retrieval_context TEXT DEFAULT '',
            quote_start_line INTEGER DEFAULT 0,
            quote_end_line INTEGER DEFAULT 0,
            quote_start_char INTEGER DEFAULT 0,
            quote_end_char INTEGER DEFAULT 0,
            status TEXT DEFAULT 'unverified',
            verified_at TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE relations (
            pack TEXT,
            source_object_id TEXT,
            target_object_id TEXT,
            relation_type TEXT,
            evidence_source_slug TEXT,
            locator TEXT DEFAULT '',
            content_hash TEXT DEFAULT '',
            retrieval_context TEXT DEFAULT '',
            quote_start_line INTEGER DEFAULT 0,
            quote_end_line INTEGER DEFAULT 0,
            quote_start_char INTEGER DEFAULT 0,
            quote_end_char INTEGER DEFAULT 0,
            status TEXT DEFAULT 'unverified',
            verified_at TEXT DEFAULT ''
        )
    """)
    conn.commit()
    return conn


class TestLatestPerKey:
    def test_empty_input(self):
        assert _latest_per_key([]) == {}

    def test_single_event(self):
        event = {
            "event_type": "evidence_verified",
            "table": "claim_evidence",
            "key": {
                "pack": "default",
                "claim_id": "c1",
                "source_slug": "s1",
                "evidence_kind": "ek1",
            },
            "locator": "loc1",
        }
        result = _latest_per_key([event])
        assert len(result) == 1

    def test_last_event_wins(self):
        base_key = {
            "pack": "default",
            "claim_id": "c1",
            "source_slug": "s1",
            "evidence_kind": "ek1",
        }
        first = {
            "event_type": "evidence_verified",
            "table": "claim_evidence",
            "key": base_key,
            "locator": "old-locator",
        }
        second = {
            "event_type": "evidence_verified",
            "table": "claim_evidence",
            "key": base_key,
            "locator": "new-locator",
        }
        result = _latest_per_key([first, second])
        assert len(result) == 1
        stored = list(result.values())[0]
        assert stored["locator"] == "new-locator"

    def test_skips_non_verified_events(self):
        event = {
            "event_type": "something_else",
            "table": "claim_evidence",
            "key": {"pack": "a", "claim_id": "b", "source_slug": "c", "evidence_kind": "d"},
        }
        assert _latest_per_key([event]) == {}

    def test_skips_unknown_table(self):
        event = {
            "event_type": "evidence_verified",
            "table": "nonexistent",
            "key": {"pack": "a"},
        }
        assert _latest_per_key([event]) == {}

    def test_skips_missing_key_column(self):
        event = {
            "event_type": "evidence_verified",
            "table": "claim_evidence",
            "key": {"pack": "a", "claim_id": "b"},
        }
        assert _latest_per_key([event]) == {}


class TestEmitEvidenceVerified:
    def test_emits_valid_event(self, replay_vault: Path):
        emit_evidence_verified(
            replay_vault,
            table="claim_evidence",
            key={"pack": "default", "claim_id": "c1", "source_slug": "s1", "evidence_kind": "ek"},
            locator="loc",
            content_hash="abc",
            retrieval_context="ctx",
            status="verified",
            verified_at="2026-04-30T00:00:00Z",
            pack="default",
        )
        log_path = replay_vault / "60-Logs" / "evidence-verifications.jsonl"
        assert log_path.exists()
        lines = [line for line in log_path.read_text().splitlines() if line.strip()]
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event_type"] == "evidence_verified"
        assert event["table"] == "claim_evidence"
        assert event["locator"] == "loc"
        assert event["pack"] == "default"
        assert event["key"] == {
            "pack": "default",
            "claim_id": "c1",
            "source_slug": "s1",
            "evidence_kind": "ek",
        }
        assert event["content_hash"] == "abc"
        assert event["retrieval_context"] == "ctx"
        assert event["status"] == "verified"
        assert event["verified_at"] == "2026-04-30T00:00:00Z"
        assert event.get("quote_start_line", 0) == 0
        assert event.get("quote_end_line", 0) == 0

    def test_rejects_unknown_table(self, replay_vault: Path):
        with pytest.raises(ValueError, match="Unsupported evidence table"):
            emit_evidence_verified(
                replay_vault,
                table="bad_table",
                key={},
                locator="",
                content_hash="",
                retrieval_context="",
                status="",
                verified_at="",
                pack="default",
            )


class TestReplayEvidenceVerifications:
    def test_replay_updates_matching_row(self, replay_vault: Path, replay_db: sqlite3.Connection):
        replay_db.execute(
            "INSERT INTO claim_evidence (pack, claim_id, source_slug, evidence_kind)"
            " VALUES (?, ?, ?, ?)",
            ("default", "c1", "s1", "ek1"),
        )
        replay_db.commit()

        emit_evidence_verified(
            replay_vault,
            table="claim_evidence",
            key={"pack": "default", "claim_id": "c1", "source_slug": "s1", "evidence_kind": "ek1"},
            locator="file.md:10",
            content_hash="hash123",
            retrieval_context="context-text",
            status="verified",
            verified_at="2026-04-30T12:00:00Z",
            pack="default",
        )

        layout = VaultLayout.from_vault(replay_vault)
        applied = replay_evidence_verifications(replay_db, layout, pack_name="default")
        assert applied == 1

        row = replay_db.execute(
            """
            SELECT locator, content_hash, retrieval_context, status, verified_at
              FROM claim_evidence
             WHERE claim_id = 'c1'
            """
        ).fetchone()
        assert row == ("file.md:10", "hash123", "context-text", "verified", "2026-04-30T12:00:00Z")

    def test_replay_returns_zero_for_empty_log(
        self, replay_vault: Path, replay_db: sqlite3.Connection
    ):
        layout = VaultLayout.from_vault(replay_vault)
        applied = replay_evidence_verifications(replay_db, layout, pack_name="default")
        assert applied == 0

    def test_replay_ignores_missing_row(self, replay_vault: Path, replay_db: sqlite3.Connection):
        emit_evidence_verified(
            replay_vault,
            table="claim_evidence",
            key={"pack": "default", "claim_id": "gone", "source_slug": "s", "evidence_kind": "e"},
            locator="loc",
            content_hash="h",
            retrieval_context="c",
            status="verified",
            verified_at="2026-04-30T00:00:00Z",
            pack="default",
        )
        layout = VaultLayout.from_vault(replay_vault)
        applied = replay_evidence_verifications(replay_db, layout, pack_name="default")
        assert applied == 0
