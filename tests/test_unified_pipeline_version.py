from __future__ import annotations

from importlib.metadata import version

from ovp_pipeline.unified_pipeline_enhanced import _get_version


def test_unified_pipeline_version_matches_distribution_metadata():
    assert _get_version() == version("obsidian-vault-pipeline")
