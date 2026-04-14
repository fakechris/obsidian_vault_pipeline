from __future__ import annotations

import os
from pathlib import Path


def test_installer_loads_ovp_scripts_from_pyproject():
    from openclaw_pipeline.installer import load_project_scripts

    scripts = load_project_scripts(Path(__file__).resolve().parents[1])

    assert scripts["ovp"] == "openclaw_pipeline.unified_pipeline_enhanced:main"
    assert scripts["ovp-ui"] == "openclaw_pipeline.commands.ui_server:main"
    assert scripts["ovp-truth"] == "openclaw_pipeline.commands.truth_api:main"


def test_installer_writes_executable_shims(tmp_path):
    from openclaw_pipeline.installer import write_shims

    target_dir = tmp_path / "bin"
    scripts = {
        "ovp-ui": "openclaw_pipeline.commands.ui_server:main",
        "ovp-truth": "openclaw_pipeline.commands.truth_api:main",
    }

    created = write_shims(
        target_dir=target_dir,
        scripts=scripts,
        python_executable="/opt/homebrew/opt/python@3.13/bin/python3.13",
    )

    assert [path.name for path in created] == ["ovp-truth", "ovp-ui"]

    wrapper = target_dir / "ovp-ui"
    body = wrapper.read_text(encoding="utf-8")
    assert body.startswith("#!/usr/bin/env bash")
    assert "sys.argv[0]" in body
    assert "ovp-ui" in body
    assert "exec /opt/homebrew/opt/python@3.13/bin/python3.13" in body
    assert "from openclaw_pipeline.commands.ui_server import main" in body
    assert os.access(wrapper, os.X_OK)


def test_installer_shell_escapes_python_executable(tmp_path):
    from openclaw_pipeline.installer import write_shims

    target_dir = tmp_path / "bin"
    scripts = {"ovp-ui": "openclaw_pipeline.commands.ui_server:main"}

    created = write_shims(
        target_dir=target_dir,
        scripts=scripts,
        python_executable="/tmp/python $HOME/bin/python3",
    )

    assert len(created) == 1
    body = (target_dir / "ovp-ui").read_text(encoding="utf-8")
    assert "exec '/tmp/python $HOME/bin/python3'" in body


def test_choose_install_bin_dir_prefers_user_path_entry(tmp_path):
    from openclaw_pipeline.installer import choose_install_bin_dir

    home = tmp_path / "home"
    user_local_bin = home / ".local" / "bin"
    user_local_bin.mkdir(parents=True)
    external_bin = tmp_path / "external-bin"
    external_bin.mkdir()

    selected = choose_install_bin_dir(
        path_env=os.pathsep.join([str(external_bin), str(user_local_bin)]),
        home_dir=home,
    )

    assert selected == user_local_bin


def test_choose_install_bin_dir_falls_back_to_user_local_bin(tmp_path):
    from openclaw_pipeline.installer import choose_install_bin_dir

    home = tmp_path / "home"
    selected = choose_install_bin_dir(path_env="", home_dir=home)

    assert selected == home / ".local" / "bin"
