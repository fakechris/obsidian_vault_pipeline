"""Tests for M23 / BL-094 — digest config loader + tz resolution."""

from __future__ import annotations

import logging
from datetime import timezone
from pathlib import Path

import pytest
import yaml

from ovp_pipeline.digest_config import (
    DigestConfig,
    load_digest_config,
    resolve_timezone,
)


# ── load_digest_config — defaults / overrides ──────────────────


def test_load_returns_defaults_when_no_override(tmp_path: Path):
    """Empty vault → bundled template defaults."""
    cfg = load_digest_config(tmp_path)
    # Template ships ``tz: ""`` so the loader resolves locally.
    assert cfg.tz == ""
    assert cfg.cluster_threshold == 5
    assert cfg.mid_day_regenerate_button is True
    assert cfg.skip_unchanged is True
    assert "article_processed" in cfg.intake_event_types
    assert "source_archived_to_processed" in cfg.intake_event_types


def test_load_returns_defaults_when_vault_dir_none():
    """Loader treats ``None`` like missing override."""
    cfg = load_digest_config(None)
    assert cfg.cluster_threshold == 5
    assert isinstance(cfg.intake_event_types, tuple)


def test_load_merges_override_with_defaults(tmp_path: Path):
    """Operator pin → override; absent keys fall through to defaults."""
    (tmp_path / ".ovp").mkdir()
    (tmp_path / ".ovp" / "digest.yaml").write_text(
        yaml.safe_dump({"tz": "America/Los_Angeles", "cluster_threshold": 8}),
        encoding="utf-8",
    )
    cfg = load_digest_config(tmp_path)
    assert cfg.tz == "America/Los_Angeles"
    assert cfg.cluster_threshold == 8
    # Untouched defaults still present.
    assert cfg.mid_day_regenerate_button is True
    assert "article_processed" in cfg.intake_event_types


def test_load_full_override_replaces_intake_list(tmp_path: Path):
    """A list override REPLACES, not appends — operator owns the
    full list when they touch it."""
    (tmp_path / ".ovp").mkdir()
    (tmp_path / ".ovp" / "digest.yaml").write_text(
        yaml.safe_dump({"intake_event_types": ["custom_event"]}),
        encoding="utf-8",
    )
    cfg = load_digest_config(tmp_path)
    assert cfg.intake_event_types == ("custom_event",)


def test_load_tolerates_malformed_yaml(tmp_path: Path, caplog):
    """Broken yaml → fall back to defaults, log warning, don't raise."""
    (tmp_path / ".ovp").mkdir()
    (tmp_path / ".ovp" / "digest.yaml").write_text(
        "this: is: not: valid: yaml: ::", encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING):
        cfg = load_digest_config(tmp_path)
    assert cfg.cluster_threshold == 5  # default
    assert any("unreadable" in rec.message for rec in caplog.records)


def test_load_tolerates_non_mapping_root(tmp_path: Path, caplog):
    """A yaml list at root falls back to defaults."""
    (tmp_path / ".ovp").mkdir()
    (tmp_path / ".ovp" / "digest.yaml").write_text("- a\n- b\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        cfg = load_digest_config(tmp_path)
    assert cfg.cluster_threshold == 5
    assert any("not a mapping" in rec.message for rec in caplog.records)


def test_load_rejects_bool_for_int_field(tmp_path: Path, caplog):
    """``True`` in a numeric field falls back, not silently → 1."""
    (tmp_path / ".ovp").mkdir()
    (tmp_path / ".ovp" / "digest.yaml").write_text(
        yaml.safe_dump({"cluster_threshold": True}), encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING):
        cfg = load_digest_config(tmp_path)
    assert cfg.cluster_threshold == 5
    assert any("bool" in rec.message for rec in caplog.records)


def test_load_rejects_negative_cluster_threshold(tmp_path: Path):
    """Negative threshold → fall back to default."""
    (tmp_path / ".ovp").mkdir()
    (tmp_path / ".ovp" / "digest.yaml").write_text(
        yaml.safe_dump({"cluster_threshold": -3}), encoding="utf-8"
    )
    cfg = load_digest_config(tmp_path)
    assert cfg.cluster_threshold == 5


def test_load_empty_intake_list_falls_back(tmp_path: Path):
    """Empty allowlist would silence every intake event → fall back
    to defaults so an operator typo can't disable Layer 0."""
    (tmp_path / ".ovp").mkdir()
    (tmp_path / ".ovp" / "digest.yaml").write_text(
        yaml.safe_dump({"intake_event_types": []}), encoding="utf-8"
    )
    cfg = load_digest_config(tmp_path)
    assert "article_processed" in cfg.intake_event_types


def test_config_is_immutable():
    """Frozen dataclass + tuple sequence — no field assignment, no
    list mutation possible."""
    cfg = DigestConfig()
    with pytest.raises(Exception):
        cfg.cluster_threshold = 99  # type: ignore[misc]
    with pytest.raises(AttributeError):
        cfg.intake_event_types.append("hack")  # type: ignore[attr-defined]


# ── resolve_timezone ───────────────────────────────────────────


def test_resolve_explicit_iana_name_wins():
    cfg = DigestConfig(tz="Asia/Shanghai")
    tz = resolve_timezone(cfg)
    name = getattr(tz, "key", None) or getattr(tz, "zone", None) or str(tz)
    assert name == "Asia/Shanghai"


def test_resolve_unknown_iana_name_falls_back(caplog):
    """A typo'd IANA name → fall back to system locale + log warning."""
    cfg = DigestConfig(tz="Not/A/Real/Zone")
    with caplog.at_level(logging.WARNING):
        tz = resolve_timezone(cfg)
    assert tz is not None
    assert any("not found" in rec.message for rec in caplog.records)


def test_resolve_empty_tz_returns_system_local():
    cfg = DigestConfig(tz="")
    tz = resolve_timezone(cfg)
    # System could be UTC or anything; just verify a usable tzinfo.
    from datetime import datetime

    now = datetime.now(tz)
    assert now.utcoffset() is not None
