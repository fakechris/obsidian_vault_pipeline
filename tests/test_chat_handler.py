"""Tests for M21a / BL-084 — chat_handler + ovp-ask CLI + write-back."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ovp_pipeline.commands.chat_handler import (
    ChatCapExceeded,
    _collect_turn_messages,
    _extract_assistant_turn,
    _find_chat_by_id,
    _parse_anchor,
    check_cost_guardrail,
    run_turn,
    writeback_to_absorb_queue,
)
from ovp_pipeline.llm_profiles import (
    ProfileConfig,
    ProfileLimits,
)


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── _parse_anchor ───────────────────────────────────────────────


def test_parse_anchor_empty_is_standalone():
    assert _parse_anchor("") == ("standalone", "")


def test_parse_anchor_kind_colon_ref():
    assert _parse_anchor("note:20-Areas/x.md") == ("note", "20-Areas/x.md")


def test_parse_anchor_bare_path_defaults_to_note():
    assert _parse_anchor("20-Areas/x.md") == ("note", "20-Areas/x.md")


# ── cost guardrail (no LLM call) ───────────────────────────────


def _profile(name: str = "balanced") -> ProfileConfig:
    return ProfileConfig(
        name=name,
        provider="anthropic",
        model="claude-sonnet-4-6",
        max_tokens=4000,
        temperature=0.7,
    )


def _limits(
    input_cap: int = 16_000,
    output_cap: int = 4_000,
    daily_cap: int = 200_000,
) -> ProfileLimits:
    return ProfileLimits(
        chat_input_tokens_per_request=input_cap,
        chat_output_tokens_per_request=output_cap,
        chat_daily_tokens_per_pack=daily_cap,
    )


def test_cost_guardrail_input_cap(tmp_path: Path):
    with pytest.raises(ChatCapExceeded) as exc_info:
        check_cost_guardrail(
            tmp_path,
            estimated_input_tokens=20_000,
            profile=_profile(),
            limits=_limits(input_cap=16_000),
        )
    assert exc_info.value.cap_kind == "input"
    assert "input cap reached" in str(exc_info.value)


def test_cost_guardrail_daily_cap_reads_audit_log(tmp_path: Path):
    """The daily cap is derived from ``audit_events`` in the JSONL
    log — projection rows (BL-085) are not consulted.  Seed the log
    with completed turns totaling 90% of the cap; one more request
    should trip the cap."""
    log = tmp_path / "60-Logs" / "pipeline.jsonl"
    log.parent.mkdir(parents=True)
    today_iso = _today_iso()
    log.write_text(
        json.dumps(
            {
                "event_id": "abc",
                "ts": today_iso,
                "event_type": "chat_turn_completed",
                "input_tokens": 90_000,
                "output_tokens": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ChatCapExceeded) as exc_info:
        check_cost_guardrail(
            tmp_path,
            estimated_input_tokens=20_000,
            profile=_profile(),
            limits=_limits(input_cap=50_000, daily_cap=100_000),
        )
    assert exc_info.value.cap_kind == "daily"
    assert "daily token cap reached" in str(exc_info.value)


def test_cost_guardrail_failures_still_count(tmp_path: Path):
    """``chat_turn_failed`` events also feed the daily cap so a
    flapping provider can't bypass the budget."""
    log = tmp_path / "60-Logs" / "pipeline.jsonl"
    log.parent.mkdir(parents=True)
    today_iso = _today_iso()
    events = []
    for _ in range(5):
        events.append(
            json.dumps(
                {
                    "event_id": "f",
                    "ts": today_iso,
                    "event_type": "chat_turn_failed",
                    "input_tokens": 18_000,
                    "output_tokens": 0,
                }
            )
        )
    log.write_text("\n".join(events) + "\n", encoding="utf-8")

    with pytest.raises(ChatCapExceeded):
        check_cost_guardrail(
            tmp_path,
            estimated_input_tokens=20_000,
            profile=_profile(),
            limits=_limits(input_cap=80_000, daily_cap=50_000),
        )


def test_cost_guardrail_yesterday_does_not_count(tmp_path: Path):
    """Audit events from yesterday don't count toward today's cap."""
    log = tmp_path / "60-Logs" / "pipeline.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        json.dumps(
            {
                "event_id": "old",
                "ts": "2020-01-01T00:00:00Z",
                "event_type": "chat_turn_completed",
                "input_tokens": 1_000_000,
                "output_tokens": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    # No raise — yesterday's giant burn doesn't count today.
    check_cost_guardrail(
        tmp_path,
        estimated_input_tokens=5_000,
        profile=_profile(),
        limits=_limits(daily_cap=100_000),
    )


def test_cost_guardrail_cap_hit_emits_audit_event(tmp_path: Path):
    """When a cap fires, the handler emits ``chat_cap_hit`` so the
    operator can see the rejection in the audit log."""
    try:
        check_cost_guardrail(
            tmp_path,
            estimated_input_tokens=99_999,
            profile=_profile(),
            limits=_limits(input_cap=10_000),
        )
    except ChatCapExceeded:
        pass

    log = tmp_path / "60-Logs" / "pipeline.jsonl"
    assert log.is_file()
    lines = [
        json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    cap_hits = [evt for evt in lines if evt.get("event_type") == "chat_cap_hit"]
    assert len(cap_hits) == 1
    assert cap_hits[0]["cap_kind"] == "input"


# ── end-to-end run_turn (mocked LLM) ───────────────────────────


@pytest.fixture
def _stub_litellm(monkeypatch):
    """Stub LiteLLM completion so run_turn doesn't make a real call."""

    class _Choice:
        def __init__(self, content: str):
            class _Msg:
                def __init__(self, c: str):
                    self.content = c

            self.message = _Msg(content)

    class _Resp:
        def __init__(self, content: str, prompt_tokens: int, completion_tokens: int):
            self.choices = [_Choice(content)]
            self.usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            }

    def _fake_completion(**kwargs):
        return _Resp(
            "Assistant reply text.",
            prompt_tokens=120,
            completion_tokens=40,
        )

    fake_module = type("LiteLLM", (), {"completion": _fake_completion})
    monkeypatch.setitem(__import__("sys").modules, "litellm", fake_module)

    # Also disable the proxy policy so we don't try to write env vars
    # the test runner doesn't have.
    from ovp_pipeline import llm_defaults

    def _direct(fn, kwargs, **_):
        return fn(**dict(kwargs))

    monkeypatch.setattr(llm_defaults, "completion_with_litellm_policy", _direct)
    return _fake_completion


def test_run_turn_creates_new_session(tmp_path: Path, _stub_litellm):
    """New chat creation: no existing chat_id passed → run_turn
    creates the transcript, appends both user + assistant turns,
    and emits a ``chat_turn_completed`` audit event."""
    result = run_turn(
        tmp_path,
        user_message="What does the digest say about memory?",
        anchor_kind="standalone",
        anchor_ref="",
    )
    assert result.chat_id.startswith("chat-")
    assert result.chat_path.is_file()
    assert result.assistant_body == "Assistant reply text."
    assert result.input_tokens == 120
    assert result.output_tokens == 40
    # Both turns landed in the transcript.
    text = result.chat_path.read_text(encoding="utf-8")
    assert "## User · " in text
    assert "## Assistant · " in text
    # Manifest carries the audit lines.
    assert "<!-- context-manifest" in text


def test_run_turn_emits_completion_audit(tmp_path: Path, _stub_litellm):
    run_turn(
        tmp_path,
        user_message="hello",
    )
    log = tmp_path / "60-Logs" / "pipeline.jsonl"
    events = [
        json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    completions = [e for e in events if e.get("event_type") == "chat_turn_completed"]
    assert len(completions) == 1
    payload = completions[0]
    assert payload["input_tokens"] == 120
    assert payload["output_tokens"] == 40
    assert payload["profile"] == "balanced"
    assert payload["visibility"] == "indexed"


def test_run_turn_reply_continues_session(tmp_path: Path, _stub_litellm):
    """A second run_turn with the chat_id of an existing session
    appends a new pair of turns rather than creating a new file."""
    first = run_turn(tmp_path, user_message="first question")
    assert first.frontmatter.turn_count == 2  # user + assistant

    second = run_turn(
        tmp_path,
        chat_id=first.chat_id,
        user_message="follow-up",
    )
    assert second.chat_path == first.chat_path
    assert second.frontmatter.turn_count == 4  # 2 + 2

    text = first.chat_path.read_text(encoding="utf-8")
    # Two user turns + two assistant turns
    assert text.count("## User · ") == 2
    assert text.count("## Assistant · ") == 2


def test_run_turn_propagates_cap_error(tmp_path: Path, _stub_litellm, monkeypatch):
    """When a cap fires, run_turn raises before the LLM call and
    *doesn't* silently log a completed turn."""
    from ovp_pipeline.commands import chat_handler
    from ovp_pipeline.llm_profiles import ProfileBook
    from ovp_pipeline.llm_profiles import load_profiles as real_load

    def _tiny_limits(*args, **kwargs):
        book = real_load(*args, **kwargs)
        return ProfileBook(
            profiles=book.profiles,
            default_for=book.default_for,
            limits=ProfileLimits(
                chat_input_tokens_per_request=16_000,
                chat_output_tokens_per_request=10,
                chat_daily_tokens_per_pack=10,  # forces daily cap hit
            ),
            source=book.source,
        )

    monkeypatch.setattr(chat_handler, "load_profiles", _tiny_limits)

    # Seed the audit log so today's total > daily cap.
    log = tmp_path / "60-Logs" / "pipeline.jsonl"
    log.parent.mkdir(parents=True)
    today_iso = _today_iso()
    log.write_text(
        json.dumps(
            {
                "ts": today_iso,
                "event_type": "chat_turn_completed",
                "input_tokens": 1_000,
                "output_tokens": 1_000,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ChatCapExceeded):
        run_turn(tmp_path, user_message="x")

    # No chat_turn_completed event for this rejected request.
    events = [
        json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    completions_after = [
        e
        for e in events
        if e.get("event_type") == "chat_turn_completed"
        and e.get("chat_id")  # the seeded one has no chat_id
    ]
    assert completions_after == []


def test_run_turn_records_failure_audit(tmp_path: Path, monkeypatch):
    """An LLM exception emits ``chat_turn_failed`` with estimated
    counts so the daily cap stays honest."""

    class _Boom(RuntimeError):
        pass

    fake_module = type(
        "LiteLLM",
        (),
        {"completion": lambda **_: (_ for _ in ()).throw(_Boom("provider blew up"))},
    )
    monkeypatch.setitem(__import__("sys").modules, "litellm", fake_module)
    from ovp_pipeline import llm_defaults

    def _direct(fn, kwargs, **_):
        return fn(**dict(kwargs))

    monkeypatch.setattr(llm_defaults, "completion_with_litellm_policy", _direct)

    with pytest.raises(_Boom):
        run_turn(tmp_path, user_message="x")

    log = tmp_path / "60-Logs" / "pipeline.jsonl"
    events = [
        json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    failures = [e for e in events if e.get("event_type") == "chat_turn_failed"]
    assert len(failures) == 1
    assert failures[0]["error_class"] == "_Boom"


# ── write-back hook ────────────────────────────────────────────


def test_writeback_creates_absorb_task(tmp_path: Path, _stub_litellm):
    """``ovp-ask absorb`` writes ``ABSORB-chat-<id>-turn-<n>.md`` to
    50-Inbox/02-Tasks and emits ``chat_writeback_handoff``."""
    result = run_turn(tmp_path, user_message="test question")

    task_path = writeback_to_absorb_queue(
        tmp_path,
        chat_id=result.chat_id,
        turn_number=2,  # assistant turn
    )
    assert task_path.is_file()
    assert task_path.name.startswith("ABSORB-chat-")
    text = task_path.read_text(encoding="utf-8")
    assert "type: task" in text
    assert "subtype: absorb-chat" in text
    assert "Captured from inquiry" in text
    assert "Assistant reply text." in text

    log = tmp_path / "60-Logs" / "pipeline.jsonl"
    events = [
        json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    handoffs = [e for e in events if e.get("event_type") == "chat_writeback_handoff"]
    assert len(handoffs) == 1


def test_writeback_rejects_missing_chat(tmp_path: Path):
    with pytest.raises(ValueError, match="chat session not found"):
        writeback_to_absorb_queue(
            tmp_path,
            chat_id="chat-nope",
            turn_number=2,
        )


def test_writeback_rejects_missing_turn(tmp_path: Path, _stub_litellm):
    result = run_turn(tmp_path, user_message="hi")
    with pytest.raises(ValueError, match="not found"):
        writeback_to_absorb_queue(
            tmp_path,
            chat_id=result.chat_id,
            turn_number=99,
        )


def test_extract_assistant_turn_skips_manifest_comment(tmp_path: Path, _stub_litellm):
    """The extracted body is just the assistant prose — no inline
    ``<!-- context-manifest ... -->`` HTML."""
    result = run_turn(tmp_path, user_message="hi")
    body = _extract_assistant_turn(result.chat_path, 2)
    assert "Assistant reply text." in body
    assert "context-manifest" not in body
    assert "<!--" not in body


# ── _find_chat_by_id ───────────────────────────────────────────


def test_find_chat_by_id_returns_none_for_missing(tmp_path: Path):
    assert _find_chat_by_id(tmp_path, "chat-nope") is None


def test_find_chat_by_id_locates_session(tmp_path: Path, _stub_litellm):
    result = run_turn(tmp_path, user_message="hi")
    found = _find_chat_by_id(tmp_path, result.chat_id)
    assert found == result.chat_path


# ── codex P2 — turn history in reply prompts ──────────────────


def test_reply_passes_prior_turns_to_llm(tmp_path: Path, monkeypatch):
    """Codex P2 — ``ovp-ask reply`` must include prior turns in
    the LLM call.  We capture the messages list and verify the
    second turn sees both the first user message + assistant
    reply."""
    captured: list[dict] = []

    def _capture(**kwargs):
        captured.append(kwargs)

        class _Msg:
            content = "Follow-up reply."

        class _Choice:
            message = _Msg()

        return type(
            "_R",
            (),
            {"choices": [_Choice()], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        )()

    fake = type("LiteLLM", (), {"completion": _capture})
    monkeypatch.setitem(__import__("sys").modules, "litellm", fake)
    from ovp_pipeline import llm_defaults

    monkeypatch.setattr(
        llm_defaults,
        "completion_with_litellm_policy",
        lambda fn, kwargs, **_: fn(**dict(kwargs)),
    )

    first = run_turn(tmp_path, user_message="first question")
    run_turn(tmp_path, chat_id=first.chat_id, user_message="follow-up")

    # Two captured calls: new + reply.  The reply's messages list
    # must include the prior user+assistant pair.
    assert len(captured) == 2
    reply_messages = captured[1]["messages"]
    roles = [m["role"] for m in reply_messages]
    # system + history(user + assistant) + new user
    assert roles == ["system", "user", "assistant", "user"]
    assert "first question" in reply_messages[1]["content"]
    assert reply_messages[-1]["content"] == "follow-up"


def test_collect_turn_messages_skips_interrupted(tmp_path: Path, _stub_litellm):
    """Interrupted assistant turns must NOT enter the LLM history —
    they have no useful reply text and would confuse a follow-up."""
    from ovp_pipeline.chat_fileops import mark_interrupted

    result = run_turn(tmp_path, user_message="alpha")
    # Inject an interrupted turn on top.
    mark_interrupted(
        result.chat_path,
        partial_body="half a sentence",
        turn_number=99,
        reason="client_disconnected",
    )
    messages = _collect_turn_messages(result.chat_path)
    # Only the ok user + assistant from the first turn.
    assert all(m["content"] != "half a sentence" for m in messages)
    assert sum(1 for m in messages if m["role"] == "user") == 1
    assert sum(1 for m in messages if m["role"] == "assistant") == 1


# ── codex P2 — profile preserved on reply ─────────────────────


def test_reply_preserves_original_profile(tmp_path: Path, _stub_litellm, monkeypatch):
    """A session created with ``--profile deep`` and a follow-up
    ``ovp-ask reply --id X`` (no ``--profile``) must keep using
    ``deep``, not silently downgrade to the chat default."""
    # Stub a yaml so "deep" exists as a profile.
    cfg = tmp_path / ".ovp" / "llm_profiles.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        """
profiles:
  balanced:
    provider: anthropic
    model: claude-sonnet-4-6
  deep:
    provider: anthropic
    model: claude-opus-4-7
default_for:
  chat: balanced
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("OVP_VAULT_DIR", str(tmp_path))

    first = run_turn(tmp_path, user_message="hi", profile_name="deep")
    assert first.frontmatter.profile == "deep"

    # Reply with no profile override.
    second = run_turn(tmp_path, chat_id=first.chat_id, user_message="follow-up")
    assert second.profile_name == "deep"
    assert second.model.endswith("/claude-opus-4-7")


# ── codex P2 — _extract_assistant_turn stops on transcript headers only


def test_extract_assistant_turn_keeps_h2_subsections(tmp_path: Path):
    """A response containing ``## Next steps`` as a body subsection
    must NOT be truncated at that line — only real transcript
    headers (``## User ·`` / ``## Assistant ·``) end the turn."""
    chat = tmp_path / "x.md"
    chat.write_text(
        "---\ntype: chat\nchat_id: chat-abc\n---\n\n"
        "## User · 2026-05-12T11:00:01Z\n\nWhat next?\n\n"
        "## Assistant · 2026-05-12T11:00:02Z · turn-2\n\n"
        "<!-- context-manifest\n  token_estimate: 10\n-->\n\n"
        "Here is the answer.\n\n## Next steps\n\nDo X then Y.\n\n"
        "## Pros and cons\n\nMore detail.\n\n"
        "## User · 2026-05-12T11:00:05Z\n\nthanks\n",
        encoding="utf-8",
    )
    body = _extract_assistant_turn(chat, 2)
    assert "Here is the answer." in body
    assert "## Next steps" in body
    assert "Do X then Y." in body
    assert "## Pros and cons" in body
    # The next User header *does* terminate the turn.
    assert "thanks" not in body


# ── CodeRabbit Critical — no orphan transcript on cap fail ────


def test_cap_rejection_leaves_no_orphan_transcript(tmp_path: Path, _stub_litellm, monkeypatch):
    """When the cost guardrail fires on a new session, no chat
    file should be created — refusal must be clean (CodeRabbit
    Critical)."""
    from ovp_pipeline.commands import chat_handler
    from ovp_pipeline.llm_profiles import ProfileBook
    from ovp_pipeline.llm_profiles import load_profiles as real_load

    def _tiny(*args, **kwargs):
        book = real_load(*args, **kwargs)
        return ProfileBook(
            profiles=book.profiles,
            default_for=book.default_for,
            limits=ProfileLimits(
                chat_input_tokens_per_request=1,  # forces input-cap hit
                chat_output_tokens_per_request=1,
                chat_daily_tokens_per_pack=1_000_000,
            ),
            source=book.source,
        )

    monkeypatch.setattr(chat_handler, "load_profiles", _tiny)

    with pytest.raises(ChatCapExceeded):
        run_turn(tmp_path, user_message="this will be too big")

    # The chats directory was never created — no orphan transcript.
    chats_dir = tmp_path / "40-Resources" / "Chats"
    assert not chats_dir.exists() or not list(chats_dir.rglob("*.md"))


# ── CodeRabbit Major — chat_id validation in writeback ────────


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../../etc/passwd",
        "chat-abc/../malicious",
        "chat-abc\nID",
        "",
        "not-a-chat-id",
    ],
)
def test_writeback_rejects_malicious_chat_id(tmp_path: Path, bad_id: str):
    with pytest.raises(ValueError, match="not a valid chat identifier"):
        writeback_to_absorb_queue(tmp_path, chat_id=bad_id, turn_number=2)
