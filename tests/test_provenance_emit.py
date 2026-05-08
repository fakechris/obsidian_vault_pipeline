"""BL-056: stage emit hooks for the provenance spine.

Verifies that every Canonical-State-write moment emits provenance
rows beyond the rebuild's ``stage='ingest'`` baseline:

1. ``synthesize_community_crystal`` — every community crystal
   committed via ``commit_crystal_version`` writes a row.
2. ``synthesize_contradiction_crystal`` — same shape for the
   contradiction crystal path.
3. ``promote`` — ``review_candidate_concept`` writes a row for the
   target evergreen after the post-promote rebuild succeeds.
4. ``extract`` — same review path, but backdated to the candidate's
   ``absorbed_at`` so the chain timestamp reflects when
   ``auto_evergreen_extractor`` produced it, not when the human
   reviewed it.  Skipped when frontmatter doesn't carry
   ``absorbed_at`` (legacy / hand-edited objects) — emitting at
   ``now`` would lie about the chain.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from ovp_pipeline.provenance import upsert_provenance
from ovp_pipeline.synthesis._versioning import commit_crystal_version


# Minimal schema covering provenance + the two crystal tables the
# versioning helper writes through.
SCHEMA = """
CREATE TABLE provenance (
  pack TEXT NOT NULL,
  object_id TEXT NOT NULL,
  source_url TEXT NOT NULL DEFAULT '',
  source_fingerprint TEXT NOT NULL DEFAULT '',
  derived_via_stage TEXT NOT NULL,
  derived_at TEXT NOT NULL,
  parent_object_id TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (pack, object_id, derived_via_stage, derived_at)
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
CREATE TABLE objects (
  pack TEXT NOT NULL, object_id TEXT NOT NULL, object_kind TEXT NOT NULL,
  title TEXT NOT NULL, canonical_path TEXT NOT NULL,
  source_slug TEXT NOT NULL, source_url TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, object_id)
);
"""


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "knowledge.db"
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# upsert_provenance — the shared helper
# ---------------------------------------------------------------------------


class TestUpsertProvenance:
    def test_writes_a_row(self, conn):
        upsert_provenance(
            conn, pack="t", object_id="alpha",
            derived_via_stage="ingest",
            source_url="https://example.com/a",
            source_fingerprint="abc",
            derived_at="2026-05-04T12:00:00+00:00",
        )
        row = conn.execute(
            "SELECT pack, object_id, derived_via_stage, source_url FROM provenance"
        ).fetchone()
        assert row == ("t", "alpha", "ingest", "https://example.com/a")

    def test_idempotent_on_pk(self, conn):
        for _ in range(3):
            upsert_provenance(
                conn, pack="t", object_id="alpha",
                derived_via_stage="ingest",
                derived_at="2026-05-04T12:00:00+00:00",
            )
        n = conn.execute("SELECT COUNT(*) FROM provenance").fetchone()[0]
        assert n == 1

    def test_different_stage_creates_new_row(self, conn):
        upsert_provenance(
            conn, pack="t", object_id="alpha",
            derived_via_stage="ingest",
            derived_at="2026-05-04T12:00:00+00:00",
        )
        upsert_provenance(
            conn, pack="t", object_id="alpha",
            derived_via_stage="promote",
            derived_at="2026-05-04T12:00:00+00:00",
        )
        n = conn.execute("SELECT COUNT(*) FROM provenance").fetchone()[0]
        assert n == 2

    def test_metadata_json_serialised(self, conn):
        upsert_provenance(
            conn, pack="t", object_id="alpha",
            derived_via_stage="synthesize_community_crystal",
            metadata={"llm_model": "minimax", "sample_size": 8},
            derived_at="2026-05-04T12:00:00+00:00",
        )
        meta = conn.execute(
            "SELECT metadata_json FROM provenance"
        ).fetchone()[0]
        assert json.loads(meta) == {"llm_model": "minimax", "sample_size": 8}

    def test_missing_table_does_not_raise(self, tmp_path):
        # No SCHEMA executed — provenance table doesn't exist.
        c = sqlite3.connect(tmp_path / "empty.db")
        try:
            upsert_provenance(
                c, pack="t", object_id="alpha",
                derived_via_stage="ingest",
                derived_at="2026-05-04T12:00:00+00:00",
            )
            # If we got here without raising, the helper handled the
            # missing schema gracefully (best-effort contract).
        finally:
            c.close()

    def test_bad_metadata_does_not_raise(self, conn):
        """gemini PR #153 review: a non-JSON-serialisable metadata
        value (e.g. an open file handle, a custom object) used to
        raise TypeError outside the try block and abort the
        caller's transaction.  The helper now catches it."""
        class _NotSerialisable:
            pass

        upsert_provenance(
            conn, pack="t", object_id="alpha",
            derived_via_stage="ingest",
            derived_at="2026-05-04T12:00:00+00:00",
            metadata={"unserialisable": _NotSerialisable()},
        )
        # The bad call is silently dropped; no row written, no raise.
        n = conn.execute("SELECT COUNT(*) FROM provenance").fetchone()[0]
        assert n == 0


# ---------------------------------------------------------------------------
# commit_crystal_version emits stage row inside the same transaction
# ---------------------------------------------------------------------------


class TestCommitCrystalVersionEmit:
    def test_community_crystal_emits_stage_row(self, conn, tmp_path):
        live_path = tmp_path / "vault" / "40-Resources" / "Crystals" / "abc.md"
        archive = tmp_path / "vault" / "70-Archive" / "Crystals" / "abc"
        commit_crystal_version(
            conn,
            table="community_crystals",
            key_column="cluster_id",
            pack="research-tech",
            key_value="cluster::abc",
            new_synthesized_at="2026-05-05T10:00:00.000000+00:00",
            insert_sql=(
                "INSERT INTO community_crystals "
                "(pack, cluster_id, body_md, source_evergreen_slugs_json, "
                " synthesized_at, llm_model, prompt_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)"
            ),
            insert_params=(
                "research-tech", "cluster::abc", "## body",
                json.dumps(["ev-1", "ev-2"]),
                "2026-05-05T10:00:00.000000+00:00",
                "minimax-m2.7-highspeed", "v1",
            ),
            new_markdown="## body\n",
            live_path=live_path,
            archive_subdir=archive,
            provenance_stage="synthesize_community_crystal",
            provenance_metadata={"llm_model": "minimax-m2.7-highspeed", "sample_size": 2},
        )

        rows = conn.execute(
            "SELECT object_id, derived_via_stage, metadata_json FROM provenance"
        ).fetchall()
        assert rows == [
            (
                "cluster::abc",
                "synthesize_community_crystal",
                '{"llm_model": "minimax-m2.7-highspeed", "sample_size": 2}',
            ),
        ]

    def test_contradiction_crystal_emits_stage_row(self, conn, tmp_path):
        live_path = tmp_path / "vault" / "40-Resources" / "Crystals" / "contradiction-xyz.md"
        archive = tmp_path / "vault" / "70-Archive" / "Crystals" / "contradiction-xyz"
        commit_crystal_version(
            conn,
            table="contradiction_crystals",
            key_column="contradiction_id",
            pack="research-tech",
            key_value="contradiction::xyz",
            new_synthesized_at="2026-05-05T10:01:00.000000+00:00",
            insert_sql=(
                "INSERT INTO contradiction_crystals "
                "(pack, contradiction_id, subject_key, body_md, "
                " positive_claim_ids_json, negative_claim_ids_json, "
                " source_object_ids_json, synthesized_at, llm_model, "
                " prompt_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            insert_params=(
                "research-tech", "contradiction::xyz", "RAG vs context",
                "## body",
                json.dumps(["c-pos"]), json.dumps(["c-neg"]),
                json.dumps(["o1", "o2"]),
                "2026-05-05T10:01:00.000000+00:00",
                "minimax-m2.7-highspeed", "v1",
            ),
            new_markdown="## body\n",
            live_path=live_path,
            archive_subdir=archive,
            provenance_stage="synthesize_contradiction_crystal",
            provenance_metadata={"subject_key": "RAG vs context"},
        )

        row = conn.execute(
            "SELECT object_id, derived_via_stage FROM provenance"
        ).fetchone()
        assert row == (
            "contradiction::xyz", "synthesize_contradiction_crystal",
        )

# ---------------------------------------------------------------------------
# _emit_extract_provenance — backdated to the candidate's ``absorbed_at``
# ---------------------------------------------------------------------------


class TestEmitExtractProvenance:
    """BL-056 ``stage='extract'`` row is the post-rebuild backdate of
    when ``auto_evergreen_extractor`` produced the candidate that
    became this evergreen.  Reads ``absorbed_at`` +
    ``extraction_prompt_version`` from the promoted evergreen's
    frontmatter; skips emission entirely when ``absorbed_at`` is
    missing rather than fabricating a ``now`` timestamp that would
    break the chain audit.
    """

    def _seed_layout(self, tmp_path):
        """Build a minimal vault directory layout the helper expects."""
        vault = tmp_path / "vault"
        (vault / "60-Logs").mkdir(parents=True, exist_ok=True)
        (vault / "10-Knowledge" / "Evergreen").mkdir(parents=True, exist_ok=True)
        return vault

    def _seed_db(self, vault, *, object_id, canonical_path, source_url=""):
        """Seed enough of the truth-store schema for the helper to
        find the evergreen and emit a row.  Mirrors only what the
        helper actually reads — narrower than ``rebuild_knowledge_index``."""
        db_path = vault / "60-Logs" / "knowledge.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(SCHEMA)
        conn.execute(
            """
            INSERT INTO objects
              (pack, object_id, object_kind, title, canonical_path,
               source_slug, source_url)
            VALUES (?, ?, 'concept', ?, ?, ?, ?)
            """,
            (
                "research-tech", object_id, object_id.title(),
                str(canonical_path), object_id, source_url,
            ),
        )
        conn.commit()
        conn.close()
        return db_path

    def test_extract_row_backdated_to_absorbed_at(self, tmp_path):
        from ovp_pipeline.truth_api import _emit_extract_provenance

        vault = self._seed_layout(tmp_path)
        evergreen = vault / "10-Knowledge" / "Evergreen" / "alpha.md"
        evergreen.write_text(
            "---\n"
            "note_id: alpha\n"
            'title: "Alpha"\n'
            "type: evergreen\n"
            'absorbed_at: "2026-04-28T12:14:03Z"\n'
            "extraction_prompt_version: v2\n"
            'source_url: "https://example.com/alpha"\n'
            "---\n\n# Alpha\n",
            encoding="utf-8",
        )
        db_path = self._seed_db(
            vault, object_id="alpha",
            canonical_path=evergreen,
            source_url="https://example.com/alpha",
        )

        _emit_extract_provenance(
            vault, pack_name="research-tech", target_slug="alpha",
        )

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT derived_via_stage, derived_at, source_url, metadata_json "
                "FROM provenance WHERE object_id = 'alpha'"
            ).fetchone()
        assert row is not None
        assert row[0] == "extract"
        # Backdated to the candidate's ``absorbed_at``, not ``now``.
        assert row[1] == "2026-04-28T12:14:03Z"
        assert row[2] == "https://example.com/alpha"
        metadata = json.loads(row[3])
        assert metadata["via"] == "auto_evergreen_extractor"
        assert metadata["prompt_version"] == "v2"

    def test_skips_emit_when_absorbed_at_missing(self, tmp_path):
        """Legacy / hand-edited evergreens without ``absorbed_at``
        are intentionally skipped.  Emitting at ``now`` would lie
        about the chain timestamp."""
        from ovp_pipeline.truth_api import _emit_extract_provenance

        vault = self._seed_layout(tmp_path)
        evergreen = vault / "10-Knowledge" / "Evergreen" / "legacy.md"
        evergreen.write_text(
            "---\n"
            "note_id: legacy\n"
            'title: "Legacy"\n'
            "type: evergreen\n"
            "---\n\n# Legacy\n",
            encoding="utf-8",
        )
        db_path = self._seed_db(
            vault, object_id="legacy", canonical_path=evergreen,
        )

        _emit_extract_provenance(
            vault, pack_name="research-tech", target_slug="legacy",
        )

        with sqlite3.connect(db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM provenance WHERE object_id = 'legacy'"
            ).fetchone()[0]
        assert count == 0

    def test_idempotent_re_emit(self, tmp_path):
        """Re-emit at the same ``absorbed_at`` is a no-op via the
        provenance PK ``(pack, object_id, derived_via_stage,
        derived_at)`` — running the candidate review twice on the
        same evergreen leaves exactly one ``extract`` row."""
        from ovp_pipeline.truth_api import _emit_extract_provenance

        vault = self._seed_layout(tmp_path)
        evergreen = vault / "10-Knowledge" / "Evergreen" / "alpha.md"
        evergreen.write_text(
            "---\n"
            "note_id: alpha\n"
            'title: "Alpha"\n'
            "type: evergreen\n"
            'absorbed_at: "2026-04-28T12:14:03Z"\n'
            "extraction_prompt_version: v2\n"
            "---\n\n# Alpha\n",
            encoding="utf-8",
        )
        db_path = self._seed_db(
            vault, object_id="alpha", canonical_path=evergreen,
        )

        for _ in range(3):
            _emit_extract_provenance(
                vault, pack_name="research-tech", target_slug="alpha",
            )

        with sqlite3.connect(db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM provenance "
                "WHERE object_id = 'alpha' AND derived_via_stage = 'extract'"
            ).fetchone()[0]
        assert count == 1

    def test_no_stage_emit_when_not_requested(self, conn, tmp_path):
        # Backward compat: callers that haven't migrated can still
        # call the helper without provenance_stage and the audit
        # log just stays empty.
        live_path = tmp_path / "vault" / "40-Resources" / "Crystals" / "noemit.md"
        archive = tmp_path / "vault" / "70-Archive" / "Crystals" / "noemit"
        commit_crystal_version(
            conn,
            table="community_crystals",
            key_column="cluster_id",
            pack="research-tech",
            key_value="cluster::noemit",
            new_synthesized_at="2026-05-05T10:02:00.000000+00:00",
            insert_sql=(
                "INSERT INTO community_crystals "
                "(pack, cluster_id, body_md, source_evergreen_slugs_json, "
                " synthesized_at, llm_model, prompt_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)"
            ),
            insert_params=(
                "research-tech", "cluster::noemit", "## body",
                json.dumps([]),
                "2026-05-05T10:02:00.000000+00:00",
                "m", "v1",
            ),
            new_markdown="## body\n",
            live_path=live_path,
            archive_subdir=archive,
        )
        n = conn.execute("SELECT COUNT(*) FROM provenance").fetchone()[0]
        assert n == 0
