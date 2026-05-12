"""Tests for M21a / BL-081 — LLM profile loader."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ovp_pipeline.llm_profiles import (
    CONFIG_REL,
    DEFAULT_USE_CASES,
    ProfileConfig,
    clear_cache,
    load_profiles,
    profile_for_use_case,
    resolve_profile,
)

# ── fixtures ────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def vault_with_yaml(tmp_path: Path) -> Path:
    """Vault containing a realistic .ovp/llm_profiles.yaml."""
    cfg = tmp_path / CONFIG_REL
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        """
profiles:
  fast:
    provider: anthropic
    model: MiniMax-M2.7-highspeed
    api_base: https://api.minimaxi.com/anthropic
    max_tokens: 1500
    temperature: 0.6
    cost_per_1k_in: 0.0001
    cost_per_1k_out: 0.0003
  balanced:
    provider: anthropic
    model: claude-sonnet-4-6
    max_tokens: 4000
    temperature: 0.7
    cost_per_1k_in: 0.003
    cost_per_1k_out: 0.015
  deep:
    provider: anthropic
    model: claude-opus-4-7
    max_tokens: 6000
    temperature: 0.7
    cost_per_1k_in: 0.015
    cost_per_1k_out: 0.075

default_for:
  chat: balanced
  extraction: fast
  digest: balanced
  router: fast

limits:
  chat_input_tokens_per_request: 16000
  chat_output_tokens_per_request: 4000
  chat_daily_tokens_per_pack: 200000
""".lstrip(),
        encoding="utf-8",
    )
    return tmp_path


# ── parsing ────────────────────────────────────────────────────


def test_load_profiles_parses_yaml(vault_with_yaml: Path):
    book = load_profiles(vault_with_yaml)
    assert book.source == "yaml"
    assert set(book.profiles) == {"fast", "balanced", "deep"}
    balanced = book.profiles["balanced"]
    assert balanced.provider == "anthropic"
    assert balanced.model == "claude-sonnet-4-6"
    assert balanced.max_tokens == 4000
    assert balanced.temperature == pytest.approx(0.7)
    assert balanced.cost_per_1k_in == pytest.approx(0.003)


def test_default_for_resolves_to_named_profile(vault_with_yaml: Path):
    book = load_profiles(vault_with_yaml)
    assert book.default_for["chat"] == "balanced"
    assert book.default_for["extraction"] == "fast"
    assert book.default_for["router"] == "fast"


def test_limits_round_trip(vault_with_yaml: Path):
    book = load_profiles(vault_with_yaml)
    assert book.limits.chat_input_tokens_per_request == 16_000
    assert book.limits.chat_output_tokens_per_request == 4_000
    assert book.limits.chat_daily_tokens_per_pack == 200_000


# ── public API ─────────────────────────────────────────────────


def test_resolve_profile_returns_named(vault_with_yaml: Path):
    cfg = resolve_profile("deep", vault_dir=vault_with_yaml)
    assert isinstance(cfg, ProfileConfig)
    assert cfg.name == "deep"
    assert cfg.model == "claude-opus-4-7"


def test_resolve_profile_raises_for_unknown(vault_with_yaml: Path):
    with pytest.raises(KeyError, match="totally-bogus"):
        resolve_profile("totally-bogus", vault_dir=vault_with_yaml)


def test_profile_for_use_case_walks_default_for(vault_with_yaml: Path):
    chat = profile_for_use_case("chat", vault_dir=vault_with_yaml)
    extraction = profile_for_use_case("extraction", vault_dir=vault_with_yaml)
    assert chat.name == "balanced"
    assert extraction.name == "fast"


def test_profile_for_use_case_falls_back_to_balanced_for_unknown_use(
    vault_with_yaml: Path,
):
    """An unmapped use-case still returns the balanced profile so
    callers don't have to do their own None-check."""
    cfg = profile_for_use_case("unknown_use", vault_dir=vault_with_yaml)
    assert cfg.name == "balanced"


def test_litellm_model_collapses_provider_prefix(vault_with_yaml: Path):
    cfg = resolve_profile("balanced", vault_dir=vault_with_yaml)
    assert cfg.litellm_model == "anthropic/claude-sonnet-4-6"


def test_default_use_cases_constant_is_canonical_four():
    """Lock the canonical use-case set so future BL changes notice
    when they add or remove a use case."""
    assert DEFAULT_USE_CASES == ("chat", "extraction", "digest", "router")


# ── fallback path ──────────────────────────────────────────────


def test_missing_yaml_uses_fallback_balanced(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTO_VAULT_MODEL", "anthropic/MiniMax-M2.7-highspeed")
    monkeypatch.setenv("AUTO_VAULT_API_KEY", "test-key")
    monkeypatch.setenv("AUTO_VAULT_API_BASE", "https://example/anthropic")

    book = load_profiles(tmp_path)
    assert book.source == "fallback"
    assert set(book.profiles) == {"balanced"}
    cfg = book.profiles["balanced"]
    assert cfg.provider == "anthropic"
    assert cfg.model == "MiniMax-M2.7-highspeed"
    assert cfg.api_key == "test-key"
    assert cfg.api_base == "https://example/anthropic"


def test_profile_for_use_case_works_without_yaml(tmp_path: Path):
    """The fallback book has a balanced profile assigned to every
    canonical use case so legacy callers never get None back."""
    cfg = profile_for_use_case("chat", vault_dir=tmp_path)
    assert cfg.name == "balanced"


def test_malformed_yaml_falls_back_silently(tmp_path: Path, caplog):
    cfg = tmp_path / CONFIG_REL
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(": : : not yaml", encoding="utf-8")

    book = load_profiles(tmp_path)
    assert book.source == "fallback"
    # The warning got logged so the operator can find the broken config.
    assert any("failed to parse" in record.getMessage() for record in caplog.records)


def test_profiles_block_missing_provider_skipped(tmp_path: Path, caplog):
    cfg = tmp_path / CONFIG_REL
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        """
profiles:
  broken:
    model: anthropic/claude-sonnet-4-6
  ok:
    provider: anthropic
    model: claude-sonnet-4-6
""",
        encoding="utf-8",
    )
    book = load_profiles(tmp_path)
    assert book.source == "yaml"
    assert "broken" not in book.profiles
    assert "ok" in book.profiles


def test_default_for_pointing_at_unknown_profile_warns(
    tmp_path: Path,
    caplog,
):
    cfg = tmp_path / CONFIG_REL
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        """
profiles:
  balanced:
    provider: anthropic
    model: claude-sonnet-4-6
default_for:
  chat: balanced
  extraction: nonexistent
""",
        encoding="utf-8",
    )
    book = load_profiles(tmp_path)
    assert book.default_for["chat"] == "balanced"
    # nonexistent didn't make it through, but every canonical use
    # case still has a value — falls back to balanced.
    assert book.default_for["extraction"] == "balanced"
    assert any("nonexistent" in record.getMessage() for record in caplog.records)


def test_oversize_yaml_falls_back(tmp_path: Path, caplog):
    cfg = tmp_path / CONFIG_REL
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_bytes(b"x" * (64 * 1024))  # 64 KB > MAX_BYTES
    book = load_profiles(tmp_path)
    assert book.source == "fallback"
    assert any("exceeded" in r.getMessage() for r in caplog.records)


# ── cache invalidation ────────────────────────────────────────


def test_cache_invalidates_on_mtime_change(tmp_path: Path):
    cfg = tmp_path / CONFIG_REL
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        """
profiles:
  balanced:
    provider: anthropic
    model: v1
""".lstrip(),
        encoding="utf-8",
    )
    first = resolve_profile("balanced", vault_dir=tmp_path)
    assert first.model == "v1"

    # APFS rounds mtime to the second on stressed CI runners.
    time.sleep(1.1)
    cfg.write_text(
        """
profiles:
  balanced:
    provider: anthropic
    model: v2
""".lstrip(),
        encoding="utf-8",
    )
    second = resolve_profile("balanced", vault_dir=tmp_path)
    assert second.model == "v2"


# ── env-var pollution guard ───────────────────────────────────


def test_explicit_yaml_overrides_env_vars(
    vault_with_yaml: Path,
    monkeypatch,
):
    """An operator with both AUTO_VAULT_* env vars *and* a yaml gets
    the yaml — env vars are the fallback, not an override."""
    monkeypatch.setenv("AUTO_VAULT_MODEL", "anthropic/MiniMax-M2.7-highspeed")
    monkeypatch.setenv("AUTO_VAULT_API_KEY", "env-key")
    cfg = resolve_profile("balanced", vault_dir=vault_with_yaml)
    assert cfg.model == "claude-sonnet-4-6"
    # api_key isn't auto-pulled from env into yaml-defined profiles —
    # the LiteLLM client picks it up from the environment directly.
    assert cfg.api_key is None
