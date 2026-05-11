"""Codex P1 regression: ``_CallableLLMClient`` exposes both
``.call`` and ``.generate`` so consumers using the BL-062 router /
BL-063 PR#3 agent's ``.generate(...)`` shape can use the adapter
returned by :func:`get_litellm_client` without unwrapping
``._inner``."""

from __future__ import annotations


class _FakeInner:
    """Minimal stand-in for ``LiteLLMClient``: just records calls
    and echoes back the user_prompt."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 3000,
    ) -> str:
        self.calls.append((system_prompt, user_prompt, max_tokens))
        return f"ECHO:{user_prompt}"


def test_callable_llm_client_exposes_generate_alias():
    """The agent (and BL-062 router) duck-type ``llm_client.generate``;
    the adapter must expose it as a first-class method."""
    from ovp_pipeline.llm_client import _CallableLLMClient

    inner = _FakeInner()
    adapter = _CallableLLMClient(inner)

    assert hasattr(adapter, "generate")
    out = adapter.generate("sys", "user", max_tokens=42)
    assert out == "ECHO:user"
    assert inner.calls == [("sys", "user", 42)]


def test_callable_llm_client_call_and_generate_are_equivalent():
    """The two surfaces must hit the same inner code path so behavior
    can't drift between consumers."""
    from ovp_pipeline.llm_client import _CallableLLMClient

    inner = _FakeInner()
    adapter = _CallableLLMClient(inner)

    via_call = adapter.call("sys", "u1", max_tokens=10)
    via_generate = adapter.generate("sys", "u2", max_tokens=10)
    assert via_call.startswith("ECHO:")
    assert via_generate.startswith("ECHO:")
    assert len(inner.calls) == 2


def test_callable_llm_client_generate_handles_tuple_response():
    """Inner clients may return ``tuple[str, dict]`` (the
    auto_article_processor variant).  Adapter must extract the
    string regardless of which method the caller used."""
    from ovp_pipeline.llm_client import _CallableLLMClient

    class _TupleInner:
        def generate(self, *_args, **_kw):
            return ("the text", {"meta": "ignored"})

    adapter = _CallableLLMClient(_TupleInner())
    assert adapter.generate("s", "u") == "the text"
    assert adapter.call("s", "u") == "the text"
