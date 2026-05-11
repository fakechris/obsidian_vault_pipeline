"""BL-062 PR#3: shadow-mode integration of ``route_source`` into
``EvergreenExtractor``.

The shadow flag (``enable_router_shadow`` constructor arg, or
``OVP_ABSORB_ROUTER_SHADOW`` env var) makes every
``extract_concepts`` call ALSO issue a Pass 1 router call alongside
the legacy v2 monolithic extract.  The router's decision is logged
via ``absorb_route_decision`` audit but is NOT yet used to drive
extraction — that's a future PR.

These tests verify the shadow integration:

* default-off contract — no extra LLM call when flag is off
* on contract — router runs, audit row emitted
* env var enables when constructor arg is None
* router failures must NOT break legacy extraction (best-effort)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_GOLDEN_ROUTER_RESPONSE = json.dumps({
    "source_value_summary": "Article on LLM eval methodology.",
    "updates": [{
        "slug": "llm-eval-leakage",
        "rationale": "Source paragraphs cover test contamination.",
        "evidence_segments": ["para 5"],
    }],
    "creates": [],
    "skip_reason": "",
})

# Legacy v2 extract response shape — minimal valid wrapper.  We use
# this for every "v2 call should still happen" assertion below.
_GOLDEN_LEGACY_RESPONSE = json.dumps({
    "source_value_summary": "x",
    "units": [],
    "skip_reason": "no extractable units",
})


def _make_extractor(tmp_path: Path, *, llm_responses: list[str], **kwargs):
    """Build an extractor with a scripted MagicMock LLM client.

    ``llm_responses`` is a queue of strings ``llm.generate(...)``
    returns in order.  Test asserts call count after the run.
    """
    from ovp_pipeline.auto_evergreen_extractor import (
        EvergreenExtractor,
        PipelineLogger,
    )

    log = PipelineLogger(tmp_path / "pipeline.jsonl")
    llm = MagicMock()
    llm.generate.side_effect = list(llm_responses)
    return EvergreenExtractor(
        llm_client=llm, logger=log, vault_dir=tmp_path, **kwargs,
    ), llm, log


def _read_audit_events(log_file: Path) -> list[dict]:
    if not log_file.exists():
        return []
    return [json.loads(line) for line in log_file.read_text(
        encoding="utf-8"
    ).splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Default off: no extra LLM call
# ---------------------------------------------------------------------------


def test_shadow_off_by_default_no_extra_llm_call(tmp_path, monkeypatch):
    """No ``OVP_ABSORB_ROUTER_SHADOW`` env, no constructor arg →
    shadow is OFF.  ``extract_concepts`` calls ``llm.generate``
    exactly once (legacy v2 only)."""
    monkeypatch.delenv("OVP_ABSORB_ROUTER_SHADOW", raising=False)

    extractor, llm, _log = _make_extractor(
        tmp_path, llm_responses=[_GOLDEN_LEGACY_RESPONSE],
    )
    assert extractor.enable_router_shadow is False

    extractor.extract_concepts(tmp_path / "x.md", "body")

    assert llm.generate.call_count == 1


def test_shadow_constructor_arg_overrides_env(tmp_path, monkeypatch):
    """Explicit ``enable_router_shadow=False`` wins even when env says yes."""
    monkeypatch.setenv("OVP_ABSORB_ROUTER_SHADOW", "1")

    extractor, _llm, _log = _make_extractor(
        tmp_path,
        llm_responses=[_GOLDEN_LEGACY_RESPONSE],
        enable_router_shadow=False,
    )
    assert extractor.enable_router_shadow is False


# ---------------------------------------------------------------------------
# Shadow on: router runs alongside, audit row emitted
# ---------------------------------------------------------------------------


def test_shadow_on_runs_router_alongside_legacy(tmp_path, monkeypatch):
    """When shadow is on, ``llm.generate`` is called twice per source
    (router + legacy) and an ``absorb_route_decision`` audit row is
    appended to the pipeline log."""
    monkeypatch.delenv("OVP_ABSORB_ROUTER_SHADOW", raising=False)

    extractor, llm, log = _make_extractor(
        tmp_path,
        # Order: router runs FIRST (our wrapper invokes it before the
        # legacy call), then legacy v2.
        llm_responses=[_GOLDEN_ROUTER_RESPONSE, _GOLDEN_LEGACY_RESPONSE],
        enable_router_shadow=True,
    )

    extractor.extract_concepts(tmp_path / "x.md", "body about LLM evals")

    assert llm.generate.call_count == 2

    audit_rows = _read_audit_events(log.log_file)
    route_rows = [r for r in audit_rows if r.get("event_type") == "absorb_route_decision"]
    assert len(route_rows) == 1
    row = route_rows[0]
    assert row["status"] == "ok"
    assert row["update_slugs"] == ["llm-eval-leakage"]
    assert row["prompt_version"] == "v2_router"


def test_shadow_env_var_enables_when_constructor_arg_is_none(tmp_path, monkeypatch):
    """Setting ``OVP_ABSORB_ROUTER_SHADOW=1`` and not passing the
    constructor arg → shadow is ON (env-var fallback path)."""
    monkeypatch.setenv("OVP_ABSORB_ROUTER_SHADOW", "1")

    extractor, llm, _log = _make_extractor(
        tmp_path,
        llm_responses=[_GOLDEN_ROUTER_RESPONSE, _GOLDEN_LEGACY_RESPONSE],
    )
    assert extractor.enable_router_shadow is True

    extractor.extract_concepts(tmp_path / "x.md", "body")
    assert llm.generate.call_count == 2


@pytest.mark.parametrize("env_value", ["0", "false", "no", "off", ""])
def test_shadow_falsy_env_values_keep_shadow_off(tmp_path, monkeypatch, env_value):
    """Falsy / explicit-no env values → shadow stays off."""
    monkeypatch.setenv("OVP_ABSORB_ROUTER_SHADOW", env_value)

    extractor, _llm, _log = _make_extractor(
        tmp_path, llm_responses=[_GOLDEN_LEGACY_RESPONSE],
    )
    assert extractor.enable_router_shadow is False


# ---------------------------------------------------------------------------
# Best-effort: shadow failures cannot break legacy extraction
# ---------------------------------------------------------------------------


def test_shadow_router_parse_failure_does_not_break_legacy(tmp_path, monkeypatch):
    """Router LLM returns garbage → router emits parse_error audit
    AND legacy v2 still runs end-to-end."""
    monkeypatch.delenv("OVP_ABSORB_ROUTER_SHADOW", raising=False)

    extractor, llm, log = _make_extractor(
        tmp_path,
        # Router gets unusable response; legacy still gets valid.
        llm_responses=["I can't help with that.", _GOLDEN_LEGACY_RESPONSE],
        enable_router_shadow=True,
    )

    # Should NOT raise.
    units = extractor.extract_concepts(tmp_path / "x.md", "body")
    assert isinstance(units, list)
    assert llm.generate.call_count == 2

    audit = _read_audit_events(log.log_file)
    parse_errors = [
        r for r in audit
        if r.get("event_type") == "absorb_route_decision"
        and r.get("status") == "parse_error"
    ]
    assert len(parse_errors) == 1


def test_shadow_router_llm_exception_does_not_break_legacy(tmp_path, monkeypatch):
    """LLM raising on the FIRST (router) call must not abort the
    second (legacy) call.  ``MagicMock.side_effect`` consumes one
    side-effect per call, and an exception counts as a side-effect
    consumed."""
    monkeypatch.delenv("OVP_ABSORB_ROUTER_SHADOW", raising=False)

    extractor, llm, log = _make_extractor(
        tmp_path,
        llm_responses=[],
        enable_router_shadow=True,
    )
    # Custom side_effect: first call raises (router), second call
    # returns the legacy response.
    llm.generate.side_effect = [
        RuntimeError("simulated rate limit"),
        _GOLDEN_LEGACY_RESPONSE,
    ]

    units = extractor.extract_concepts(tmp_path / "x.md", "body")
    assert isinstance(units, list)
    assert llm.generate.call_count == 2

    audit = _read_audit_events(log.log_file)
    # Post-BL-068-fix: LLM raising is ``request_error``, not
    # ``parse_error``.  Distinguishing provider failures from JSON
    # parsing failures lets dashboards triage by cause.
    request_errors = [
        r for r in audit
        if r.get("event_type") == "absorb_route_decision"
        and r.get("status") == "request_error"
    ]
    assert len(request_errors) == 1
    assert "simulated rate limit" in request_errors[0]["error"]


def test_shadow_unexpected_exception_logs_shadow_error(tmp_path, monkeypatch):
    """If something inside the shadow path raises *outside*
    ``route_source``'s own contract (e.g. import/registry failure
    that escapes the helper), the wrapper's outer ``except`` catches
    it, emits ``absorb_router_shadow_error``, and lets the legacy
    extract proceed.

    Simulated by monkey-patching ``absorb_router.route_source`` to
    raise — the wrapper imports it lazily inside ``_run_router_shadow``
    so the patch is picked up.
    """
    monkeypatch.delenv("OVP_ABSORB_ROUTER_SHADOW", raising=False)

    def _broken_route_source(*args, **kwargs):
        # Accept the same call signature ``_run_router_shadow`` uses
        # (one positional ``llm_client`` + several kwargs) so the
        # raise inside the function is what the wrapper sees, not a
        # TypeError before that.
        raise RuntimeError("simulated registry failure")

    monkeypatch.setattr(
        "ovp_pipeline.absorb_router.route_source",
        _broken_route_source,
    )

    extractor, llm, log = _make_extractor(
        tmp_path,
        llm_responses=[_GOLDEN_LEGACY_RESPONSE],
        enable_router_shadow=True,
    )

    # Legacy extraction should still complete.
    units = extractor.extract_concepts(tmp_path / "x.md", "body")
    assert isinstance(units, list)
    # Legacy still issued exactly one call (router was bypassed
    # because route_source raised inside the wrapper's try block).
    assert llm.generate.call_count == 1

    audit = _read_audit_events(log.log_file)
    shadow_errors = [
        r for r in audit
        if r.get("event_type") == "absorb_router_shadow_error"
    ]
    assert len(shadow_errors) == 1
    assert "simulated registry failure" in shadow_errors[0]["error"]


# ---------------------------------------------------------------------------
# BL-068: router LLM config (separate model for the router)
# ---------------------------------------------------------------------------


def test_build_router_llm_returns_main_when_no_override(tmp_path, monkeypatch):
    """Default behavior: with no ``OVP_ROUTER_*`` env vars set, the
    router uses the same LLM client as the main extractor — no new
    client constructed, no spend on a second provider."""
    for var in (
        "OVP_ROUTER_MODEL",
        "OVP_ROUTER_API_BASE",
        "OVP_ROUTER_API_KEY",
        "OVP_ROUTER_API_TYPE",
    ):
        monkeypatch.delenv(var, raising=False)
    extractor, llm, _ = _make_extractor(tmp_path, llm_responses=[])
    assert extractor._build_router_llm() is llm


def test_build_router_llm_constructs_override_when_env_set(
    tmp_path, monkeypatch,
):
    """BL-068: setting ``OVP_ROUTER_*`` env vars yields a new
    ``LiteLLMClient`` wired to the override config.  Verifies the
    operator can swap router model independently of main extractor —
    the load-bearing fix for MiniMax-2K-cap on the router prompt."""
    from ovp_pipeline.auto_evergreen_extractor import LiteLLMClient

    monkeypatch.setenv("OVP_ROUTER_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("OVP_ROUTER_API_BASE", "https://token.sensenova.cn/v1")
    monkeypatch.setenv("OVP_ROUTER_API_KEY", "sk-test-router")
    monkeypatch.setenv("OVP_ROUTER_API_TYPE", "openai")
    extractor, llm, _ = _make_extractor(tmp_path, llm_responses=[])
    router_llm = extractor._build_router_llm()
    assert router_llm is not llm
    assert isinstance(router_llm, LiteLLMClient)
    assert router_llm.api_base == "https://token.sensenova.cn/v1"
    assert router_llm.model == "openai/deepseek-v4-flash"


def test_router_config_does_not_leak_main_key_to_different_endpoint(monkeypatch):
    """Security contract: when ``OVP_ROUTER_API_BASE`` overrides the
    endpoint, the main vault's api_key must NOT be inherited.  The
    operator must explicitly set ``OVP_ROUTER_API_KEY`` for the new
    endpoint, or auth fails with a visible error rather than
    silently shipping the main key to an unintended provider."""
    from ovp_pipeline.llm_defaults import resolve_router_llm_config

    # Simulate a main vault config with a real key.
    monkeypatch.setenv("AUTO_VAULT_API_KEY", "sk-main-secret")
    # Operator overrides ONLY the endpoint (forgets the router key).
    monkeypatch.setenv(
        "OVP_ROUTER_API_BASE", "https://different.provider.com/v1",
    )
    monkeypatch.delenv("OVP_ROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OVP_ROUTER_MODEL", raising=False)
    monkeypatch.delenv("OVP_ROUTER_API_TYPE", raising=False)

    cfg = resolve_router_llm_config()
    assert cfg is not None
    assert cfg["api_base"] == "https://different.provider.com/v1"
    assert cfg["api_key"] == "", (
        "main key must not leak to a different endpoint"
    )


def test_router_config_inherits_main_key_when_base_unchanged(monkeypatch):
    """Inverse of the leak-prevention rule: when the endpoint is NOT
    overridden (operator just wants to test a different model on
    the same provider), inheriting the main key is the convenient
    behavior — no need to repeat the same secret in env."""
    from ovp_pipeline.llm_defaults import resolve_router_llm_config

    monkeypatch.setenv("AUTO_VAULT_API_KEY", "sk-main-secret")
    monkeypatch.setenv("OVP_ROUTER_MODEL", "different-model-same-provider")
    monkeypatch.delenv("OVP_ROUTER_API_BASE", raising=False)
    monkeypatch.delenv("OVP_ROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OVP_ROUTER_API_TYPE", raising=False)

    cfg = resolve_router_llm_config()
    assert cfg is not None
    assert cfg["api_key"] == "sk-main-secret"


def test_build_router_llm_falls_back_to_main_on_construct_error(
    tmp_path, monkeypatch,
):
    """Best-effort contract: if the override config fails to build
    a client (e.g. missing dep / bad config), the shadow path falls
    back to the main LLM and emits ``absorb_router_llm_build_error``
    rather than killing the main extract path."""
    from ovp_pipeline import auto_evergreen_extractor

    monkeypatch.setenv("OVP_ROUTER_MODEL", "x")
    monkeypatch.setenv("OVP_ROUTER_API_BASE", "https://example.com")
    monkeypatch.setenv("OVP_ROUTER_API_KEY", "sk-test")

    class BoomLLM:
        def __init__(self, *_a, **_kw):
            raise RuntimeError("simulated litellm init failure")

    monkeypatch.setattr(
        auto_evergreen_extractor, "LiteLLMClient", BoomLLM,
    )

    extractor, llm, log = _make_extractor(tmp_path, llm_responses=[])
    assert extractor._build_router_llm() is llm
    audit = _read_audit_events(log.log_file)
    build_errors = [
        r for r in audit
        if r.get("event_type") == "absorb_router_llm_build_error"
    ]
    assert len(build_errors) == 1
    assert "simulated litellm init failure" in build_errors[0]["error"]
