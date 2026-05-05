"""Tests for the Phase 1 prompt registry.

The registry's job is small but load-bearing: every prompt that
mutates canonical state goes through it, so a silent failure here
(returning the wrong version, parsing frontmatter wrong, etc.)
ripples into evergreens on disk.

Two layers of test:
1. ``PromptRegistry`` against a fixture directory we control.
2. The shipped ``prompts/absorb/v2.md`` loads cleanly through the
   default registry — catches real frontmatter regressions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ovp_pipeline.prompt_registry import (
    Prompt,
    PromptRegistry,
    PromptRegistryError,
    get_default_registry,
    get_prompt,
    reset_default_registry_for_tests,
)


# ---------------------------------------------------------------------------
# Helpers — write fixture prompts to a tmp dir
# ---------------------------------------------------------------------------


def _write_prompt(
    base: Path,
    name: str,
    version: str,
    *,
    status: str = "stable",
    schema_version: int = 1,
    body: str = "PROMPT BODY",
    extra_metadata: dict | None = None,
    raw_text: str | None = None,
) -> Path:
    """Create a fixture prompt file.  Returns its path."""
    target_dir = base / name
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{version}.md"
    if raw_text is not None:
        target.write_text(raw_text, encoding="utf-8")
        return target
    fm_lines = [
        "---",
        f"prompt_name: {name}",
        f"version: {version}",
        f"status: {status}",
        f"schema_version: {schema_version}",
    ]
    for k, v in (extra_metadata or {}).items():
        fm_lines.append(f"{k}: {v}")
    fm_lines.append("---")
    target.write_text("\n".join(fm_lines) + "\n\n" + body + "\n", encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Loading & caching
# ---------------------------------------------------------------------------


class TestRegistryLoad:
    def test_loads_well_formed_prompt(self, tmp_path):
        _write_prompt(tmp_path, "absorb", "v2", body="hello system prompt")
        reg = PromptRegistry(tmp_path)
        prompt = reg.load("absorb", "v2")
        assert isinstance(prompt, Prompt)
        assert prompt.name == "absorb"
        assert prompt.version == "v2"
        assert prompt.status == "stable"
        assert prompt.schema_version == 1
        assert "hello system prompt" in prompt.body

    def test_load_caches_result(self, tmp_path):
        path = _write_prompt(tmp_path, "x", "v1", body="initial")
        reg = PromptRegistry(tmp_path)
        first = reg.load("x", "v1")

        # Mutate the file on disk; cached version should NOT change.
        path.write_text(
            "---\nprompt_name: x\nversion: v1\nstatus: stable\nschema_version: 1\n---\n\nDIFFERENT\n",
            encoding="utf-8",
        )
        second = reg.load("x", "v1")
        assert first is second  # same cached object
        assert "initial" in second.body

    def test_extra_frontmatter_keys_preserved_in_metadata(self, tmp_path):
        _write_prompt(
            tmp_path, "x", "v1",
            extra_metadata={
                "tunables: {body_chars: 1000}": "",  # nested mapping
                "notes": "some notes",
            },
        )
        # Easier: write the full thing manually for nested YAML
        target = tmp_path / "x" / "v1.md"
        target.write_text(
            "---\n"
            "prompt_name: x\n"
            "version: v1\n"
            "status: stable\n"
            "schema_version: 1\n"
            "notes: explanation\n"
            "tunables:\n"
            "  body_chars: 1000\n"
            "  max_output_tokens: 2000\n"
            "---\n\n"
            "BODY\n",
            encoding="utf-8",
        )
        reg = PromptRegistry(tmp_path)
        prompt = reg.load("x", "v1")
        assert prompt.metadata["notes"] == "explanation"
        assert prompt.metadata["tunables"] == {"body_chars": 1000, "max_output_tokens": 2000}


# ---------------------------------------------------------------------------
# Validation — fail-loud paths
# ---------------------------------------------------------------------------


class TestRegistryValidation:
    def test_missing_file_raises(self, tmp_path):
        reg = PromptRegistry(tmp_path)
        with pytest.raises(PromptRegistryError, match="not found"):
            reg.load("absorb", "v99")

    def test_no_frontmatter_raises(self, tmp_path):
        _write_prompt(tmp_path, "x", "v1", raw_text="just a body, no fences\n")
        reg = PromptRegistry(tmp_path)
        with pytest.raises(PromptRegistryError, match="missing YAML frontmatter"):
            reg.load("x", "v1")

    def test_unclosed_frontmatter_raises(self, tmp_path):
        _write_prompt(
            tmp_path, "x", "v1",
            raw_text="---\nprompt_name: x\n\nbody but no closing fence\n",
        )
        reg = PromptRegistry(tmp_path)
        with pytest.raises(PromptRegistryError, match="missing closing"):
            reg.load("x", "v1")

    def test_malformed_yaml_raises(self, tmp_path):
        _write_prompt(
            tmp_path, "x", "v1",
            raw_text="---\nprompt_name: [unclosed\n---\n\nbody\n",
        )
        reg = PromptRegistry(tmp_path)
        with pytest.raises(PromptRegistryError, match="failed to parse YAML"):
            reg.load("x", "v1")

    def test_missing_required_keys_raises(self, tmp_path):
        _write_prompt(
            tmp_path, "x", "v1",
            raw_text="---\nprompt_name: x\nversion: v1\n---\n\nbody\n",
        )
        reg = PromptRegistry(tmp_path)
        with pytest.raises(PromptRegistryError, match="missing required key"):
            reg.load("x", "v1")

    def test_filename_version_mismatch_raises(self, tmp_path):
        """Catch typos: filename says v2.md but frontmatter says v3."""
        target_dir = tmp_path / "x"
        target_dir.mkdir()
        (target_dir / "v2.md").write_text(
            "---\nprompt_name: x\nversion: v3\nstatus: stable\nschema_version: 1\n---\n\nbody\n",
            encoding="utf-8",
        )
        reg = PromptRegistry(tmp_path)
        with pytest.raises(PromptRegistryError, match="version=.*v3.*v2"):
            reg.load("x", "v2")

    def test_filename_name_mismatch_raises(self, tmp_path):
        """Catch the same kind of typo in prompt_name."""
        target_dir = tmp_path / "absorb"
        target_dir.mkdir()
        (target_dir / "v2.md").write_text(
            "---\nprompt_name: x\nversion: v2\nstatus: stable\nschema_version: 1\n---\n\nbody\n",
            encoding="utf-8",
        )
        reg = PromptRegistry(tmp_path)
        with pytest.raises(PromptRegistryError, match="prompt_name=.*x.*absorb"):
            reg.load("absorb", "v2")

    def test_invalid_status_raises(self, tmp_path):
        _write_prompt(tmp_path, "x", "v1", status="oops")
        reg = PromptRegistry(tmp_path)
        with pytest.raises(PromptRegistryError, match="status="):
            reg.load("x", "v1")

    def test_non_int_schema_version_raises(self, tmp_path):
        target = tmp_path / "x" / "v1.md"
        target.parent.mkdir()
        target.write_text(
            "---\nprompt_name: x\nversion: v1\nstatus: stable\nschema_version: not-an-int\n---\n\nbody\n",
            encoding="utf-8",
        )
        reg = PromptRegistry(tmp_path)
        with pytest.raises(PromptRegistryError, match="schema_version"):
            reg.load("x", "v1")


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


class TestRegistryListing:
    def test_list_versions(self, tmp_path):
        _write_prompt(tmp_path, "absorb", "v1")
        _write_prompt(tmp_path, "absorb", "v2")
        _write_prompt(tmp_path, "absorb", "v3-experimental")
        _write_prompt(tmp_path, "other", "v1")
        reg = PromptRegistry(tmp_path)
        assert reg.list_versions("absorb") == ["v1", "v2", "v3-experimental"]
        assert reg.list_versions("other") == ["v1"]
        assert reg.list_versions("nonexistent") == []

    def test_list_names(self, tmp_path):
        _write_prompt(tmp_path, "absorb", "v1")
        _write_prompt(tmp_path, "article-rewriter", "v1")
        # Add a stray file under prompts/ — should be ignored (not a dir)
        (tmp_path / "README.md").write_text("readme", encoding="utf-8")
        reg = PromptRegistry(tmp_path)
        assert reg.list_names() == ["absorb", "article-rewriter"]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


class TestModuleSingleton:
    def test_get_default_registry_returns_same_instance(self):
        reset_default_registry_for_tests()
        try:
            r1 = get_default_registry()
            r2 = get_default_registry()
            assert r1 is r2
        finally:
            reset_default_registry_for_tests()

    def test_reset_clears_singleton(self):
        reset_default_registry_for_tests()
        r1 = get_default_registry()
        reset_default_registry_for_tests()
        r2 = get_default_registry()
        assert r1 is not r2

    def test_get_prompt_uses_default_registry(self):
        """The shipped prompts/absorb/v2.md must load cleanly via the
        package-default registry — this is the canary test that runs
        against real production prompt files."""
        reset_default_registry_for_tests()
        try:
            prompt = get_prompt("absorb", "v2")
            assert prompt.name == "absorb"
            assert prompt.version == "v2"
            assert prompt.status == "stable"
            assert prompt.schema_version == 2
            # Body checks — these are also pinned by
            # test_evergreen_prompt_liberation, but having one here too
            # ensures the registry → load path itself is the failure
            # point if frontmatter breaks.
            assert "你的任务" in prompt.body
            assert "unit_type" in prompt.body
            assert "source_anchor" in prompt.body
        finally:
            reset_default_registry_for_tests()


# ---------------------------------------------------------------------------
# Integration — EvergreenExtractor pulls from registry
# ---------------------------------------------------------------------------


class TestEvergreenExtractorIntegration:
    def test_class_attrs_match_registry(self):
        from ovp_pipeline.auto_evergreen_extractor import EvergreenExtractor

        assert EvergreenExtractor.PROMPT_NAME == "absorb"
        assert EvergreenExtractor.PROMPT_VERSION == "v2"
        # SYSTEM_PROMPT loaded from the registry, should match the file
        registry_body = get_prompt("absorb", "v2").body
        assert EvergreenExtractor.SYSTEM_PROMPT == registry_body
