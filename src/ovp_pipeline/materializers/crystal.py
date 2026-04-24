"""Phase 38 — Crystal materializer.

A Crystal is a persisted, frozen snapshot of an ``operator_briefing`` assembly
recipe. Where ``observation_surface`` payloads are ephemeral (recomputed on
every UI / API call), a Crystal lands on disk as a markdown note in
``40-Resources/Crystals/`` with frontmatter declaring its source object ids,
the EVOLVES relations that motivated it, and the assembly recipe used to
build it.

Design choices:

* ``crystal_id`` is content-hash-derived (not timestamp-derived), so
  re-materializing the same snapshot produces the same id and overwrites
  the same file. Run twice in a minute → one file. Run after the underlying
  state changed → a *new* Crystal, leaving the prior one as a historical
  record.
* The hash input deliberately excludes the recipe's ``generated_at`` field
  so identical content at different timestamps is still considered the same
  Crystal. Callers wanting time-distinguished Crystals should pass an
  explicit ``date`` and accept that two same-day runs collapse.
* Frontmatter follows the existing OVP note conventions (YAML between
  ``---`` fences, then ``# title``) so Obsidian renders it like any other
  note.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date as _date_cls
from pathlib import Path
from typing import Any, Iterable

CRYSTAL_DIR = ("40-Resources", "Crystals")
ASSEMBLY_RECIPE = "operator_briefing"


@dataclass(frozen=True)
class CrystalRecord:
    crystal_id: str
    path: Path
    source_object_ids: tuple[str, ...]
    evolves_relations: tuple[dict[str, str], ...]
    created: bool


def _collect_source_object_ids(snapshot: dict[str, Any]) -> list[str]:
    """Union of object_ids referenced in changed_objects, active_topics, and
    insight payloads. Order is preserved (first-seen wins) so the frontmatter
    is stable across runs with the same input."""
    ids: list[str] = []
    seen: set[str] = set()

    def _add(value: object) -> None:
        if not isinstance(value, str) or not value or value in seen:
            return
        seen.add(value)
        ids.append(value)

    for item in snapshot.get("changed_objects") or []:
        if isinstance(item, dict):
            _add(item.get("object_id"))
    for item in snapshot.get("active_topics") or []:
        if isinstance(item, dict):
            _add(item.get("object_id"))
    for item in snapshot.get("insights") or []:
        if isinstance(item, dict):
            for object_id in item.get("object_ids") or []:
                _add(object_id)
    return ids


def _query_evolves_relations(
    vault_dir: Path,
    object_ids: Iterable[str],
    *,
    pack_name: str,
) -> list[dict[str, str]]:
    """Pull every ``evolves:*`` graph edge that touches any source object id.

    Uses ``knowledge_index.ensure_knowledge_db_current`` so a stale DB gets
    refreshed before the query — we never want a Crystal to silently miss a
    just-promoted EVOLVES relation. Returns ``[]`` if the DB doesn't exist
    yet (e.g. an empty vault on first build).
    """
    import sqlite3

    from ..knowledge_index import ensure_knowledge_db_current
    from ..runtime import VaultLayout

    ids = [oid for oid in object_ids if oid]
    if not ids:
        return []
    try:
        ensure_knowledge_db_current(vault_dir)
    except Exception:
        pass
    db_path = VaultLayout.from_vault(vault_dir).knowledge_db
    if not Path(db_path).exists():
        return []
    placeholders = ",".join("?" for _ in ids)
    sql = (
        "SELECT source_object_id, target_object_id, edge_kind "
        "FROM graph_edges "
        f"WHERE pack = ? AND edge_kind LIKE 'evolves:%' "
        f"AND (source_object_id IN ({placeholders}) "
        f"OR target_object_id IN ({placeholders})) "
        "ORDER BY edge_kind, source_object_id, target_object_id"
    )
    out: list[dict[str, str]] = []
    with sqlite3.connect(db_path) as conn:
        for src, tgt, kind in conn.execute(sql, [pack_name, *ids, *ids]):
            subtype = str(kind).split(":", 1)[1] if ":" in str(kind) else ""
            out.append({"source": str(src), "target": str(tgt), "subtype": subtype})
    return out


def _hash_payload(snapshot: dict[str, Any], object_ids: list[str]) -> str:
    """Stable digest over content (not timestamps).

    ``generated_at`` and ``queue_summary.running_count`` are excluded from
    the hash so the same logical state at two different moments produces the
    same Crystal id. Anything that materially affects the briefing — signals,
    issues, insights, object set — does flow into the hash.
    """
    salient = {
        "object_ids": object_ids,
        "unresolved_issues": [
            {
                "signal_id": item.get("signal_id"),
                "signal_type": item.get("signal_type"),
                "title": item.get("title"),
            }
            for item in snapshot.get("unresolved_issues") or []
            if isinstance(item, dict)
        ],
        "insights": [
            {
                "kind": item.get("kind"),
                "title": item.get("title"),
                "object_ids": item.get("object_ids"),
            }
            for item in snapshot.get("insights") or []
            if isinstance(item, dict)
        ],
        "priority_items_count": len(snapshot.get("priority_items") or []),
    }
    blob = json.dumps(salient, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _crystal_id(snapshot: dict[str, Any], object_ids: list[str], *, when: _date_cls) -> str:
    digest = _hash_payload(snapshot, object_ids)
    return f"crystal-{when.isoformat()}-{digest[:8]}"


def _yaml_list(values: Iterable[str]) -> str:
    items = [str(v) for v in values]
    if not items:
        return "[]"
    quoted = ", ".join(f'"{item}"' for item in items)
    return f"[{quoted}]"


def _evolves_yaml(relations: list[dict[str, str]]) -> str:
    if not relations:
        return "[]"
    lines = []
    for rel in relations:
        lines.append(
            f'  - {{source: "{rel["source"]}", '
            f'target: "{rel["target"]}", '
            f'subtype: "{rel["subtype"]}"}}'
        )
    return "\n" + "\n".join(lines)


def _render_body(
    snapshot: dict[str, Any],
    object_ids: list[str],
    relations: list[dict[str, str]],
) -> str:
    sections: list[str] = []
    sections.append("# Crystal — Operator Briefing\n")

    sections.append("## Priority Items\n")
    items = snapshot.get("priority_items") or []
    if items:
        for item in items[:10]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("signal_type") or "(untitled)")
            sections.append(f"- {title}")
    else:
        sections.append("- (none)")
    sections.append("")

    sections.append("## Active Topics\n")
    topics = snapshot.get("active_topics") or []
    if topics:
        for topic in topics[:10]:
            if not isinstance(topic, dict):
                continue
            obj_id = str(topic.get("object_id") or "")
            title = str(topic.get("title") or obj_id)
            sections.append(f"- [[{obj_id}|{title}]]")
    else:
        sections.append("- (none)")
    sections.append("")

    sections.append("## Insights\n")
    insights = snapshot.get("insights") or []
    if insights:
        for ins in insights[:10]:
            if not isinstance(ins, dict):
                continue
            sections.append(f"- {ins.get('title') or ins.get('kind') or '(untitled)'}")
    else:
        sections.append("- (none)")
    sections.append("")

    sections.append("## EVOLVES Relations\n")
    if relations:
        for rel in relations:
            sections.append(f"- [[{rel['source']}]] **{rel['subtype']}** [[{rel['target']}]]")
    else:
        sections.append("- (none)")
    sections.append("")

    sections.append("## Source Objects\n")
    if object_ids:
        for oid in object_ids:
            sections.append(f"- [[{oid}]]")
    else:
        sections.append("- (none)")

    return "\n".join(sections).rstrip() + "\n"


def materialize_crystal(
    snapshot: dict[str, Any],
    vault_dir: Path,
    *,
    pack_name: str = "research-tech",
    when: _date_cls | None = None,
) -> CrystalRecord:
    """Persist a briefing snapshot as a Crystal note.

    ``snapshot`` is the dict returned by ``truth_api.get_briefing_snapshot``.
    ``when`` defaults to today's UTC date — pinning it lets tests be
    deterministic across midnight boundaries.

    Returns a :class:`CrystalRecord` with ``created=True`` if a new file was
    written, ``False`` if the Crystal already exists with identical content
    (idempotent re-run).
    """
    from datetime import datetime, timezone

    target_date = when or datetime.now(timezone.utc).date()
    object_ids = _collect_source_object_ids(snapshot)
    relations = _query_evolves_relations(vault_dir, object_ids, pack_name=pack_name)
    crystal_id = _crystal_id(snapshot, object_ids, when=target_date)

    output_dir = vault_dir.joinpath(*CRYSTAL_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{crystal_id}.md"

    frontmatter = (
        "---\n"
        f"crystal_id: {crystal_id}\n"
        f"type: crystal\n"
        f"date: {target_date.isoformat()}\n"
        f"pack: {pack_name}\n"
        f"assembly_recipe: {ASSEMBLY_RECIPE}\n"
        f"source_object_ids: {_yaml_list(object_ids)}\n"
        f"evolves_relations:{_evolves_yaml(relations)}\n"
        "---\n\n"
    )
    body = _render_body(snapshot, object_ids, relations)
    new_content = frontmatter + body

    created = True
    if output_path.exists() and output_path.read_text(encoding="utf-8") == new_content:
        created = False
    else:
        output_path.write_text(new_content, encoding="utf-8")

    return CrystalRecord(
        crystal_id=crystal_id,
        path=output_path,
        source_object_ids=tuple(object_ids),
        evolves_relations=tuple(relations),
        created=created,
    )
