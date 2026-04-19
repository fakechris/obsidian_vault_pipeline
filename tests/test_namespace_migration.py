import importlib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_ovp_pipeline_is_the_only_importable_package_namespace():
    assert importlib.import_module("ovp_pipeline").__name__ == "ovp_pipeline"

    old_namespace = "open" + "claw" + "_pipeline"
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(old_namespace)


def test_packaging_metadata_uses_only_ovp_pipeline_namespace():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    old_namespace = "open" + "claw" + "_pipeline"

    assert 'packages = ["src/ovp_pipeline"]' in pyproject
    assert old_namespace not in pyproject
    assert '[project.entry-points."ovp.packs"]' in pyproject
