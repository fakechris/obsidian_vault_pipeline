"""Tests for ovp-backup-db.

Don't depend on the real knowledge.db; build a tiny SQLite fixture
in tmp_path and prove the round-trip.
"""

from __future__ import annotations

import sqlite3

import pytest

from ovp_pipeline.commands.backup_db import (
    _sha256_of,
    backup_one,
    main,
    prune,
)


def _make_fixture_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        for i in range(50):
            conn.execute("INSERT INTO t VALUES (?, ?)", (i, f"row-{i}"))
        conn.commit()
    finally:
        conn.close()


class TestBackupOne:
    def test_round_trip(self, tmp_path):
        src = tmp_path / "src.db"
        _make_fixture_db(src)
        dst = tmp_path / "out" / "snap.db"

        size = backup_one(src, dst)
        assert size > 0
        assert dst.exists()

        # Reading the snapshot must yield identical rows.
        conn = sqlite3.connect(dst)
        try:
            rows = conn.execute("SELECT id, v FROM t ORDER BY id").fetchall()
        finally:
            conn.close()
        assert len(rows) == 50
        assert rows[0] == (0, "row-0")
        assert rows[-1] == (49, "row-49")

    def test_atomic_no_tmp_left_behind(self, tmp_path):
        # Successful backup must clean up the .tmp staging file.
        src = tmp_path / "src.db"
        _make_fixture_db(src)
        dst = tmp_path / "snap.db"
        backup_one(src, dst)
        assert dst.exists()
        assert not dst.with_suffix(".db.tmp").exists()

    def test_overwrites_existing(self, tmp_path):
        # Re-running the same target path replaces, doesn't error.
        src = tmp_path / "src.db"
        _make_fixture_db(src)
        dst = tmp_path / "snap.db"
        backup_one(src, dst)
        backup_one(src, dst)        # second run must succeed
        assert dst.exists()

    def test_missing_source_raises(self, tmp_path):
        src = tmp_path / "doesnotexist.db"
        dst = tmp_path / "snap.db"
        with pytest.raises(FileNotFoundError):
            backup_one(src, dst)


class TestPrune:
    def test_keeps_newest_n(self, tmp_path):
        # Six snapshot files with naturally-sortable ISO timestamps.
        for i in range(6):
            (tmp_path / f"knowledge-2026-05-0{i+1}T03-00-00.db").write_text("")
        pruned = prune(tmp_path, keep=3)
        kept = sorted(p.name for p in tmp_path.glob("knowledge-*.db"))
        assert len(kept) == 3
        # The three newest survived (highest dates)
        assert kept[-1].endswith("06T03-00-00.db")
        assert kept[0].endswith("04T03-00-00.db")
        # Pruner reported three deletions
        assert len(pruned) == 3

    def test_also_removes_sidecar_checksums(self, tmp_path):
        # Each .db has its .sha256 sibling — pruning should delete
        # both, not orphan the manifest.
        for i in range(4):
            db = tmp_path / f"knowledge-2026-05-0{i+1}T03-00-00.db"
            db.write_text("")
            db.with_suffix(".sha256").write_text("hash")
        prune(tmp_path, keep=2)
        assert len(list(tmp_path.glob("*.db"))) == 2
        assert len(list(tmp_path.glob("*.sha256"))) == 2

    def test_keep_zero_no_op_for_safety(self, tmp_path):
        # keep<1 is treated as "don't prune" rather than "delete everything".
        # Better to leave snapshots than to nuke them on a misconfig.
        (tmp_path / "knowledge-2026-05-01T03-00-00.db").write_text("")
        pruned = prune(tmp_path, keep=0)
        assert pruned == []
        assert len(list(tmp_path.glob("*.db"))) == 1


class TestSha256:
    def test_known_value(self, tmp_path):
        f = tmp_path / "x.bin"
        f.write_bytes(b"ovp")
        # python -c 'import hashlib;print(hashlib.sha256(b"ovp").hexdigest())'
        assert _sha256_of(f) == (
            "6b25ca9b509d11025a53fde57c09cf10f4c13836eeffb1d2a3545d58ef3e8efa"
        )


class TestMain:
    def test_full_run_creates_snapshot_and_checksum(self, tmp_path):
        vault = tmp_path / "vault"
        src = vault / "60-Logs" / "knowledge.db"
        _make_fixture_db(src)

        rc = main(["--vault-dir", str(vault), "--quiet"])
        assert rc == 0

        backups = list((vault / "60-Logs" / "backups").glob("knowledge-*.db"))
        assert len(backups) == 1
        sidecar = backups[0].with_suffix(".sha256")
        assert sidecar.exists()
        # Sidecar format: "<hex>  <filename>\n"
        text = sidecar.read_text(encoding="utf-8")
        assert backups[0].name in text

    def test_main_missing_db_returns_2(self, tmp_path):
        vault = tmp_path / "empty"
        (vault / "60-Logs").mkdir(parents=True)
        rc = main(["--vault-dir", str(vault), "--quiet"])
        assert rc == 2

    def test_main_no_checksum_flag(self, tmp_path):
        vault = tmp_path / "vault"
        _make_fixture_db(vault / "60-Logs" / "knowledge.db")
        rc = main(["--vault-dir", str(vault), "--no-checksum", "--quiet"])
        assert rc == 0
        sidecars = list((vault / "60-Logs" / "backups").glob("*.sha256"))
        assert sidecars == []

    def test_main_prunes_old_snapshots(self, tmp_path):
        # Pre-populate with stale snapshots, run main, observe rotation.
        vault = tmp_path / "vault"
        _make_fixture_db(vault / "60-Logs" / "knowledge.db")
        backups = vault / "60-Logs" / "backups"
        backups.mkdir(parents=True)
        # Five old snapshots, all dated before "now" so they sort lower
        for i in range(5):
            (backups / f"knowledge-2025-01-0{i+1}T03-00-00.db").write_text("stale")

        rc = main(["--vault-dir", str(vault), "--keep", "3", "--quiet"])
        assert rc == 0
        # 5 stale + 1 fresh = 6, keep 3 → 3 remaining (the fresh one
        # plus 2 newest stale ones).
        remaining = sorted(p.name for p in backups.glob("knowledge-*.db"))
        assert len(remaining) == 3
        # The freshly-written snapshot survived.
        assert any("2026" in name for name in remaining)
