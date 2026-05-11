"""Tests for ``ovp-init-user-context`` (M20 / BL-075)."""

from __future__ import annotations

from pathlib import Path

from ovp_pipeline.commands.init_user_context import main
from ovp_pipeline.context_loader import RULES_REL, USER_PROFILE_REL


def test_scaffolds_both_files(tmp_path: Path, capsys):
    rc = main(["--vault-dir", str(tmp_path)])
    assert rc == 0

    user = tmp_path / USER_PROFILE_REL
    rules = tmp_path / RULES_REL
    assert user.exists()
    assert rules.exists()
    assert "type: user-profile" in user.read_text(encoding="utf-8")
    assert "Autonomous Action Rules" in rules.read_text(encoding="utf-8")

    out = capsys.readouterr().out
    assert "write" in out
    assert "Next:" in out


def test_skips_existing_without_force(tmp_path: Path):
    user = tmp_path / USER_PROFILE_REL
    user.parent.mkdir(parents=True)
    user.write_text("hand-edited\n", encoding="utf-8")

    rc = main(["--vault-dir", str(tmp_path)])
    assert rc == 0
    # Hand-edited content must survive.
    assert user.read_text(encoding="utf-8") == "hand-edited\n"


def test_force_overwrites(tmp_path: Path):
    user = tmp_path / USER_PROFILE_REL
    user.parent.mkdir(parents=True)
    user.write_text("stale\n", encoding="utf-8")

    rc = main(["--vault-dir", str(tmp_path), "--force"])
    assert rc == 0
    assert "type: user-profile" in user.read_text(encoding="utf-8")


def test_rejects_missing_vault(tmp_path: Path, capsys):
    rc = main(["--vault-dir", str(tmp_path / "nope")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "does not exist" in err
