"""Phase 38 — Working Memory daily distill.

Writes a single markdown file under ``60-Logs/working-memory/YYYY-MM-DD.md``
that summarizes the state of the vault at a moment in time. Designed to be
re-run cheaply (idempotent overwrite of today's file) so the autopilot daemon
can call it once a day.

Sections:

* **Top of Mind** — top-N slugs ranked by ``page_metrics.citation_count`` over
  the lookback window. We use citation_count (inbound wikilinks) rather than
  raw reuse_count because citations describe the *durable* shape of attention,
  while reuse_count is more transient.
* **Fresh Crystals** — Crystal frontmatter for any file under
  ``40-Resources/Crystals/`` whose mtime is within the last 24h. Cheap and
  honest: we don't need a separate audit log because Crystals are
  self-describing.
* **Pending Decisions** — head of ``00-Polaris/Writing-Prompts.md`` (the
  single canonical sink for prompts/questions per Phase 36 feedback-router).
* **EVOLVES Today** — relation_promoted events from
  ``60-Logs/relation-promotions.jsonl`` whose ``ts`` is in the lookback window
  AND whose ``relation_type`` starts with ``evolves:``. Grouped by subtype.
* **Pulse Highlights** — event_type counts from ``60-Logs/pipeline.jsonl`` in
  the lookback window. Mirrors the Pulse panel but as a static table.

Empty sections are still rendered with a "(none)" placeholder so the file
shape is stable across days.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import date as _date_cls, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from ..projection_labels import frontmatter_projection_fields
    from ..runtime import VaultLayout, resolve_vault_dir
except ImportError:
    from ovp_pipeline.projection_labels import frontmatter_projection_fields  # type: ignore
    from ovp_pipeline.runtime import VaultLayout, resolve_vault_dir  # type: ignore


WORKING_MEMORY_DIR = ("60-Logs", "working-memory")
DEFAULT_TOP_N = 5
DEFAULT_LOOKBACK_HOURS = 24
DEFAULT_TOP_OF_MIND_LOOKBACK_DAYS = 7


def _parse_ts(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    # Legacy "timestamp" fields were written without TZ info; coerce to UTC
    # so the `<` comparison against the offset-aware `since` cutoff works.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return out


def _top_of_mind(layout: VaultLayout, *, top_n: int, now: datetime) -> list[dict[str, Any]]:
    """Top-N pages by inbound citations, restricted to slugs touched in the
    last ``DEFAULT_TOP_OF_MIND_LOOKBACK_DAYS`` (so a high-citation but stale
    page doesn't dominate every day)."""
    if not layout.knowledge_db.exists():
        return []
    cutoff = int((now - timedelta(days=DEFAULT_TOP_OF_MIND_LOOKBACK_DAYS)).timestamp())
    rows: list[dict[str, Any]] = []
    try:
        with sqlite3.connect(layout.knowledge_db) as conn:
            for slug, citation_count, reuse_count, last_seen_ts in conn.execute(
                "SELECT pm.slug, pm.citation_count, pm.reuse_count, pm.last_seen_ts "
                "FROM page_metrics pm "
                "WHERE pm.last_seen_ts >= ? "
                "ORDER BY pm.citation_count DESC, pm.reuse_count DESC "
                "LIMIT ?",
                (cutoff, top_n),
            ):
                rows.append(
                    {
                        "slug": str(slug),
                        "citation_count": int(citation_count or 0),
                        "reuse_count": int(reuse_count or 0),
                        "last_seen_ts": int(last_seen_ts or 0),
                    }
                )
    except sqlite3.DatabaseError:
        return []
    return rows


def _fresh_crystals(vault_dir: Path, *, since: datetime) -> list[dict[str, Any]]:
    crystals_dir = vault_dir / "40-Resources" / "Crystals"
    if not crystals_dir.exists():
        return []
    cutoff_ts = since.timestamp()
    out: list[dict[str, Any]] = []
    for path in sorted(crystals_dir.glob("*.md")):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff_ts:
            continue
        crystal_id = path.stem
        title = crystal_id
        text = path.read_text(encoding="utf-8", errors="ignore")
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end > 0:
                fm_block = text[3:end]
                for raw_line in fm_block.splitlines():
                    if raw_line.startswith("crystal_id:"):
                        crystal_id = raw_line.split(":", 1)[1].strip()
                    elif raw_line.startswith("date:"):
                        title = f"{crystal_id} ({raw_line.split(':', 1)[1].strip()})"
        out.append({"crystal_id": crystal_id, "title": title, "path": str(path)})
    return out


def _pending_decisions(vault_dir: Path, *, max_lines: int = 10) -> list[str]:
    target = vault_dir / "00-Polaris" / "Writing-Prompts.md"
    if not target.exists():
        return []
    lines = []
    for raw in target.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
        if len(lines) >= max_lines:
            break
    return lines


def _evolves_today(layout: VaultLayout, *, since: datetime) -> dict[str, list[dict[str, str]]]:
    """Group ``relation_promoted`` events with ``relation_type`` starting
    ``evolves:`` by subtype. Returned as ``{subtype: [{source, target}, ...]}``.
    """
    log_path = layout.logs_dir / "relation-promotions.jsonl"
    grouped: dict[str, list[dict[str, str]]] = {}
    for event in _read_jsonl(log_path):
        ts = _parse_ts(event.get("ts"))
        if ts is None or ts < since:
            continue
        relation_type = str(event.get("relation_type") or "")
        if not relation_type.startswith("evolves:"):
            continue
        subtype = relation_type.split(":", 1)[1]
        grouped.setdefault(subtype, []).append(
            {
                "source": str(event.get("source_object_id") or ""),
                "target": str(event.get("target_object_id") or ""),
            }
        )
    return grouped


def _pulse_highlights(layout: VaultLayout, *, since: datetime) -> Counter:
    counts: Counter = Counter()
    for event in _read_jsonl(layout.pipeline_log):
        # Pipeline writers split between "ts" (newer paths) and "timestamp"
        # (older paths) — try both so highlights aren't silently empty when a
        # vault is dominated by legacy events.
        ts = _parse_ts(event.get("ts") or event.get("timestamp"))
        if ts is None or ts < since:
            continue
        event_type = str(event.get("event_type") or "(unknown)")
        counts[event_type] += 1
    return counts


def _render(
    *,
    target_date: _date_cls,
    top_of_mind: list[dict[str, Any]],
    fresh_crystals: list[dict[str, Any]],
    pending_decisions: list[str],
    evolves_today: dict[str, list[dict[str, str]]],
    pulse_highlights: Counter,
) -> str:
    sections: list[str] = []
    sections.append(f"# Working Memory — {target_date.isoformat()}\n")

    sections.append("## Top of Mind\n")
    if top_of_mind:
        for row in top_of_mind:
            sections.append(
                f"- [[{row['slug']}]] — {row['citation_count']} citations, "
                f"{row['reuse_count']} reuses"
            )
    else:
        sections.append("- (none)")
    sections.append("")

    sections.append("## Fresh Crystals\n")
    if fresh_crystals:
        for crystal in fresh_crystals:
            sections.append(f"- [[{crystal['crystal_id']}]] — {crystal['title']}")
    else:
        sections.append("- (none)")
    sections.append("")

    sections.append("## Pending Decisions\n")
    if pending_decisions:
        for line in pending_decisions:
            sections.append(f"- {line}")
    else:
        sections.append("- (none)")
    sections.append("")

    sections.append("## EVOLVES Today\n")
    if evolves_today:
        for subtype in sorted(evolves_today):
            relations = evolves_today[subtype]
            sections.append(f"### {subtype} ({len(relations)})")
            for rel in relations:
                sections.append(f"- [[{rel['source']}]] → [[{rel['target']}]]")
    else:
        sections.append("- (none)")
    sections.append("")

    sections.append("## Pulse Highlights\n")
    if pulse_highlights:
        sections.append("| event_type | count |")
        sections.append("|---|---|")
        for event_type, count in pulse_highlights.most_common():
            sections.append(f"| {event_type} | {count} |")
    else:
        sections.append("- (none)")
    sections.append("")

    return "\n".join(sections).rstrip() + "\n"


def build_working_memory(
    vault_dir: Path,
    *,
    target_date: _date_cls | None = None,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    top_n: int = DEFAULT_TOP_N,
    now: datetime | None = None,
) -> Path:
    """Materialize the working-memory file for ``target_date``.

    ``now`` is overridable for tests; defaults to ``datetime.now(UTC)``. The
    lookback window is computed from ``now`` regardless of ``target_date`` so
    the file always reflects "the last 24h leading up to now".
    """
    layout = VaultLayout.from_vault(vault_dir)
    now = now or datetime.now(timezone.utc)
    target_date = target_date or now.date()
    since = now - timedelta(hours=lookback_hours)

    output_dir = vault_dir.joinpath(*WORKING_MEMORY_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{target_date.isoformat()}.md"

    body = _render(
        target_date=target_date,
        top_of_mind=_top_of_mind(layout, top_n=top_n, now=now),
        fresh_crystals=_fresh_crystals(vault_dir, since=since),
        pending_decisions=_pending_decisions(vault_dir),
        evolves_today=_evolves_today(layout, since=since),
        pulse_highlights=_pulse_highlights(layout, since=since),
    )
    frontmatter = (
        "---\n"
        "type: working_memory\n"
        f"date: {target_date.isoformat()}\n"
        + "\n".join(
            frontmatter_projection_fields(
                surface="working_memory",
                projection_kind="context_pack_projection",
                owner_pack="research-tech",
                generated_by="build_working_memory",
                derived_from=("knowledge.db", "crystals", "audit ledgers"),
                rebuild_policy="on_derived_refresh",
            )
        )
        + "\n---\n\n"
    )
    output_path.write_text(frontmatter + body, encoding="utf-8")
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ovp-working-memory",
        description="Write the daily working-memory distill markdown file.",
    )
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault root (default: cwd)")
    parser.add_argument(
        "--date",
        default=None,
        help="Target date in YYYY-MM-DD (default: today UTC)",
    )
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=DEFAULT_LOOKBACK_HOURS,
        help=f"Hours to scan for fresh activity (default: {DEFAULT_LOOKBACK_HOURS})",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=DEFAULT_TOP_N,
        help=f"Top-N items per ranked section (default: {DEFAULT_TOP_N})",
    )
    parser.add_argument("--json", action="store_true", help="Print structured summary to stdout.")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    target_date: _date_cls | None = None
    if args.date:
        target_date = _date_cls.fromisoformat(args.date)
    output_path = build_working_memory(
        vault_dir,
        target_date=target_date,
        lookback_hours=args.lookback_hours,
        top_n=args.top_n,
    )

    if args.json:
        print(json.dumps({"path": str(output_path)}, ensure_ascii=False, indent=2))
        return 0

    print("=" * 60)
    print("WORKING MEMORY")
    print("=" * 60)
    print(f"Path:                 {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
