"""Headless inquiry handler + ``ovp-ask`` CLI (M21a / BL-084).

End-to-end glue for one inquiry turn:

1. Resolve the operator's profile from
   :mod:`ovp_pipeline.llm_profiles` (BL-081).
2. Build context with
   :func:`ovp_pipeline.context_binder.build_chat_context` (BL-083).
3. Check the three-tier cost guardrail against the audit-events
   ledger.  Refuse the request with a clear error before the LLM
   call when any cap is exceeded.
4. Call the LLM (non-streaming for v1; BL-086 adds streaming).
5. Append the assistant turn via
   :mod:`ovp_pipeline.chat_fileops` (BL-082) with the manifest
   serialised inline.
6. Emit ``chat_turn_completed`` / ``chat_turn_failed`` /
   ``chat_cap_hit`` events to the audit-events ledger.

The write-back hook (``ovp-ask absorb``) is also here — it writes
``50-Inbox/02-Tasks/ABSORB-chat-<id>-turn-<n>.md`` so existing
absorb / promote / review machinery handles the rest.  Inquiry
transcripts NEVER write directly to ``objects`` / ``claims`` /
``evergreens``; the invariant is "inquiry is artifact, not
knowledge" — only review-gated paths produce knowledge.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ovp_pipeline.chat_fileops import (
    CHATS_DIR,
    ChatAnchor,
    ChatFrontmatter,
    append_turn,
    create_chat_file,
    parse_chat,
)
from ovp_pipeline.context_binder import (
    build_chat_context,
    manifest_to_lines,
)
from ovp_pipeline.event_emitter import emit
from ovp_pipeline.llm_defaults import (
    DEFAULT_LITELLM_TIMEOUT_SECONDS,
    completion_with_litellm_policy,
)
from ovp_pipeline.llm_profiles import (
    ProfileConfig,
    ProfileLimits,
    load_profiles,
    profile_for_use_case,
    resolve_profile,
)
from ovp_pipeline.runtime import resolve_vault_dir

logger = logging.getLogger(__name__)


# Vault-relative location for write-back tasks.  Matches M20's
# task-dispatcher convention (BL-076).
_TASKS_DIR = "50-Inbox/02-Tasks"

# Sanity regex on operator-supplied chat_id.  CodeRabbit Major —
# without this, a chat_id of ``../../../etc/passwd`` would land
# inside the task filename and escape the tasks directory.  All
# chat ids minted by :func:`new_chat_id` match this pattern.
_CHAT_ID_RE = re.compile(r"\Achat-[A-Za-z0-9_.-]+\Z")

# Audit-events vocabulary (also documented in the plan doc).
_EVENT_TURN_COMPLETED = "chat_turn_completed"
_EVENT_TURN_FAILED = "chat_turn_failed"
_EVENT_CAP_HIT = "chat_cap_hit"
_EVENT_WRITEBACK_HANDOFF = "chat_writeback_handoff"


class ChatCapExceeded(RuntimeError):
    """Raised when one of the three cost guardrail caps is hit.

    The handler turns this into an audit event + a friendly error
    message; CLI surfaces it as an exit code, the Reader UI
    (BL-086) renders it inline."""

    def __init__(self, *, cap_kind: str, message: str):
        super().__init__(message)
        self.cap_kind = cap_kind


@dataclass
class ChatTurnResult:
    """Return value of :func:`run_turn` — what the CLI / UI needs."""

    chat_path: Path
    chat_id: str
    frontmatter: ChatFrontmatter
    assistant_body: str
    manifest_lines: list[str]
    input_tokens: int
    output_tokens: int
    profile_name: str
    model: str
    audit_event_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------
# Cost guardrail
# ---------------------------------------------------------------


def _today_utc_iso_prefix() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _read_jsonl_audit(vault_dir: Path) -> list[dict[str, Any]]:
    """Read ``60-Logs/pipeline.jsonl`` line-by-line.

    Tolerates missing / malformed lines so a corrupted log can't
    silently disable the daily cap.  Empty file → empty list →
    today_total = 0 → request proceeds.
    """
    log_path = vault_dir / "60-Logs" / "pipeline.jsonl"
    if not log_path.is_file():
        return []
    events: list[dict[str, Any]] = []
    try:
        for raw_line in log_path.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                events.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
    except OSError as exc:
        logger.warning(
            "chat_handler: failed to read audit log: %s",
            exc,
        )
    return events


def _today_total_tokens(vault_dir: Path, pack: str) -> int:
    """Sum input + output tokens for today's chat events on ``pack``.

    Counts both ``chat_turn_completed`` and ``chat_turn_failed`` —
    failures still cost.  Unindexed sessions still count — privacy
    is about reuse, not cost.
    """
    today_prefix = _today_utc_iso_prefix()
    total = 0
    for event in _read_jsonl_audit(vault_dir):
        event_type = event.get("event_type")
        if event_type not in (_EVENT_TURN_COMPLETED, _EVENT_TURN_FAILED):
            continue
        ts = str(event.get("ts") or "")
        if not ts.startswith(today_prefix):
            continue
        # Pack filter is opt-in: events without a pack field still
        # count toward the global daily cap (no escape via empty
        # pack).  Pack-specific caps will be added when packs land
        # on the inquiry surface.
        event_pack = str(event.get("pack") or "")
        if pack and event_pack and event_pack != pack:
            continue
        try:
            total += int(event.get("input_tokens") or 0)
            total += int(event.get("output_tokens") or 0)
        except (TypeError, ValueError):
            continue
    return total


def check_cost_guardrail(
    vault_dir: Path,
    *,
    estimated_input_tokens: int,
    profile: ProfileConfig,
    limits: ProfileLimits,
    pack: str = "",
) -> None:
    """Enforce the three-tier cap before the LLM call.

    Raises :class:`ChatCapExceeded` with ``cap_kind`` set to the
    specific cap that fired.  Reading from the audit-events ledger
    keeps cap math single-sourced — projection rows (BL-085) are
    derivatives and never consulted here.
    """
    # 1. Per-request input cap.
    if estimated_input_tokens > limits.chat_input_tokens_per_request:
        _emit_cap_hit(
            vault_dir,
            cap_kind="input",
            profile_name=profile.name,
            pack=pack,
            cap_value=limits.chat_input_tokens_per_request,
            today_total=estimated_input_tokens,
        )
        raise ChatCapExceeded(
            cap_kind="input",
            message=(
                f"chat input cap reached "
                f"({estimated_input_tokens} > "
                f"{limits.chat_input_tokens_per_request}); "
                "shorten the message or raise the limit in "
                ".ovp/llm_profiles.yaml"
            ),
        )

    # 2. Per-response output cap.  Honored by passing max_tokens
    #    to the provider; nothing to enforce here.

    # 3. Per-pack daily soft cap, summed from audit events.
    today_total = _today_total_tokens(vault_dir, pack)
    daily_cap = limits.chat_daily_tokens_per_pack
    if today_total + estimated_input_tokens > daily_cap:
        _emit_cap_hit(
            vault_dir,
            cap_kind="daily",
            profile_name=profile.name,
            pack=pack,
            cap_value=daily_cap,
            today_total=today_total,
        )
        raise ChatCapExceeded(
            cap_kind="daily",
            message=(
                f"chat daily token cap reached "
                f"({today_total}/{daily_cap}); "
                "resume tomorrow or raise the limit in "
                ".ovp/llm_profiles.yaml"
            ),
        )


def _emit_cap_hit(
    vault_dir: Path,
    *,
    cap_kind: str,
    profile_name: str,
    pack: str,
    cap_value: int,
    today_total: int,
) -> dict[str, Any]:
    return emit(
        vault_dir,
        "pipeline.jsonl",
        _EVENT_CAP_HIT,
        {
            "cap_kind": cap_kind,
            "profile": profile_name,
            "cap_value": cap_value,
            "today_total": today_total,
        },
        pack=pack or None,
    )


# ---------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------


def _resolve_chat_profile(
    profile_name: str | None,
    vault_dir: Path,
) -> ProfileConfig:
    if profile_name:
        return resolve_profile(profile_name, vault_dir=vault_dir)
    return profile_for_use_case("chat", vault_dir=vault_dir)


def _call_llm(
    profile: ProfileConfig,
    *,
    system_prompt: str,
    user_message: str,
    max_output_tokens: int,
    history: list[dict[str, str]] | None = None,
) -> tuple[str, int, int]:
    """Synchronous LLM call.  Returns (reply, input_tokens, output_tokens).

    ``history`` is prior turns as ``{role, content}`` dicts in
    chronological order — the handler reads them from the
    transcript so follow-up turns see the conversation (codex P2).
    Streaming is deferred to BL-086.
    """
    try:
        import litellm  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "litellm is required for ovp-ask; install with `pip install litellm`"
        ) from exc

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    kwargs: dict[str, Any] = {
        "model": profile.litellm_model,
        "messages": messages,
        "temperature": profile.temperature,
        "max_tokens": max_output_tokens,
        "timeout": DEFAULT_LITELLM_TIMEOUT_SECONDS,
    }
    if profile.api_key:
        kwargs["api_key"] = profile.api_key
    if profile.api_base:
        kwargs["api_base"] = profile.api_base

    response = completion_with_litellm_policy(litellm.completion, kwargs)
    reply = response.choices[0].message.content or ""

    usage = getattr(response, "usage", None) or {}
    if isinstance(usage, dict):
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    else:
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    if not input_tokens:
        history_chars = sum(len(m["content"]) for m in (history or []))
        input_tokens = max(1, (len(system_prompt) + history_chars + len(user_message)) // 4)
    if not output_tokens:
        output_tokens = max(1, len(reply) // 4)
    return reply, input_tokens, output_tokens


def _collect_turn_messages(chat_path: Path) -> list[dict[str, str]]:
    """Parse the transcript into a ``[{role, content}, ...]`` list.

    Walks ``## User · <ts>`` and ``## Assistant · <ts> · turn-N``
    headers (transcript-only — bodies may contain other ``## ...``
    subsections like ``## Next steps``).  Manifest comments and
    interrupted-turn placeholders are stripped.

    Returns oldest-first so the LLM sees the conversation in
    order.  Empty list when there are no prior turns.
    """
    if not chat_path.is_file():
        return []
    text = chat_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    turns: list[dict[str, str]] = []
    current_role: str | None = None
    current_status: str = "ok"
    current_body: list[str] = []
    in_block_comment = False

    def _flush() -> None:
        if current_role is None:
            return
        # Skip interrupted / error turns — they have no useful
        # reply text for the LLM to follow.
        if current_status != "ok":
            return
        body = "\n".join(current_body).strip()
        if body:
            turns.append({"role": current_role, "content": body})

    for raw in lines:
        if _is_user_header(raw):
            _flush()
            current_role = "user"
            current_status = "ok"
            current_body = []
            in_block_comment = False
            continue
        if _is_assistant_header(raw):
            _flush()
            current_role = "assistant"
            current_status = _assistant_status(raw)
            current_body = []
            in_block_comment = False
            continue
        if current_role is None:
            continue
        line = raw
        # Strip inline HTML comment blocks (the manifest snapshot
        # + interrupt marker).  Multi-line comments are tracked
        # across lines; single-line comments are dropped in place
        # rather than dropping the whole line (CodeRabbit M).
        if in_block_comment:
            if "-->" in line:
                line = line.split("-->", 1)[1]
                in_block_comment = False
            else:
                continue
        # Strip every fully-closed <!-- ... --> pair on this line.
        while "<!--" in line and "-->" in line:
            start = line.index("<!--")
            end = line.index("-->", start) + len("-->")
            line = line[:start] + line[end:]
        if "<!--" in line and "-->" not in line:
            # Block comment started — keep the prefix, swallow the rest.
            line = line.split("<!--", 1)[0]
            in_block_comment = True
        current_body.append(line)
    _flush()
    return turns


_USER_HEADER_RE = re.compile(r"^##\s+User\s+·\s+")
_ASSISTANT_HEADER_RE = re.compile(r"^##\s+Assistant\s+·\s+")


def _is_user_header(line: str) -> bool:
    return bool(_USER_HEADER_RE.match(line))


def _is_assistant_header(line: str) -> bool:
    return bool(_ASSISTANT_HEADER_RE.match(line))


def _assistant_status(header: str) -> str:
    """Read the suffix of an Assistant header to detect interrupted / error."""
    if " · interrupted" in header:
        return "interrupted"
    if " · error" in header:
        return "error"
    return "ok"


# ---------------------------------------------------------------
# Public API — run_turn
# ---------------------------------------------------------------


def run_turn(
    vault_dir: Path | str,
    chat_id: str | None = None,
    *,
    user_message: str,
    anchor_kind: str = "standalone",
    anchor_ref: str = "",
    anchor_title: str = "",
    profile_name: str | None = None,
    visibility: str = "indexed",
    pack: str = "",
    chat_path: Path | None = None,
) -> ChatTurnResult:
    """Run one inquiry turn end to end.

    For a brand-new session, pass ``chat_id=None``; the handler
    creates the transcript and returns the assigned id.  For a
    follow-up, pass the existing ``chat_id`` + ``chat_path`` or
    just ``chat_path`` (the handler reads the id from frontmatter).

    Raises :class:`ChatCapExceeded` on cap hits, propagating to
    the CLI / UI.  Emits ``chat_turn_completed`` on success;
    ``chat_turn_failed`` on LLM failure (with counts so the
    daily cap still tracks).
    """
    if not user_message.strip():
        raise ValueError("user_message cannot be empty")

    vault = resolve_vault_dir(vault_dir)
    book = load_profiles(vault)
    limits = book.limits

    # Phase 1 — resolve session + profile + anchor BEFORE the cap
    # check.  CodeRabbit Critical: no orphan transcript can be
    # created when the cap subsequently fires.
    existing_fm: ChatFrontmatter | None = None
    if chat_path is not None or chat_id is not None:
        if chat_path is None:
            chat_path = _find_chat_by_id(vault, chat_id or "")
        if chat_path is None or not chat_path.is_file():
            raise ValueError(f"chat session not found: chat_id={chat_id!r}, chat_path={chat_path}")
        existing_fm = parse_chat(chat_path)
        if existing_fm is None:
            raise ValueError(f"chat_path {chat_path} is not a valid chat transcript")
        # Take anchor + visibility from the existing session.
        anchor_kind = existing_fm.anchor.kind
        anchor_ref = existing_fm.anchor.path
        visibility = existing_fm.visibility

    # Codex P2: a reply with no explicit ``--profile`` reuses the
    # session's original profile so a Deep session doesn't silently
    # downgrade to Balanced on follow-up turns.
    if profile_name is None and existing_fm is not None and existing_fm.profile:
        profile = _resolve_chat_profile(existing_fm.profile, vault)
    else:
        profile = _resolve_chat_profile(profile_name, vault)

    # Phase 2 — collect prior turns for the LLM messages list
    # (codex P2: replies must see the conversation, not just the
    # latest message).  Manifest-stripping happens in the helper.
    history_messages: list[dict[str, str]] = []
    if existing_fm is not None and chat_path is not None:
        history_messages = _collect_turn_messages(chat_path)

    # Phase 3 — build context.
    system_prompt, manifest = build_chat_context(
        vault,
        anchor_kind=anchor_kind,
        anchor_ref=anchor_ref,
        user_message=user_message,
        profile_input_cap=limits.chat_input_tokens_per_request,
    )

    # Phase 4 — cost guardrail.  Includes the expected output
    # tokens for this turn (CodeRabbit M) so a request whose reply
    # would push us past the daily cap is refused up front.  No
    # files have been written yet — refusal is clean.
    estimated_input = manifest.token_estimate_total + len(user_message) // 4
    estimated_total = estimated_input + limits.chat_output_tokens_per_request
    check_cost_guardrail(
        vault,
        estimated_input_tokens=estimated_total,
        profile=profile,
        limits=limits,
        pack=pack,
    )

    # Phase 5 — now safe to create the new transcript (or use
    # the existing one).
    if existing_fm is None:
        anchor = ChatAnchor(
            kind=anchor_kind,
            path=anchor_ref,
            title=anchor_title,
        )
        chat_path, fm = create_chat_file(
            vault,
            anchor=anchor,
            profile=profile.name,
            model=profile.litellm_model,
            temperature=profile.temperature,
            visibility=visibility,
            topic=user_message,
        )
    else:
        fm = existing_fm

    # Append the user turn before the LLM call so the operator's
    # message is preserved even if the LLM call later errors out.
    fm = append_turn(
        chat_path,
        role="user",
        body=user_message,
    )

    # LLM call — pass prior turns + the new user message together.
    audit_event_ids: list[str] = []
    try:
        reply, input_tokens, output_tokens = _call_llm(
            profile,
            system_prompt=system_prompt,
            history=history_messages,
            user_message=user_message,
            max_output_tokens=limits.chat_output_tokens_per_request,
        )
    except Exception as exc:
        # Failure still costs — emit chat_turn_failed with the
        # estimated input tokens so the daily cap still tracks.
        estimated_input = manifest.token_estimate_total + len(user_message) // 4
        event = emit(
            vault,
            "pipeline.jsonl",
            _EVENT_TURN_FAILED,
            {
                "chat_id": fm.chat_id,
                "turn": fm.turn_count,
                "profile": profile.name,
                "visibility": visibility,
                "anchor_kind": anchor_kind,
                "input_tokens": estimated_input,
                "output_tokens": 0,
                "error_class": exc.__class__.__name__,
            },
            pack=pack or None,
        )
        audit_event_ids.append(event["event_id"])
        raise

    # Append the assistant turn with the manifest.
    lines = manifest_to_lines(manifest)
    new_turn_number = fm.turn_count + 1  # next turn after the user turn
    fm = append_turn(
        chat_path,
        role="assistant",
        body=reply,
        turn_number=new_turn_number,
        manifest_lines=lines,
    )

    # Audit success.  Body is *not* recorded for unindexed sessions
    # — counts + metadata only.  For indexed sessions we also keep
    # only metadata (the body is on disk in the markdown anyway).
    completion_payload: dict[str, Any] = {
        "chat_id": fm.chat_id,
        "turn": fm.turn_count,
        "profile": profile.name,
        "visibility": visibility,
        "anchor_kind": anchor_kind,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    event = emit(
        vault,
        "pipeline.jsonl",
        _EVENT_TURN_COMPLETED,
        completion_payload,
        pack=pack or None,
    )
    audit_event_ids.append(event["event_id"])

    return ChatTurnResult(
        chat_path=chat_path,
        chat_id=fm.chat_id,
        frontmatter=fm,
        assistant_body=reply,
        manifest_lines=lines,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        profile_name=profile.name,
        model=profile.litellm_model,
        audit_event_ids=audit_event_ids,
    )


def _find_chat_by_id(vault: Path, chat_id: str) -> Path | None:
    """Locate a chat transcript by chat_id.

    Sweeps ``40-Resources/Chats/**/*.md`` and parses frontmatter.
    Linear scan is fine for the volumes M21a will see; BL-085's
    projection will make this an indexed lookup.
    """
    if not chat_id:
        return None
    chats_dir = vault / CHATS_DIR
    if not chats_dir.is_dir():
        return None
    for path in chats_dir.rglob("*.md"):
        fm = parse_chat(path)
        if fm and fm.chat_id == chat_id:
            return path
    return None


# ---------------------------------------------------------------
# Write-back hook (BL-084b)
# ---------------------------------------------------------------


def writeback_to_absorb_queue(
    vault_dir: Path | str,
    *,
    chat_id: str,
    turn_number: int,
    chat_path: Path | None = None,
    pack: str = "",
) -> Path:
    """Emit an ABSORB-chat task into the existing absorb queue.

    Writes ``50-Inbox/02-Tasks/ABSORB-chat-<id>-turn-<n>.md`` with
    the assistant body of turn ``turn_number`` as content, plus
    frontmatter pointing back at the originating chat.  Emits the
    ``chat_writeback_handoff`` audit event.

    Invariant: this is the **only** path from inquiry into
    knowledge state, and it routes through the same review-gated
    pipeline operator-written notes use.  Nothing in M21 writes
    to ``objects`` / ``claims`` / ``evergreens`` directly.
    """
    if turn_number < 1:
        raise ValueError(f"turn_number must be >= 1, got {turn_number}")
    # CodeRabbit Major — refuse a chat_id that doesn't match the
    # ``chat-<alnum>`` shape minted by new_chat_id.  Stops
    # operator-supplied ``../../etc/passwd`` from escaping the
    # tasks directory via the filename interpolation below.
    if not _CHAT_ID_RE.match(chat_id):
        raise ValueError(f"chat_id {chat_id!r} is not a valid chat identifier")
    vault = resolve_vault_dir(vault_dir)
    if chat_path is None:
        chat_path = _find_chat_by_id(vault, chat_id)
    if chat_path is None or not chat_path.is_file():
        raise ValueError(f"chat session not found: chat_id={chat_id!r}")

    assistant_body = _extract_assistant_turn(chat_path, turn_number)
    if not assistant_body:
        raise ValueError(f"assistant turn {turn_number} not found in {chat_path}")

    tasks_dir = vault / _TASKS_DIR
    tasks_dir.mkdir(parents=True, exist_ok=True)
    task_path = tasks_dir / f"ABSORB-chat-{chat_id}-turn-{turn_number}.md"
    when = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    fm_yaml = (
        "---\n"
        "type: task\n"
        "subtype: absorb-chat\n"
        f"chat_id: {chat_id}\n"
        f"chat_turn: {turn_number}\n"
        f"source_chat_path: {chat_path.relative_to(vault)}\n"
        f"created_at: {when}\n"
        "---\n\n"
    )
    body = (
        f"# Captured from inquiry {chat_id} turn {turn_number}\n\n" f"{assistant_body.rstrip()}\n"
    )
    task_path.write_text(fm_yaml + body, encoding="utf-8")

    emit(
        vault,
        "pipeline.jsonl",
        _EVENT_WRITEBACK_HANDOFF,
        {
            "chat_id": chat_id,
            "turn": turn_number,
            "task_path": str(task_path.relative_to(vault)),
        },
        pack=pack or None,
    )
    return task_path


def _extract_assistant_turn(chat_path: Path, turn_number: int) -> str:
    """Return the body of the Nth assistant turn from ``chat_path``.

    Codex P2: stop only on real transcript headers (``## User ·``
    or ``## Assistant ·``), not every H2 — assistant prose often
    contains ``## Next steps`` / ``## Pros & cons`` style
    subsections.  HTML comments are stripped in place so a single-
    line comment doesn't drop the surrounding prose.

    Returns ``""`` when the turn isn't found.
    """
    if not chat_path.is_file():
        return ""
    text = chat_path.read_text(encoding="utf-8")
    suffix_marker = f"turn-{turn_number}"
    lines = text.splitlines()

    in_target = False
    collected: list[str] = []
    in_block_comment = False
    for raw in lines:
        if _is_user_header(raw):
            if in_target:
                break
            continue
        if _is_assistant_header(raw):
            if in_target:
                # Next assistant turn — stop.
                break
            if suffix_marker in raw:
                in_target = True
            continue
        if not in_target:
            continue
        line = raw
        if in_block_comment:
            if "-->" in line:
                line = line.split("-->", 1)[1]
                in_block_comment = False
            else:
                continue
        while "<!--" in line and "-->" in line:
            start = line.index("<!--")
            end = line.index("-->", start) + len("-->")
            line = line[:start] + line[end:]
        if "<!--" in line and "-->" not in line:
            line = line.split("<!--", 1)[0]
            in_block_comment = True
        collected.append(line)
    return "\n".join(collected).strip()


# ---------------------------------------------------------------
# CLI — ovp-ask
# ---------------------------------------------------------------


def _parse_anchor(anchor: str) -> tuple[str, str]:
    """Split ``kind:path`` into a tuple.  Defaults to ``standalone``."""
    if not anchor:
        return "standalone", ""
    if ":" not in anchor:
        return "note", anchor
    kind, _, ref = anchor.partition(":")
    return kind.strip() or "standalone", ref.strip()


def _cmd_new(args: argparse.Namespace) -> int:
    vault = Path(args.vault_dir) if args.vault_dir else None
    anchor_kind, anchor_ref = _parse_anchor(args.anchor)
    try:
        result = run_turn(
            vault or os.getcwd(),
            user_message=args.message,
            anchor_kind=anchor_kind,
            anchor_ref=anchor_ref,
            profile_name=args.profile,
            visibility=args.visibility,
            pack=args.pack or "",
        )
    except ChatCapExceeded as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"chat_id: {result.chat_id}")
    print(f"path: {result.chat_path}")
    print(f"profile: {result.profile_name}  model: {result.model}")
    print(f"tokens: in={result.input_tokens} out={result.output_tokens}")
    print()
    print(result.assistant_body)
    return 0


def _cmd_reply(args: argparse.Namespace) -> int:
    vault = Path(args.vault_dir) if args.vault_dir else None
    try:
        result = run_turn(
            vault or os.getcwd(),
            chat_id=args.id,
            user_message=args.message,
            profile_name=args.profile,
            pack=args.pack or "",
        )
    except ChatCapExceeded as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"chat_id: {result.chat_id}")
    print(f"tokens: in={result.input_tokens} out={result.output_tokens}")
    print()
    print(result.assistant_body)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    vault = resolve_vault_dir(args.vault_dir or os.getcwd())
    chats_dir = vault / CHATS_DIR
    if not chats_dir.is_dir():
        print("(no chats yet)")
        return 0
    rows: list[tuple[str, str, str, int, Path]] = []
    for path in sorted(chats_dir.rglob("*.md")):
        fm = parse_chat(path)
        if fm is None:
            continue
        if args.status and fm.status != args.status:
            continue
        rows.append((fm.chat_id, fm.last_message_at, fm.status, fm.turn_count, path))
    rows.sort(key=lambda r: r[1], reverse=True)
    for chat_id, last, status, turns, path in rows:
        rel = path.relative_to(vault)
        print(f"{chat_id}  {last}  {status:<8}  turns={turns}  {rel}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    vault = resolve_vault_dir(args.vault_dir or os.getcwd())
    path = _find_chat_by_id(vault, args.id)
    if path is None:
        print(f"error: chat {args.id!r} not found", file=sys.stderr)
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


def _cmd_absorb(args: argparse.Namespace) -> int:
    vault = Path(args.vault_dir) if args.vault_dir else Path(os.getcwd())
    try:
        task_path = writeback_to_absorb_queue(
            vault,
            chat_id=args.id,
            turn_number=args.turn,
            pack=args.pack or "",
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote: {task_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ovp-ask",
        description="Anchored inquiry CLI for OVP (M21a).",
    )
    parser.add_argument(
        "--vault-dir",
        help="Vault root (default: OVP_VAULT_DIR env var or cwd)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_new = sub.add_parser("new", help="Start a new inquiry session.")
    p_new.add_argument("--anchor", default="", help="<kind>:<ref> or just <path>")
    p_new.add_argument(
        "--profile", default=None, help="Profile name (Fast/Balanced/Deep or custom)"
    )
    p_new.add_argument("--visibility", default="indexed", choices=["indexed", "unindexed"])
    p_new.add_argument("--pack", default="")
    p_new.add_argument("--message", required=True)
    p_new.set_defaults(func=_cmd_new)

    p_reply = sub.add_parser("reply", help="Continue an existing session.")
    p_reply.add_argument("--id", required=True, help="chat_id (e.g. chat-a7b3)")
    p_reply.add_argument("--profile", default=None)
    p_reply.add_argument("--pack", default="")
    p_reply.add_argument("--message", required=True)
    p_reply.set_defaults(func=_cmd_reply)

    p_list = sub.add_parser("list", help="List inquiry sessions.")
    p_list.add_argument("--status", default=None, choices=["active", "pinned", "archived"])
    p_list.set_defaults(func=_cmd_list)

    p_show = sub.add_parser("show", help="Print one inquiry transcript.")
    p_show.add_argument("--id", required=True)
    p_show.set_defaults(func=_cmd_show)

    p_absorb = sub.add_parser(
        "absorb",
        help="Hand a turn's assistant body to the absorb queue.",
    )
    p_absorb.add_argument("--id", required=True)
    p_absorb.add_argument("--turn", type=int, required=True)
    p_absorb.add_argument("--pack", default="")
    p_absorb.set_defaults(func=_cmd_absorb)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
