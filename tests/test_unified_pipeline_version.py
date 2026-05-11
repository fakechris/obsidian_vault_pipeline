from __future__ import annotations

from importlib.metadata import version

from ovp_pipeline.unified_pipeline_enhanced import _get_version


def test_unified_pipeline_version_matches_distribution_metadata():
    assert _get_version() == version("obsidian-vault-pipeline")


def test_unified_pipeline_version_falls_back_to_pyproject_when_metadata_missing(monkeypatch):
    import importlib.metadata as metadata

    def missing_distribution(_: str) -> str:
        raise metadata.PackageNotFoundError

    monkeypatch.setattr(metadata, "version", missing_distribution)

    assert _get_version() == "0.17.0"
