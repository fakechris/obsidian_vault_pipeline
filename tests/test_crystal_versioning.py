"""Tests for BL-044 crystal append-only versioning.

The crystal tables already had ``synthesized_at`` in the PK so
re-runs always appended.  BL-044 adds the bookkeeping that turns
that append into a proper version chain:

* Prior current row's ``superseded_by_synthesized_at`` flips to
  the new row's timestamp.
* Prior live markdown moves to ``70-Archive/Crystals/<safe-id>/<ts>.md``.
* New live markdown lands at ``40-Resources/Crystals/<safe-id>.md``.

Failures in either step must not corrupt the chain — version
integrity matters more than file accounting.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from ovp_pipeline.synthesis._versioning import (
    ARCHIVE_DIR_REL,
    _safe_archive_filename,
    supersede_and_archive_previous,
)
from ovp_pipeline.synthesis.community_crystal import (
    CRYSTAL_DIR_REL,
    synthesize_community_crystals,
)
from ovp_pipeline.synthesis.contradiction_crystal import (
    synthesize_contradiction_crystals,
)


SCHEMA = """
CREATE TABLE objects (
  pack TEXT NOT NULL,
  object_id TEXT NOT NULL,
  object_kind TEXT NOT NULL,
  title TEXT NOT NULL,
  canonical_path TEXT NOT NULL,
  source_slug TEXT NOT NULL,
  PRIMARY KEY (pack, object_id)
);
CREATE TABLE claims (
  pack TEXT NOT NULL,
  claim_id TEXT NOT NULL,
  object_id TEXT NOT NULL,
  claim_kind TEXT NOT NULL,
  claim_text TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (pack, claim_id)
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
CREATE TABLE contradictions (
  pack TEXT NOT NULL,
  contradiction_id TEXT NOT NULL,
  subject_key TEXT NOT NULL,
  positive_claim_ids_json TEXT NOT NULL,
  negative_claim_ids_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  resolution_note TEXT NOT NULL DEFAULT '',
  resolved_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, contradiction_id)
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
CREATE TABLE contradiction_crystals (
  pack TEXT NOT NULL,
  contradiction_id TEXT NOT NULL,
  subject_key TEXT NOT NULL,
  body_md TEXT NOT NULL,
  positive_claim_ids_json TEXT NOT NULL,
  negative_claim_ids_json TEXT NOT NULL,
  source_object_ids_json TEXT NOT NULL,
  synthesized_at TEXT NOT NULL,
  llm_model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  superseded_by_synthesized_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, contradiction_id, synthesized_at)
);
"""


class _CountingLLM:
    """LLM stub that returns a different body on each call so tests
    can distinguish version 1 from version 2 by content."""

    def __init__(self):
        self.calls = 0

    def call(self, *_, **__) -> str:
        self.calls += 1
        return f"## 概念核心\n\nbody version {self.calls}"


# ---------------------------------------------------------------------------
# _safe_archive_filename — pure function smoke tests
# ---------------------------------------------------------------------------


class TestSafeArchiveFilename:
    def test_replaces_colons(self):
        # ISO timestamps contain `:` four times; archives need a
        # portable filename.  The canonical timestamp survives in
        # the file's frontmatter and the synthesized_at column.
        assert (
            _safe_archive_filename("2026-05-04T03:30:00+00:00")
            == "2026-05-04T03-30-00+00-00.md"
        )


# ---------------------------------------------------------------------------
# supersede_and_archive_previous — direct unit tests
# ---------------------------------------------------------------------------


class TestSupersedeHelper:
    def _setup_db(self, tmp_path):
        db = tmp_path / "knowledge.db"
        conn = sqlite3.connect(db)
        conn.executescript(SCHEMA)
        return conn, db

    def test_first_version_returns_none(self, tmp_path):
        conn, _ = self._setup_db(tmp_path)
        prior = supersede_and_archive_previous(
            conn,
            table="community_crystals",
            key_column="cluster_id",
            pack="t",
            key_value="cluster::aa",
            new_synthesized_at="2026-05-04T01:00:00+00:00",
            live_path=tmp_path / "missing.md",
            archive_subdir=tmp_path / "archive",
        )
        assert prior is None

    def test_flips_supersede_pointer_on_prior(self, tmp_path):
        conn, _ = self._setup_db(tmp_path)
        # Seed an existing row.
        conn.execute(
            "INSERT INTO community_crystals (pack, cluster_id, body_md, "
            "source_evergreen_slugs_json, synthesized_at, llm_model, "
            "prompt_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("t", "cluster::aa", "v1", "[]",
             "2026-05-04T01:00:00+00:00", "m", "v1"),
        )
        # Live file the helper will archive.
        live = tmp_path / "aa.md"
        live.write_text("# v1", encoding="utf-8")
        archive = tmp_path / "archive" / "aa"

        prior = supersede_and_archive_previous(
            conn,
            table="community_crystals",
            key_column="cluster_id",
            pack="t",
            key_value="cluster::aa",
            new_synthesized_at="2026-05-04T02:00:00+00:00",
            live_path=live,
            archive_subdir=archive,
        )
        assert prior == "2026-05-04T01:00:00+00:00"
        # Old row's supersede pointer is set.
        row = conn.execute(
            "SELECT superseded_by_synthesized_at FROM community_crystals "
            "WHERE cluster_id = 'cluster::aa'"
        ).fetchone()
        assert row[0] == "2026-05-04T02:00:00+00:00"
        # Live file moved into archive.
        assert not live.exists()
        moved = archive / "2026-05-04T01-00-00+00-00.md"
        assert moved.exists()
        assert moved.read_text(encoding="utf-8") == "# v1"

    def test_missing_live_file_still_updates_db(self, tmp_path):
        # If the live file was deleted out from under us (operator
        # cleanup, prior dry-run), the DB pointer must still flip
        # so the chain stays navigable.
        conn, _ = self._setup_db(tmp_path)
        conn.execute(
            "INSERT INTO community_crystals (pack, cluster_id, body_md, "
            "source_evergreen_slugs_json, synthesized_at, llm_model, "
            "prompt_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("t", "cluster::aa", "v1", "[]",
             "2026-05-04T01:00:00+00:00", "m", "v1"),
        )
        prior = supersede_and_archive_previous(
            conn,
            table="community_crystals",
            key_column="cluster_id",
            pack="t",
            key_value="cluster::aa",
            new_synthesized_at="2026-05-04T02:00:00+00:00",
            live_path=tmp_path / "ghost.md",  # never existed
            archive_subdir=tmp_path / "archive" / "aa",
        )
        assert prior == "2026-05-04T01:00:00+00:00"
        row = conn.execute(
            "SELECT superseded_by_synthesized_at FROM community_crystals "
            "WHERE cluster_id = 'cluster::aa'"
        ).fetchone()
        assert row[0] == "2026-05-04T02:00:00+00:00"

    def test_only_supersedes_unsuperseded_row(self, tmp_path):
        # If two rows exist (v1 already superseded by v2), and we
        # land v3, only v2's pointer flips — v1's already-set
        # pointer must NOT be touched (would corrupt the chain).
        conn, _ = self._setup_db(tmp_path)
        conn.execute(
            "INSERT INTO community_crystals (pack, cluster_id, body_md, "
            "source_evergreen_slugs_json, synthesized_at, llm_model, "
            "prompt_version, superseded_by_synthesized_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("t", "cluster::aa", "v1", "[]",
             "2026-05-04T01:00:00+00:00", "m", "v1",
             "2026-05-04T02:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO community_crystals (pack, cluster_id, body_md, "
            "source_evergreen_slugs_json, synthesized_at, llm_model, "
            "prompt_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("t", "cluster::aa", "v2", "[]",
             "2026-05-04T02:00:00+00:00", "m", "v1"),
        )
        prior = supersede_and_archive_previous(
            conn,
            table="community_crystals",
            key_column="cluster_id",
            pack="t",
            key_value="cluster::aa",
            new_synthesized_at="2026-05-04T03:00:00+00:00",
            live_path=tmp_path / "ghost.md",
            archive_subdir=tmp_path / "archive",
        )
        # v2 is the prior current — that's what got returned + flipped.
        assert prior == "2026-05-04T02:00:00+00:00"
        rows = conn.execute(
            "SELECT synthesized_at, superseded_by_synthesized_at "
            "FROM community_crystals "
            "WHERE cluster_id = 'cluster::aa' ORDER BY synthesized_at"
        ).fetchall()
        # v1 still points at v2; v2 now points at v3.
        assert rows[0] == ("2026-05-04T01:00:00+00:00",
                           "2026-05-04T02:00:00+00:00")
        assert rows[1] == ("2026-05-04T02:00:00+00:00",
                           "2026-05-04T03:00:00+00:00")


# ---------------------------------------------------------------------------
# End-to-end through synthesize_community_crystals
# ---------------------------------------------------------------------------


def _seed_community_vault(tmp_path: Path) -> tuple[Path, Path]:
    vault = tmp_path / "vault"
    vault.mkdir()
    db = vault / "60-Logs" / "knowledge.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    canonical = "10-Knowledge/Evergreen/a.md"
    (vault / canonical).parent.mkdir(parents=True, exist_ok=True)
    (vault / canonical).write_text("evergreen body", encoding="utf-8")
    conn.execute(
        "INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?)",
        ("research-tech", "a", "evergreen", "A", canonical, ""),
    )
    conn.execute(
        "INSERT INTO graph_clusters VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("research-tech", "cluster::ver01", "louvain_community",
         "C", "a", json.dumps(["a"]), 1.0),
    )
    conn.commit()
    conn.close()
    return vault, db


class TestCommunityCrystalVersioning:
    def test_v1_then_v2_flips_pointer_and_archives(self, tmp_path):
        vault, db = _seed_community_vault(tmp_path)
        llm = _CountingLLM()
        synthesize_community_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        time.sleep(1)  # next iso-second
        synthesize_community_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )

        # DB has two rows; v1 superseded by v2; v2 current.
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT body_md, synthesized_at, superseded_by_synthesized_at "
            "FROM community_crystals WHERE cluster_id = 'cluster::ver01' "
            "ORDER BY synthesized_at"
        ).fetchall()
        conn.close()
        assert len(rows) == 2
        v1_body, v1_at, v1_super = rows[0]
        v2_body, v2_at, v2_super = rows[1]
        assert "version 1" in v1_body
        assert "version 2" in v2_body
        assert v1_super == v2_at  # v1 points at v2
        assert v2_super == ""     # v2 is current

        # Live markdown holds v2; v1 lives in archive.
        live = vault / CRYSTAL_DIR_REL / "ver01.md"
        assert live.exists()
        assert "version 2" in live.read_text(encoding="utf-8")
        archive_dir = vault / ARCHIVE_DIR_REL / "ver01"
        archived_files = sorted(archive_dir.iterdir())
        assert len(archived_files) == 1
        archived = archived_files[0]
        # Archive filename uses sanitized timestamp (no `:`).
        assert ":" not in archived.name
        assert "version 1" in archived.read_text(encoding="utf-8")

    def test_three_versions_chain_correctly(self, tmp_path):
        # v1 → v2 → v3.  v1.super = v2.synth, v2.super = v3.synth,
        # v3.super = ''.  Two files in archive, one live.
        vault, db = _seed_community_vault(tmp_path)
        llm = _CountingLLM()
        for _ in range(3):
            synthesize_community_crystals(
                vault_dir=vault, llm_client=llm, db_path=db,
            )
            time.sleep(1)

        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT synthesized_at, superseded_by_synthesized_at "
            "FROM community_crystals "
            "WHERE cluster_id = 'cluster::ver01' "
            "ORDER BY synthesized_at"
        ).fetchall()
        conn.close()
        assert len(rows) == 3
        # First two have non-empty supersede; third is current.
        assert rows[0][1] == rows[1][0]   # v1 → v2
        assert rows[1][1] == rows[2][0]   # v2 → v3
        assert rows[2][1] == ""           # v3 current

        # Two archived files, one live file.
        archive_dir = vault / ARCHIVE_DIR_REL / "ver01"
        assert len(list(archive_dir.iterdir())) == 2
        assert (vault / CRYSTAL_DIR_REL / "ver01.md").exists()


# ---------------------------------------------------------------------------
# End-to-end through synthesize_contradiction_crystals
# ---------------------------------------------------------------------------


def _seed_contradiction_vault(tmp_path: Path) -> tuple[Path, Path]:
    vault = tmp_path / "vault"
    vault.mkdir()
    db = vault / "60-Logs" / "knowledge.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    for object_id in ("a", "b"):
        canonical = f"10-Knowledge/Evergreen/{object_id}.md"
        (vault / canonical).parent.mkdir(parents=True, exist_ok=True)
        (vault / canonical).write_text(
            f"body of {object_id}", encoding="utf-8",
        )
        conn.execute(
            "INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?)",
            ("research-tech", object_id, "evergreen",
             object_id.upper(), canonical, ""),
        )
        conn.execute(
            "INSERT INTO claims VALUES (?, ?, ?, ?, ?, 1.0)",
            ("research-tech", f"{object_id}::cc", object_id,
             "page_summary", f"X is {'true' if object_id == 'a' else 'not true'}"),
        )
    conn.execute(
        "INSERT INTO contradictions VALUES (?, ?, ?, ?, ?, ?, '', '')",
        ("research-tech", "contradiction::ver01", "x",
         json.dumps(["a::cc"]), json.dumps(["b::cc"]), "open"),
    )
    conn.commit()
    conn.close()
    return vault, db


class TestListCrystalsCli:
    """Basic smoke for ``ovp-list-crystals`` — output format details
    can drift without breaking callers, but the CLI must at least
    report version counts + ID + (super-pointer when --show-chain)
    for both crystal kinds."""

    def test_lists_chains_with_version_counts(self, tmp_path, capsys):
        from ovp_pipeline.commands.list_crystals import main

        vault, db = _seed_community_vault(tmp_path)
        # Patch VaultLayout.knowledge_db to point at our DB.
        # Simpler: just put the DB at the conventional path.
        # _seed_community_vault already does that.
        llm = _CountingLLM()
        synthesize_community_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        time.sleep(1)
        synthesize_community_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )

        rc = main(["--vault-dir", str(vault), "--kind", "community"])
        assert rc == 0
        out = capsys.readouterr().out
        # Two versions of the one chain.
        assert "1 chain, 2 total versions" in out
        # The cluster_id appears.
        assert "cluster::ver01" in out

    def test_show_chain_prints_supersede_pointers(self, tmp_path, capsys):
        from ovp_pipeline.commands.list_crystals import main

        vault, db = _seed_community_vault(tmp_path)
        llm = _CountingLLM()
        synthesize_community_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        time.sleep(1)
        synthesize_community_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )

        rc = main([
            "--vault-dir", str(vault), "--kind", "community",
            "--show-chain",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        # First version is superseded; second is current.
        # Format: "  cluster::ver01" then version lines below.
        assert "current" in out
        # The arrow form `→ <iso-timestamp>` appears for the older
        # row's supersede pointer.
        assert "→ " in out

    def test_bulk_versions_groups_by_chain_id(self, tmp_path):
        # Pin the bulk-fetch invariant directly: one SELECT returns
        # all chains' versions, grouped in Python.  Pre-fix the CLI
        # fired one query per chain (N+1).
        from ovp_pipeline.commands.list_crystals import _bulk_versions

        vault, db = _seed_community_vault(tmp_path)
        # Add a second cluster with one version, plus a 2-version
        # chain on the seeded cluster.
        llm = _CountingLLM()
        synthesize_community_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        time.sleep(1)
        synthesize_community_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        conn = sqlite3.connect(db)
        # Seed a singleton chain on a different cluster_id.
        conn.execute(
            "INSERT INTO community_crystals (pack, cluster_id, body_md, "
            "source_evergreen_slugs_json, synthesized_at, llm_model, "
            "prompt_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("research-tech", "cluster::single", "v1", "[]",
             "2026-05-04T10:00:00+00:00", "m", "v1"),
        )
        conn.commit()

        out = _bulk_versions(
            conn, table="community_crystals",
            key_column="cluster_id", pack="research-tech",
        )
        conn.close()
        # Two chains, one with 2 versions, one with 1.
        assert set(out.keys()) == {"cluster::ver01", "cluster::single"}
        assert len(out["cluster::ver01"]) == 2
        assert len(out["cluster::single"]) == 1
        # Within a chain, versions ordered chronologically.
        ver01_synth_ats = [s for s, _ in out["cluster::ver01"]]
        assert ver01_synth_ats == sorted(ver01_synth_ats)


class TestContradictionCrystalVersioning:
    def test_v1_then_v2_flips_pointer_and_archives(self, tmp_path):
        vault, db = _seed_contradiction_vault(tmp_path)
        llm = _CountingLLM()
        synthesize_contradiction_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        time.sleep(1)
        synthesize_contradiction_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )

        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT body_md, synthesized_at, superseded_by_synthesized_at "
            "FROM contradiction_crystals "
            "WHERE contradiction_id = 'contradiction::ver01' "
            "ORDER BY synthesized_at"
        ).fetchall()
        conn.close()
        assert len(rows) == 2
        v1_body, v1_at, v1_super = rows[0]
        _v2_body, v2_at, v2_super = rows[1]
        assert v1_super == v2_at
        assert v2_super == ""

        # Live + archive paths use the `contradiction-` prefix
        # (matching the existing filename convention).
        live = vault / CRYSTAL_DIR_REL / "contradiction-ver01.md"
        assert live.exists()
        archive_dir = vault / ARCHIVE_DIR_REL / "contradiction-ver01"
        assert archive_dir.exists()
        assert len(list(archive_dir.iterdir())) == 1
