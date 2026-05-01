from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date as _date_cls, datetime, timezone
from pathlib import Path
from typing import Any

from ..packs.loader import DEFAULT_WORKFLOW_PACK_NAME
from ..projection_labels import frontmatter_projection_fields
from ..reuse_emitter import emit_reuse_events
from ..runtime import resolve_vault_dir, split_markdown_frontmatter
from .working_memory import DEFAULT_CONTEXT_BUDGET_TOKENS, DEFAULT_TOP_N, build_working_memory


SESSION_SNAPSHOT_DIR = ("60-Logs", "session-snapshots")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_session_id(now: datetime) -> str:
    return f"session-{now.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def _safe_session_id(value: str, *, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return cleaned.strip("-") or fallback


def _relative_path(path: Path, vault_dir: Path) -> str:
    return str(path.relative_to(vault_dir))


def _selected_top_of_mind_slugs(body: str) -> list[str]:
    slugs: list[str] = []
    in_top_of_mind = False
    for line in body.splitlines():
        if line == "## Top of Mind":
            in_top_of_mind = True
            continue
        if in_top_of_mind and line.startswith("## "):
            break
        if not in_top_of_mind:
            continue
        match = re.match(r"^- \[\[([^\]]+)\]\]", line)
        if match:
            slugs.append(match.group(1))
    return slugs


def _render_prime(
    *,
    session_id: str,
    generated_at: datetime,
    source_context_pack: str,
    working_memory_body: str,
    metadata: dict[str, Any],
) -> str:
    budget_metadata = _context_budget_metadata(metadata)
    return "\n".join(
        [
            f"# OVP Prime — {session_id}",
            "",
            "## Start Here",
            "",
            f"- Source context pack: `{source_context_pack}`",
            f"- Generated at: {_format_dt(generated_at)}",
            f"- Budget: {budget_metadata['context_budget_tokens']} tokens",
            f"- Selected: {budget_metadata['context_selected_tokens']} tokens",
            f"- Selected objects: {budget_metadata['context_selected_objects']}",
            f"- Omitted by budget: {budget_metadata['context_omitted_objects']}",
            "- Boundary: this is a projection for session start, not Authority.",
            "",
            "## Working Memory Context",
            "",
            working_memory_body.rstrip(),
            "",
        ]
    )


def _context_budget_metadata(metadata: dict[str, Any]) -> dict[str, int]:
    return {
        "context_budget_tokens": int(metadata.get("context_budget_tokens") or 0),
        "context_selected_tokens": int(metadata.get("context_selected_tokens") or 0),
        "context_selected_objects": int(metadata.get("context_selected_objects") or 0),
        "context_omitted_objects": int(metadata.get("context_omitted_objects") or 0),
    }


def build_prime_context(
    vault_dir: Path | str,
    *,
    session_id: str | None = None,
    target_date: _date_cls | None = None,
    context_budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
    top_n: int = DEFAULT_TOP_N,
    now: datetime | None = None,
) -> Path:
    resolved_vault = resolve_vault_dir(vault_dir)
    current_time = now or _utc_now()
    session_default = _default_session_id(current_time)
    session = _safe_session_id(session_id or session_default, fallback=session_default)
    target_date = target_date or current_time.date()

    working_memory_path = build_working_memory(
        resolved_vault,
        target_date=target_date,
        top_n=top_n,
        context_budget_tokens=context_budget_tokens,
        now=current_time,
    )
    working_memory_text = working_memory_path.read_text(encoding="utf-8")
    metadata, working_memory_body = split_markdown_frontmatter(working_memory_text)
    budget_metadata = _context_budget_metadata(metadata)
    source_context_pack = _relative_path(working_memory_path, resolved_vault)

    output_dir = resolved_vault.joinpath(*SESSION_SNAPSHOT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{session}.md"
    latest_path = output_dir / "latest.md"

    frontmatter = (
        "---\n"
        "type: session_snapshot\n"
        f"session_id: {session}\n"
        f"generated_at: {_format_dt(current_time)}\n"
        f"date: {target_date.isoformat()}\n"
        f"source_context_pack: {source_context_pack}\n"
        f"context_budget_tokens: {budget_metadata['context_budget_tokens']}\n"
        f"context_selected_tokens: {budget_metadata['context_selected_tokens']}\n"
        f"context_selected_objects: {budget_metadata['context_selected_objects']}\n"
        f"context_omitted_objects: {budget_metadata['context_omitted_objects']}\n"
        + "\n".join(
            frontmatter_projection_fields(
                surface="ovp_prime",
                projection_kind="context_pack_projection",
                owner_pack="research-tech",
                generated_by="build_prime_context",
                derived_from=(source_context_pack, "reuse-events.jsonl"),
                rebuild_policy="on_session_start",
            )
        )
        + "\n---\n\n"
    )
    body = _render_prime(
        session_id=session,
        generated_at=current_time,
        source_context_pack=source_context_pack,
        working_memory_body=working_memory_body,
        metadata=metadata,
    )
    rendered = frontmatter + body
    output_path.write_text(rendered, encoding="utf-8")
    latest_path.write_text(rendered, encoding="utf-8")

    selected_slugs = _selected_top_of_mind_slugs(working_memory_body)
    if selected_slugs:
        emit_reuse_events(
            resolved_vault,
            pack=DEFAULT_WORKFLOW_PACK_NAME,
            slugs=selected_slugs,
            surface="ovp_prime",
            consumer_ref=_relative_path(output_path, resolved_vault),
            session_id=session,
            extra_payload={
                "source_context_pack": source_context_pack,
                "context_budget_tokens": budget_metadata["context_budget_tokens"],
                "context_selected_tokens": budget_metadata["context_selected_tokens"],
            },
        )
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ovp-prime",
        description="Write an OVP Prime session snapshot from the budgeted working-memory context pack.",
    )
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault root (default: cwd)")
    parser.add_argument("--session-id", default=None, help="Stable session id for the snapshot")
    parser.add_argument("--date", default=None, help="Context date in YYYY-MM-DD (default: today UTC)")
    parser.add_argument(
        "--budget-tokens",
        type=int,
        default=DEFAULT_CONTEXT_BUDGET_TOKENS,
        help=f"Approximate context budget for selected objects (default: {DEFAULT_CONTEXT_BUDGET_TOKENS})",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=DEFAULT_TOP_N,
        help=f"Top-N working-memory candidates before budget selection (default: {DEFAULT_TOP_N})",
    )
    parser.add_argument("--json", action="store_true", help="Print structured summary to stdout.")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    target_date = _date_cls.fromisoformat(args.date) if args.date else None
    output_path = build_prime_context(
        vault_dir,
        session_id=args.session_id,
        target_date=target_date,
        context_budget_tokens=args.budget_tokens,
        top_n=args.top_n,
    )
    latest_path = vault_dir.joinpath(*SESSION_SNAPSHOT_DIR) / "latest.md"
    metadata, _body = split_markdown_frontmatter(output_path.read_text(encoding="utf-8"))
    summary = {
        "session_id": metadata.get("session_id"),
        "path": str(output_path),
        "latest_path": str(latest_path),
        "source_context_pack": str(vault_dir / str(metadata.get("source_context_pack") or "")),
        "context_budget_tokens": int(metadata.get("context_budget_tokens") or 0),
        "context_selected_tokens": int(metadata.get("context_selected_tokens") or 0),
        "context_selected_objects": int(metadata.get("context_selected_objects") or 0),
        "context_omitted_objects": int(metadata.get("context_omitted_objects") or 0),
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    print("=" * 60)
    print("OVP PRIME")
    print("=" * 60)
    print(f"Session:              {summary['session_id']}")
    print(f"Path:                 {summary['path']}")
    print(f"Latest:               {summary['latest_path']}")
    print(f"Source context pack:  {summary['source_context_pack']}")
    print(f"Budget tokens:        {summary['context_budget_tokens']}")
    print(f"Selected objects:     {summary['context_selected_objects']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
