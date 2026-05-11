"""BL-063 PR#3: Live Concept agent — runs the LLM synthesis pass.

Closes M19's main item: when a trigger fires for a Live Concept,
the agent rewrites the three agent-owned sections
(``## Current synthesis`` / ``## Recent evidence`` / ``## Tensions``)
based on the latest in-scope evidence + open contradictions.

Architecture (post-BL-067 single-writer pattern at section
granularity):

* **Pure synthesis** lives here.  Loads the prompt, builds the
  user context from the :class:`ConceptEvaluation` produced by
  PR#2's scheduler, calls the LLM, parses the JSON response into
  an :class:`AgentResult`.  No file I/O; no DB writes.
* **Patch application** is delegated to
  :mod:`live_concept_fileops`.  ``apply_agent_result`` calls
  :func:`patch_agent_section` for each section delta and
  :func:`patch_live` to advance the runtime fields
  (``lastAttemptAt`` / ``lastRunAt`` / ``lastRunSummary`` /
  ``lastRunError``).  Section + frontmatter writes are both
  single-writer via that module.
* **Best-effort** failure handling.  An LLM call that returns
  parse-error / empty response / network error sets
  ``lastRunError`` via patch_live and leaves the body untouched —
  the next scheduler pass retries.

What the agent does NOT do (preserved from PR#1 single-writer
contract):

* Never writes ``## My take`` — that's the user's section.
* Never modifies the ``live:`` frontmatter directly except via
  ``patch_live``.
* Never adds slugs to ``scope_evergreens`` — the operator decides
  scope.
* Never promotes / merges / rejects evergreens — that's the absorb
  + review path.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .live_concept import LiveConceptHandle
from .live_concept_fileops import (
    AGENT_OWNED_SECTIONS,
    patch_agent_section,
    patch_live,
)

logger = logging.getLogger(__name__)

LIVE_CONCEPT_AGENT_PROMPT = "live_concept_synthesize"
LIVE_CONCEPT_AGENT_VERSION = "v1"
LIVE_CONCEPT_AGENT_EVENT = "live_concept_agent_run"

# Cap how much we send the LLM per source — the agent works from
# slugs + titles + short rationales, not full bodies.  500 chars
# per `source_value_summary` is enough for the agent to know what
# the source was about without ballooning the prompt.
_MAX_ROUTE_SUMMARY_CHARS = 500
_MAX_OUTPUT_TOKENS = 3000


@dataclass(frozen=True)
class AgentResult:
    """Parsed output of the LLM synthesis pass.

    Empty strings indicate "no change for this section" — the
    apply step skips section writes when the field is empty.
    """

    current_synthesis: str = ""
    recent_evidence: str = ""
    tensions: str = ""
    summary: str = ""


@dataclass
class AgentRunOutcome:
    """Result of one full agent run (compose + LLM + apply).

    Carries enough info for audit emission + the CLI's text report
    of "what just happened".  ``status`` is one of:

    * ``"ok"`` — LLM call succeeded, sections written
    * ``"parse_error"`` — got LLM response, couldn't parse JSON
    * ``"request_error"`` — LLM call raised before producing output
    * ``"skip"`` — nothing to do (no triggers fired, or LLM
      returned an empty result)
    """

    handle: LiveConceptHandle
    status: str
    summary: str = ""
    error: str = ""
    sections_written: list[str] = field(default_factory=list)


def _truncate(text: str, max_chars: int) -> str:
    """Inline truncation — duplicates the one in absorb_router but
    keeps this module dependency-free of that one."""
    if not text:
        return ""
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1] + "…"


def _read_body_sections(path: Path) -> dict[str, str]:
    """Extract current content of agent-owned sections from the live
    concept file.  Used by ``build_agent_prompt`` so the LLM can see
    "what's there now" and produce an evolution-aware delta instead
    of rewriting everything from scratch each run.

    Returns dict mapping section title → markdown body (without
    the heading).  Missing sections map to empty strings.
    """
    out = {name: "" for name in AGENT_OWNED_SECTIONS}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    # Cheap H2 split — same convention as live_concept_fileops.
    for name in AGENT_OWNED_SECTIONS:
        pattern = re.compile(
            r"^## " + re.escape(name) + r"\s*$(.*?)(?=^## |^# |\Z)",
            re.MULTILINE | re.DOTALL,
        )
        match = pattern.search(text)
        if match:
            out[name] = match.group(1).strip()
    return out


def build_agent_user_prompt(
    handle: LiveConceptHandle,
    *,
    recent_route_decisions: list[dict[str, Any]],
    open_contradictions: list[dict[str, Any]],
    existing_sections: dict[str, str] | None = None,
) -> str:
    """Render the LLM user prompt from the evaluation context.

    ``recent_route_decisions`` is the same list of
    ``absorb_route_decision`` audit rows BL-063 PR#2's scheduler
    already produces — caller (PR#3 wiring) passes the same data
    to both the trigger evaluator and the agent so they can't
    drift.
    """
    fm = handle.frontmatter
    scope_block = (
        "\n".join(f"  - {slug}" for slug in fm.scope_evergreens)
        if fm.scope_evergreens
        else "  (no scope_evergreens declared)"
    )

    # Compact the recent route decisions to slug-level info; the
    # agent doesn't need the full source body.
    decision_lines: list[str] = []
    for row in recent_route_decisions[:20]:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        source = str(payload.get("source", "") or row.get("source_log", ""))
        update_slugs = payload.get("update_slugs") or []
        create_titles = payload.get("create_titles") or []
        summary_text = _truncate(
            str(payload.get("source_value_summary", "")),
            _MAX_ROUTE_SUMMARY_CHARS,
        )
        decision_lines.append(
            f"  - source: {source}\n"
            f"    updates: {update_slugs}\n"
            f"    creates: {create_titles}\n"
            f"    summary: {summary_text}"
        )
    decisions_block = "\n".join(decision_lines) or "  (no recent route decisions in window)"

    contradiction_lines: list[str] = []
    for c in open_contradictions[:10]:
        contradiction_lines.append(
            f"  - id: {c.get('contradiction_id', '')}\n"
            f"    subject: {c.get('subject_key', '')}\n"
            f"    positive_claims: {c.get('positive_claim_ids', [])}\n"
            f"    negative_claims: {c.get('negative_claim_ids', [])}"
        )
    contradictions_block = "\n".join(contradiction_lines) or "  (no open contradictions on in-scope evergreens)"

    existing = existing_sections or {}
    existing_block = "\n".join(
        f"### {name}:\n{existing.get(name, '').strip() or '(empty)'}"
        for name in AGENT_OWNED_SECTIONS
    )

    return (
        f"Concept slug: {handle.slug}\n"
        f"Concept relative_path: {handle.relative_path}\n\n"
        f"objective:\n  {fm.objective}\n\n"
        f"scope_evergreens:\n{scope_block}\n\n"
        f"recent_route_decisions (last evaluation window):\n{decisions_block}\n\n"
        f"open_contradictions (in scope):\n{contradictions_block}\n\n"
        f"existing_sections (the file's current agent-owned content):\n{existing_block}\n\n"
        "请按 system prompt 中描述的 JSON schema 输出.记住:不写 `## My take`,不改 frontmatter."
    )


def parse_agent_response(response_text: str) -> AgentResult:
    """Parse a JSON-encoded response from the synthesis LLM.

    Tolerates the same cosmetic LLM quirks the BL-062 router does
    (conversational preamble, markdown fence) by extracting the
    first balanced ``{...}`` block.  Raises :class:`ValueError`
    when no JSON object can be found at all.

    Missing keys default to empty strings — partial output is
    valid (an LLM that updates only the synthesis section without
    touching tensions is a fine outcome).
    """
    if not response_text:
        raise ValueError("empty response from agent")
    match = re.search(r"\{.*\}", response_text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object found in agent response")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(f"agent response is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"agent response root is {type(data).__name__}, not dict")
    return AgentResult(
        current_synthesis=str(data.get("current_synthesis", "") or ""),
        recent_evidence=str(data.get("recent_evidence", "") or ""),
        tensions=str(data.get("tensions", "") or ""),
        summary=str(data.get("summary", "") or ""),
    )


def synthesize_live_concept(
    handle: LiveConceptHandle,
    *,
    llm_client: Any,
    recent_route_decisions: list[dict[str, Any]],
    open_contradictions: list[dict[str, Any]],
    max_output_tokens: int = _MAX_OUTPUT_TOKENS,
) -> AgentResult:
    """Call the LLM with the BL-063 PR#3 synthesis prompt and
    return parsed deltas.

    ``llm_client`` is duck-typed to the same shape the BL-062
    router expects: any object with ``generate(system_prompt,
    user_prompt, max_tokens=...) -> str``.  This lets the agent
    reuse the existing :class:`LiteLLMClient` machinery + the
    pluggable router-LLM config (BL-068).

    Raises :class:`ValueError` on parse failure; raises whatever
    the LLM client raises on request failure.  Caller wraps both
    via ``apply_agent_result`` to translate into ``lastRunError``.
    """
    from .prompt_registry import get_prompt

    prompt = get_prompt(LIVE_CONCEPT_AGENT_PROMPT, LIVE_CONCEPT_AGENT_VERSION)
    existing_sections = _read_body_sections(handle.path)
    user_prompt = build_agent_user_prompt(
        handle,
        recent_route_decisions=recent_route_decisions,
        open_contradictions=open_contradictions,
        existing_sections=existing_sections,
    )
    response = llm_client.generate(
        system_prompt=prompt.body,
        user_prompt=user_prompt,
        max_tokens=max_output_tokens,
    )
    return parse_agent_response(response)


def apply_agent_result(
    handle: LiveConceptHandle,
    result: AgentResult,
    *,
    pipeline_logger: Any | None = None,
) -> AgentRunOutcome:
    """Write the agent's section deltas + advance lastRunAt /
    lastRunSummary on the live: frontmatter.

    Section writes use :func:`patch_agent_section` which refuses
    to touch anything outside :data:`AGENT_OWNED_SECTIONS`, so a
    bug in the LLM response (e.g. ``current_synthesis`` field
    carrying frontmatter-like content) can't corrupt the file.

    Best-effort: a write failure on one section doesn't abort the
    other writes.  Frontmatter timestamps are advanced in a single
    ``patch_live`` call at the end.
    """
    sections_written: list[str] = []
    section_field_map = {
        "Current synthesis": result.current_synthesis,
        "Recent evidence": result.recent_evidence,
        "Tensions": result.tensions,
    }
    for section_title, new_content in section_field_map.items():
        if not new_content.strip():
            continue
        try:
            changed = patch_agent_section(
                handle.path, section_title, new_content,
            )
            if changed:
                sections_written.append(section_title)
        except Exception as exc:  # noqa: BLE001 — best-effort per section
            logger.warning(
                "BL-063 PR#3: patch_agent_section failed for %s: %s",
                section_title, exc,
            )
            if pipeline_logger is not None:
                try:
                    pipeline_logger.log(
                        "live_concept_section_write_error",
                        {
                            "slug": handle.slug,
                            "section": section_title,
                            "error": str(exc),
                        },
                    )
                except Exception:  # noqa: BLE001 — audit best-effort
                    pass

    # Advance the runtime fields on the live: block.  lastAttemptAt
    # was set by the caller BEFORE we entered the agent call; here
    # we set lastRunAt + lastRunSummary on success.
    now_iso = (
        datetime.now(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    try:
        patch_live(
            handle.path,
            last_run_at=now_iso,
            last_run_summary=result.summary or "",
            last_run_error="",
        )
    except Exception as exc:  # noqa: BLE001 — best-effort runtime field update
        logger.warning(
            "BL-063 PR#3: patch_live runtime-field update failed for %s: %s",
            handle.slug, exc,
        )

    return AgentRunOutcome(
        handle=handle,
        status="ok",
        summary=result.summary,
        sections_written=sections_written,
    )


def fire_agent_for_concept(
    handle: LiveConceptHandle,
    *,
    llm_client: Any,
    recent_route_decisions: list[dict[str, Any]],
    open_contradictions: list[dict[str, Any]],
    pipeline_logger: Any | None = None,
    max_output_tokens: int = _MAX_OUTPUT_TOKENS,
) -> AgentRunOutcome:
    """End-to-end "trigger fire" for one Live Concept: stamp
    lastAttemptAt → call agent → apply sections → stamp lastRunAt
    (or lastRunError on failure).

    Best-effort: every exception is caught and translated to a
    ``status='request_error'`` / ``status='parse_error'`` outcome
    with ``lastRunError`` set on the frontmatter.  The scheduler
    can call this in a loop without one bad concept aborting the
    whole batch.
    """
    now_iso = (
        datetime.now(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    # Stamp lastAttemptAt BEFORE the LLM call so a hard-crash
    # leaves an audit trail (backoff anchor — same two-timestamp
    # split BL-061 design documented).
    try:
        patch_live(handle.path, last_attempt_at=now_iso)
    except Exception as exc:  # noqa: BLE001 — non-fatal; continue with agent
        logger.warning(
            "BL-063 PR#3: lastAttemptAt stamp failed for %s: %s",
            handle.slug, exc,
        )

    try:
        result = synthesize_live_concept(
            handle,
            llm_client=llm_client,
            recent_route_decisions=recent_route_decisions,
            open_contradictions=open_contradictions,
            max_output_tokens=max_output_tokens,
        )
    except ValueError as exc:
        # Parse error path — got an LLM response but couldn't decode.
        _record_run_error(handle, status="parse_error", error=str(exc))
        if pipeline_logger is not None:
            try:
                pipeline_logger.log(LIVE_CONCEPT_AGENT_EVENT, {
                    "slug": handle.slug,
                    "status": "parse_error",
                    "error": str(exc),
                })
            except Exception:  # noqa: BLE001
                pass
        return AgentRunOutcome(handle=handle, status="parse_error", error=str(exc))
    except Exception as exc:  # noqa: BLE001 — LLM call boundary
        _record_run_error(handle, status="request_error", error=str(exc))
        if pipeline_logger is not None:
            try:
                pipeline_logger.log(LIVE_CONCEPT_AGENT_EVENT, {
                    "slug": handle.slug,
                    "status": "request_error",
                    "error": str(exc),
                })
            except Exception:  # noqa: BLE001
                pass
        return AgentRunOutcome(handle=handle, status="request_error", error=str(exc))

    outcome = apply_agent_result(handle, result, pipeline_logger=pipeline_logger)
    if pipeline_logger is not None:
        try:
            pipeline_logger.log(LIVE_CONCEPT_AGENT_EVENT, {
                "slug": handle.slug,
                "status": "ok",
                "summary": outcome.summary,
                "sections_written": outcome.sections_written,
                "prompt_name": LIVE_CONCEPT_AGENT_PROMPT,
                "prompt_version": LIVE_CONCEPT_AGENT_VERSION,
            })
        except Exception:  # noqa: BLE001
            pass
    return outcome


def _record_run_error(handle: LiveConceptHandle, *, status: str, error: str) -> None:
    """Stamp ``last_run_error`` on the live: frontmatter so a failed
    run surfaces in the file list and ``ovp-live-concept-scan``
    output.  Best-effort — a failure to stamp the error is logged
    but doesn't escalate."""
    error_text = f"[{status}] {error}"[:1000]  # cap so the YAML stays readable
    try:
        patch_live(handle.path, last_run_error=error_text)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "BL-063 PR#3: lastRunError stamp failed for %s: %s",
            handle.slug, exc,
        )
