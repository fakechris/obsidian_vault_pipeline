"""Tests for ``ovp-rerender-crystals`` — the no-LLM CLI that
regenerates on-disk crystal markdowns from DB rows.  Used when the
renderer changes (new frontmatter fields, new ## 相关笔记 section)
and the existing crystal files need to pick up the new format
without paying LLM cost again.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ovp_pipeline.synthesis._shared import CRYSTAL_DIR_REL


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
  concept_id TEXT NOT NULL DEFAULT '',
  supersede_reason TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, cluster_id, synthesized_at)
);

CREATE TABLE concept_identity_ledger (
  pack TEXT NOT NULL,
  concept_id TEXT NOT NULL,
  current_cluster_id TEXT NOT NULL DEFAULT '',
  last_matched_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT '',
  lineage_json TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (pack, concept_id)
);
CREATE TRIGGER IF NOT EXISTS trg_community_crystal_seed_ledger
AFTER INSERT ON community_crystals
WHEN NEW.concept_id = ''
BEGIN
  UPDATE community_crystals
     SET concept_id = NEW.cluster_id
   WHERE pack = NEW.pack
     AND cluster_id = NEW.cluster_id
     AND synthesized_at = NEW.synthesized_at;
  INSERT OR IGNORE INTO concept_identity_ledger
      (pack, concept_id, current_cluster_id,
       last_matched_at, created_at, lineage_json)
  VALUES (NEW.pack, NEW.cluster_id, NEW.cluster_id,
          NEW.synthesized_at, NEW.synthesized_at, '[]');
END;
CREATE TRIGGER IF NOT EXISTS trg_community_crystal_seed_ledger_explicit
AFTER INSERT ON community_crystals
WHEN NEW.concept_id <> ''
BEGIN
  INSERT OR IGNORE INTO concept_identity_ledger
      (pack, concept_id, current_cluster_id,
       last_matched_at, created_at, lineage_json)
  VALUES (NEW.pack, NEW.concept_id, NEW.cluster_id,
          NEW.synthesized_at, NEW.synthesized_at, '[]');
END;
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


def _build_vault(tmp_path: Path) -> tuple[Path, Path]:
    """Seed a vault with one community + one contradiction, each
    with a stale on-disk markdown that doesn't include the new
    ``## 相关笔记`` section.  The rerender CLI should refresh both."""
    vault = tmp_path / "vault"
    vault.mkdir()
    db = vault / "60-Logs" / "knowledge.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)

    # Seed a Louvain community + its members.
    for oid, title, body in [
        ("a", "A", "body a"),
        ("b", "B", "body b"),
    ]:
        path = "10-Knowledge/Evergreen/" + oid + ".md"
        (vault / path).parent.mkdir(parents=True, exist_ok=True)
        (vault / path).write_text(body, encoding="utf-8")
        conn.execute(
            "INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?)",
            ("research-tech", oid, "evergreen", title, path, ""),
        )
    conn.execute(
        "INSERT INTO graph_clusters VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("research-tech", "cluster::ren01", "louvain_community",
         "Renderer test", "a", json.dumps(["a", "b"]), 2.0),
    )
    # Community crystal row — body_md is the LLM output WITHOUT
    # the new related-notes section.
    conn.execute(
        "INSERT INTO community_crystals (pack, cluster_id, body_md, "
        "source_evergreen_slugs_json, synthesized_at, llm_model, "
        "prompt_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("research-tech", "cluster::ren01",
         "## 概念核心\n\nfoo", json.dumps(["a", "b"]),
         "2026-05-04T01:00:00.000000+00:00", "m", "v1"),
    )
    # Contradiction crystal row.
    for cid, oid, kind, text in [
        ("a::cc", "a", "page_summary", "X is true"),
        ("b::cc", "b", "page_summary", "X is not true"),
    ]:
        conn.execute(
            "INSERT INTO claims VALUES (?, ?, ?, ?, ?, 1.0)",
            ("research-tech", cid, oid, kind, text),
        )
    conn.execute(
        "INSERT INTO contradictions VALUES "
        "(?, ?, ?, ?, ?, ?, '', '')",
        ("research-tech", "contradiction::ren01", "x",
         json.dumps(["a::cc"]), json.dumps(["b::cc"]), "open"),
    )
    conn.execute(
        "INSERT INTO contradiction_crystals (pack, contradiction_id, "
        "subject_key, body_md, positive_claim_ids_json, "
        "negative_claim_ids_json, source_object_ids_json, "
        "synthesized_at, llm_model, prompt_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("research-tech", "contradiction::ren01", "x",
         "## 争议核心\n\nopen question text",
         json.dumps(["a::cc"]), json.dumps(["b::cc"]),
         json.dumps(["a", "b"]),
         "2026-05-04T01:00:00.000000+00:00", "m", "v1"),
    )

    # Stale on-disk files (would have been written by a prior
    # renderer that didn't know about ## 相关笔记).
    crystal_dir = vault / CRYSTAL_DIR_REL
    crystal_dir.mkdir(parents=True, exist_ok=True)
    (crystal_dir / "ren01.md").write_text(
        "stale community markdown", encoding="utf-8",
    )
    (crystal_dir / "contradiction-ren01.md").write_text(
        "stale contradiction markdown", encoding="utf-8",
    )

    conn.commit()
    conn.close()
    return vault, db


class TestRerenderCli:
    def test_rewrites_both_kinds(self, tmp_path, capsys):
        from ovp_pipeline.commands.rerender_crystals import main

        vault, _ = _build_vault(tmp_path)
        rc = main(["--vault-dir", str(vault)])
        assert rc == 0
        # Live community crystal now has the new ## 相关笔记 section
        # AND the body_md from the DB row.
        comm = (vault / CRYSTAL_DIR_REL / "ren01.md").read_text(encoding="utf-8")
        assert "## 相关笔记" in comm
        assert "[[a]]" in comm and "[[b]]" in comm
        assert "## 概念核心" in comm  # body_md preserved
        # Sampling disclosure NOT present (sample == total == 2).
        assert "采样说明" not in comm

        # Live contradiction crystal also refreshed.
        contra = (vault / CRYSTAL_DIR_REL / "contradiction-ren01.md").read_text(
            encoding="utf-8",
        )
        assert "## 相关笔记" in contra
        assert "[[a]]" in contra and "[[b]]" in contra
        assert "## 争议核心" in contra

        out = capsys.readouterr().out
        assert "community crystals rewrote:" in out
        assert "contradiction crystals rewrote:" in out

    def test_dry_run_does_not_touch_files(self, tmp_path, capsys):
        from ovp_pipeline.commands.rerender_crystals import main

        vault, _ = _build_vault(tmp_path)
        comm_path = vault / CRYSTAL_DIR_REL / "ren01.md"
        original = comm_path.read_text(encoding="utf-8")

        rc = main(["--vault-dir", str(vault), "--dry-run"])
        assert rc == 0
        # Live file unchanged.
        assert comm_path.read_text(encoding="utf-8") == original

    def test_kind_filter(self, tmp_path):
        from ovp_pipeline.commands.rerender_crystals import main

        vault, _ = _build_vault(tmp_path)
        comm_path = vault / CRYSTAL_DIR_REL / "ren01.md"
        contra_path = vault / CRYSTAL_DIR_REL / "contradiction-ren01.md"

        # Only rerender contradictions; community file stays stale.
        rc = main([
            "--vault-dir", str(vault), "--kind", "contradiction",
        ])
        assert rc == 0
        assert comm_path.read_text(encoding="utf-8") == "stale community markdown"
        assert "## 相关笔记" in contra_path.read_text(encoding="utf-8")

    def test_archive_files_are_rewritten_for_superseded_rows(self, tmp_path):
        # When a crystal has a superseded predecessor, the rerender
        # CLI must also refresh the archive file (not just the live
        # one) — otherwise the two versions would carry inconsistent
        # frontmatter / sections after a renderer change.
        from ovp_pipeline.commands.rerender_crystals import main
        from ovp_pipeline.synthesis._versioning import ARCHIVE_DIR_REL

        vault, db = _build_vault(tmp_path)
        # Add a v1 (already superseded) and update the existing row
        # to be v2.  Use a sub-second microsecond difference so the
        # PK accepts both rows.
        conn = sqlite3.connect(db)
        conn.execute(
            "UPDATE community_crystals "
            "SET superseded_by_synthesized_at = '2026-05-04T01:00:01.000000+00:00' "
            "WHERE cluster_id = 'cluster::ren01'"
        )
        conn.execute(
            "INSERT INTO community_crystals (pack, cluster_id, body_md, "
            "source_evergreen_slugs_json, synthesized_at, llm_model, "
            "prompt_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("research-tech", "cluster::ren01",
             "## 概念核心\n\nv2 body", json.dumps(["a", "b"]),
             "2026-05-04T01:00:01.000000+00:00", "m", "v1"),
        )
        conn.commit()
        conn.close()

        rc = main(["--vault-dir", str(vault), "--kind", "community"])
        assert rc == 0
        # Live file now reflects v2 (newer body) with the new section.
        live = (vault / CRYSTAL_DIR_REL / "ren01.md").read_text(encoding="utf-8")
        assert "v2 body" in live
        assert "## 相关笔记" in live
        # Archive file for v1 should also exist and have the new section.
        archive_dir = vault / ARCHIVE_DIR_REL / "ren01"
        archive_files = list(archive_dir.iterdir())
        assert len(archive_files) == 1
        archived = archive_files[0].read_text(encoding="utf-8")
        assert "## 相关笔记" in archived
        # Archive holds v1's body, not v2's.
        assert "## 概念核心\n\nfoo" in archived
