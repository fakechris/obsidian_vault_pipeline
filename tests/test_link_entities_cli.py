"""Tests for the ``ovp-link-entities`` CLI iteration helpers.

Focused on the two invariants surfaced in PR-128 review:
  * ``UnicodeDecodeError`` on a binary file must NOT crash the scan
  * Skip-parts (``__pycache__`` / ``_backup`` / ``.git``) must be
    checked against the path RELATIVE to the vault root, not the
    absolute path — otherwise a vault placed inside a directory
    that happens to contain ``_backup`` gets silently skipped.
"""

from __future__ import annotations

import json
from pathlib import Path

from ovp_pipeline.commands.link_entities import _iter_target_files, main


def _seed_authors_jsonl(tmp_path: Path) -> Path:
    p = tmp_path / "60-Logs" / "authors.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"handle": "karpathy", "authority": 0.95}) + "\n",
        encoding="utf-8",
    )
    return p


class TestIterTargetFilesSkipPartsRelative:
    """The skip-parts check must apply to the path RELATIVE to the
    vault root.  Pre-fix the absolute-path check would silently skip
    every file when the vault sat inside an ancestor directory whose
    name matched a skip token (e.g., a developer running from
    ``~/_backup/ovp-vault``)."""

    def test_vault_inside_backup_dir_still_yields(self, tmp_path):
        # Construct a vault at <tmp_path>/_backup/vault/...
        # so the absolute path includes ``_backup`` but the path
        # relative to the vault root does not.
        vault = tmp_path / "_backup" / "vault"
        scan_dir = Path("10-Knowledge/Evergreen")
        target = vault / scan_dir / "x.md"
        target.parent.mkdir(parents=True)
        target.write_text("body", encoding="utf-8")
        out = list(_iter_target_files(vault, [scan_dir]))
        # File yielded despite the ``_backup`` ancestor.
        assert any(p == target for p in out)

    def test_skip_parts_inside_vault_still_filtered(self, tmp_path):
        # The legitimate skip case still works: a ``_backup`` dir
        # *inside* the vault should be skipped.
        vault = tmp_path / "vault"
        scan_dir = Path("10-Knowledge/Evergreen")
        good = vault / scan_dir / "real.md"
        skipped = vault / scan_dir / "_backup" / "old.md"
        good.parent.mkdir(parents=True)
        skipped.parent.mkdir(parents=True)
        good.write_text("body", encoding="utf-8")
        skipped.write_text("body", encoding="utf-8")
        out = list(_iter_target_files(vault, [scan_dir]))
        names = {p.name for p in out}
        assert "real.md" in names
        assert "old.md" not in names


class TestCliHandlesBinaryFiles:
    """A binary file with .md extension (rare but possible — e.g.,
    a corrupted save) raises ``UnicodeDecodeError`` from
    ``read_text(encoding='utf-8')``.  That's NOT a subclass of
    ``OSError``, so the pre-fix ``except OSError`` would miss it
    and crash the whole CLI run."""

    def test_binary_md_does_not_crash(self, tmp_path, capsys):
        vault = tmp_path / "vault"
        # Seed the entity layer so the CLI has aliases to work with.
        _seed_authors_jsonl(vault)
        scan_dir = Path("10-Knowledge/Evergreen")
        # One legitimate utf-8 file…
        good = vault / scan_dir / "good.md"
        good.parent.mkdir(parents=True)
        good.write_text("see karpathy talk", encoding="utf-8")
        # …and one with non-utf8 bytes (a stray Latin-1 file someone
        # dropped in the vault).
        bad = vault / scan_dir / "bad.md"
        bad.write_bytes(b"\xff\xfe binary garbage \x00\x01\x02")

        rc = main(["--vault-dir", str(vault), "--no-stubs", "--quiet"])
        assert rc == 0
        # The good file got rewritten despite the bad one's presence.
        assert "[[karpathy]]" in good.read_text(encoding="utf-8")
        # The bad file was NOT touched (read failed, skipped).
        assert bad.read_bytes().startswith(b"\xff\xfe")
