"""BL-063 PR#3 — Live Concept agent + section-aware patch helpers.

Covers:

1. ``patch_agent_section`` — replace one H2 body, preserve
   frontmatter + other sections, refuse non-allowlisted writes.
2. ``parse_agent_response`` — JSON tolerance (markdown fence,
   preamble, missing keys).
3. ``synthesize_live_concept`` — happy path with mocked LLM.
4. ``fire_agent_for_concept`` — full lifecycle including
   lastAttemptAt + lastRunAt + lastRunError stamps.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _make_concept_file(tmp_path: Path, *, slug: str = "topic") -> Path:
    """Build a minimal Live Concept file with three agent-owned
    sections seeded with placeholder content."""
    path = tmp_path / "30-Projects" / "Tracking" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "type: live-concept\n"
        "live:\n"
        "  objective: Track topic.\n"
        "  active: true\n"
        "  scope_evergreens:\n"
        "    - alpha\n"
        "    - beta\n"
        "---\n"
        "\n"
        "# Topic\n"
        "\n"
        "## My take\n"
        "\n"
        "MY personal interpretation.  Agent must not touch this.\n"
        "\n"
        "## Current synthesis\n"
        "\n"
        "Old synthesis placeholder.\n"
        "\n"
        "## Recent evidence\n"
        "\n"
        "(none yet)\n"
        "\n"
        "## Tensions\n"
        "\n"
        "(none yet)\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# patch_agent_section
# ---------------------------------------------------------------------------


def test_patch_agent_section_replaces_body_keeps_others(tmp_path):
    from ovp_pipeline.live_concept_fileops import patch_agent_section

    path = _make_concept_file(tmp_path)
    changed = patch_agent_section(
        path, "Current synthesis",
        "- Bullet one\n- Bullet two\n",
    )
    assert changed is True
    text = path.read_text(encoding="utf-8")
    assert "## Current synthesis" in text
    assert "- Bullet one" in text
    assert "Old synthesis placeholder" not in text
    # My take stays exactly the same — the contract.
    assert "MY personal interpretation.  Agent must not touch this." in text
    # Frontmatter intact.
    assert "type: live-concept" in text
    assert "scope_evergreens:" in text


def test_patch_agent_section_creates_section_when_missing(tmp_path):
    """Section heading doesn't exist yet → appended at end of body."""
    from ovp_pipeline.live_concept_fileops import patch_agent_section

    path = tmp_path / "30-Projects" / "Tracking" / "x.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntype: live-concept\nlive:\n  objective: x\n"
        "---\n\n# X\n\n## My take\n\nUser text.\n",
        encoding="utf-8",
    )
    patch_agent_section(path, "Tensions", "- contradiction X vs Y\n")
    text = path.read_text(encoding="utf-8")
    assert "## Tensions" in text
    assert "contradiction X vs Y" in text
    # User section preserved
    assert "User text." in text


def test_patch_agent_section_refuses_my_take(tmp_path):
    """The agent contract: My take is user-owned.  patch_agent_section
    refuses to write there even with a valid agent run."""
    from ovp_pipeline.live_concept_fileops import patch_agent_section

    path = _make_concept_file(tmp_path)
    with pytest.raises(ValueError, match=r"agent-owned allowlist"):
        patch_agent_section(path, "My take", "agent trying to hijack")


def test_patch_agent_section_tolerates_inline_html_comment(tmp_path):
    """Codex P2 regression: BL-063 PR#1 example file documents
    headings annotated with ownership comments like
    ``## Current synthesis  <!-- agent-owned -->``.  The section
    matcher must find these (so agent replaces them) instead of
    treating them as missing and appending a duplicate."""
    from ovp_pipeline.live_concept_fileops import patch_agent_section

    path = tmp_path / "30-Projects" / "Tracking" / "x.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntype: live-concept\nlive:\n  objective: x\n---\n\n"
        "# X\n\n"
        "## My take  <!-- user-owned section; agent never writes here -->\n\n"
        "User text.\n\n"
        "## Current synthesis  <!-- agent-owned -->\n\n"
        "Old synthesis placeholder.\n",
        encoding="utf-8",
    )
    changed = patch_agent_section(
        path, "Current synthesis", "- New bullet from agent\n",
    )
    assert changed is True
    text = path.read_text(encoding="utf-8")
    # Exactly ONE Current synthesis section — not duplicated.
    assert text.count("## Current synthesis") == 1
    assert "Old synthesis placeholder" not in text
    assert "- New bullet from agent" in text
    # User section still untouched.
    assert "User text." in text


def test_patch_agent_section_idempotent_no_change(tmp_path):
    """Writing the same content twice → second call is a no-op."""
    from ovp_pipeline.live_concept_fileops import patch_agent_section

    path = _make_concept_file(tmp_path)
    first = patch_agent_section(path, "Current synthesis", "- Bullet\n")
    second = patch_agent_section(path, "Current synthesis", "- Bullet\n")
    assert first is True
    assert second is False


# ---------------------------------------------------------------------------
# parse_agent_response
# ---------------------------------------------------------------------------


def test_parse_response_happy_path():
    from ovp_pipeline.live_concept_agent import parse_agent_response
    import json

    response = json.dumps({
        "current_synthesis": "- bullet\n",
        "recent_evidence": "- src1\n",
        "tensions": "(none)\n",
        "summary": "Updated synthesis.",
    })
    r = parse_agent_response(response)
    assert r.current_synthesis == "- bullet\n"
    assert r.summary == "Updated synthesis."


def test_parse_response_tolerates_preamble_and_fence():
    """LLM hallucinates conversational filler before/after JSON."""
    from ovp_pipeline.live_concept_agent import parse_agent_response

    raw = (
        "Sure, here's the synthesis:\n\n"
        "```json\n"
        '{"current_synthesis": "x", "summary": "ok"}\n'
        "```\n"
        "Hope this helps!\n"
    )
    r = parse_agent_response(raw)
    assert r.current_synthesis == "x"
    assert r.summary == "ok"


def test_parse_response_missing_keys_default_to_empty():
    from ovp_pipeline.live_concept_agent import parse_agent_response

    r = parse_agent_response('{"summary": "only summary changed"}')
    assert r.current_synthesis == ""
    assert r.recent_evidence == ""
    assert r.tensions == ""
    assert r.summary == "only summary changed"


def test_parse_response_no_json_raises():
    from ovp_pipeline.live_concept_agent import parse_agent_response

    with pytest.raises(ValueError, match=r"no JSON object"):
        parse_agent_response("I cannot help with that.")


# ---------------------------------------------------------------------------
# fire_agent_for_concept
# ---------------------------------------------------------------------------


def _golden_agent_response():
    import json
    return json.dumps({
        "current_synthesis": "- New synthesis bullet from agent.\n",
        "recent_evidence": "- Source A summarised here.\n",
        "tensions": "(no open contradictions in scope)\n",
        "summary": "Refreshed synthesis with 1 new source.",
    })


def test_fire_agent_writes_sections_and_stamps_run_fields(tmp_path):
    from ovp_pipeline.live_concept import parse_live_concept
    from ovp_pipeline.live_concept_agent import fire_agent_for_concept

    path = _make_concept_file(tmp_path)
    handle = parse_live_concept(path)
    assert handle is not None

    llm = MagicMock()
    llm.generate.return_value = _golden_agent_response()

    outcome = fire_agent_for_concept(
        handle,
        llm_client=llm,
        recent_route_decisions=[
            {"payload": {
                "source": "50-Inbox/03-Processed/x.md",
                "update_slugs": ["alpha"],
                "create_titles": [],
                "source_value_summary": "Source about alpha topic.",
            }},
        ],
        open_contradictions=[],
    )

    assert outcome.status == "ok"
    assert outcome.summary.startswith("Refreshed synthesis")
    assert "Current synthesis" in outcome.sections_written
    assert "Recent evidence" in outcome.sections_written
    assert "Tensions" in outcome.sections_written

    # File on disk: agent sections written, My take untouched.
    text = path.read_text(encoding="utf-8")
    assert "New synthesis bullet from agent" in text
    assert "Source A summarised here" in text
    assert "MY personal interpretation.  Agent must not touch this." in text

    # Live frontmatter: lastAttemptAt + lastRunAt + lastRunSummary set,
    # lastRunError cleared.
    refreshed = parse_live_concept(path)
    assert refreshed is not None
    assert refreshed.frontmatter.last_attempt_at != ""
    assert refreshed.frontmatter.last_run_at != ""
    assert refreshed.frontmatter.last_run_summary.startswith("Refreshed")
    assert refreshed.frontmatter.last_run_error == ""


def test_fire_agent_records_request_error_on_llm_exception(tmp_path):
    from ovp_pipeline.live_concept import parse_live_concept
    from ovp_pipeline.live_concept_agent import fire_agent_for_concept

    path = _make_concept_file(tmp_path)
    handle = parse_live_concept(path)
    assert handle is not None

    llm = MagicMock()
    llm.generate.side_effect = RuntimeError("simulated rate limit")

    outcome = fire_agent_for_concept(
        handle, llm_client=llm,
        recent_route_decisions=[], open_contradictions=[],
    )
    assert outcome.status == "request_error"
    assert "simulated rate limit" in outcome.error

    # lastAttemptAt stamped (we tried); lastRunError set.
    refreshed = parse_live_concept(path)
    assert refreshed is not None
    assert refreshed.frontmatter.last_attempt_at != ""
    assert "request_error" in refreshed.frontmatter.last_run_error
    # lastRunAt NOT stamped (the run didn't succeed).
    assert refreshed.frontmatter.last_run_at == ""


def test_fire_agent_records_parse_error_on_garbage_response(tmp_path):
    from ovp_pipeline.live_concept import parse_live_concept
    from ovp_pipeline.live_concept_agent import fire_agent_for_concept

    path = _make_concept_file(tmp_path)
    handle = parse_live_concept(path)
    assert handle is not None

    llm = MagicMock()
    llm.generate.return_value = "I cannot help with that."

    outcome = fire_agent_for_concept(
        handle, llm_client=llm,
        recent_route_decisions=[], open_contradictions=[],
    )
    assert outcome.status == "parse_error"
    refreshed = parse_live_concept(path)
    assert refreshed is not None
    assert "parse_error" in refreshed.frontmatter.last_run_error
