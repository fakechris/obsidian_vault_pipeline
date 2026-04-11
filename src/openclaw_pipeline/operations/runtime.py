from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path

from ..derived.paths import review_queue_path
from ..extraction.artifacts import iter_run_results
from ..runtime import VaultLayout, iter_markdown_files, read_markdown_frontmatter, resolve_vault_dir
from .specs import OperationProfileSpec


def _frontmatter_audit_items(vault_dir: Path) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for md_file in iter_markdown_files(vault_dir):
        frontmatter = read_markdown_frontmatter(md_file)
        if frontmatter.get("title"):
            continue
        items.append(
            {
                "queue_name": "frontmatter",
                "issue_type": "missing-title",
                "file": str(md_file.relative_to(vault_dir)),
                "message": "Missing title field in frontmatter",
                "review_required": True,
            }
        )
    return items


def _noop_items(_: Path) -> list[dict[str, object]]:
    return []


def _review_queue_items(vault_dir: Path) -> list[dict[str, object]]:
    layout = VaultLayout.from_vault(vault_dir)
    items: list[dict[str, object]] = []
    for result in iter_run_results(layout, pack_name="default-knowledge"):
        issue_type = "extraction-empty" if not result.records else "extraction-review"
        items.append(
            {
                "queue_name": "review",
                "issue_type": issue_type,
                "file": result.source_path,
                "profile": result.profile_name,
                "record_count": len(result.records),
                "relation_count": len(result.relations),
                "message": f"{result.profile_name} produced {len(result.records)} records",
                "review_required": True,
            }
        )
    return items


def _subject_key(claim_text: str) -> str:
    lowered = claim_text.strip().lower()
    lowered = re.sub(r"\s+", " ", lowered)
    for marker in (" supports ", " does not support ", " is ", " are ", " has ", " have "):
        if marker in lowered:
            return lowered.split(marker, 1)[0].strip()
    return lowered.split(".", 1)[0].strip()


def _truth_contradiction_items(vault_dir: Path) -> list[dict[str, object]]:
    layout = VaultLayout.from_vault(vault_dir)
    if not layout.knowledge_db.exists():
        return []

    with sqlite3.connect(layout.knowledge_db) as conn:
        rows = conn.execute(
            """
            SELECT contradiction_id, subject_key, positive_claim_ids_json, negative_claim_ids_json
            FROM contradictions
            WHERE status = 'open'
            ORDER BY subject_key
            """
        ).fetchall()

    items: list[dict[str, object]] = []
    with sqlite3.connect(layout.knowledge_db) as conn:
        for contradiction_id, subject, positive_json, negative_json in rows:
            positive_ids = json.loads(str(positive_json))
            negative_ids = json.loads(str(negative_json))
            positive_claims = _load_claim_rows(conn, positive_ids)
            negative_claims = _load_claim_rows(conn, negative_ids)
            if not positive_claims or not negative_claims:
                continue
            items.append(
                {
                    "queue_name": "contradictions",
                    "issue_type": "truth-contradiction",
                    "file": f"{subject}.json",
                    "contradiction_id": str(contradiction_id),
                    "subject_key": subject,
                    "positive_claims": positive_claims,
                    "negative_claims": negative_claims,
                    "message": f"Detected contradictory truth claims for '{subject}'",
                    "review_required": True,
                }
            )
    return items


def _load_claim_rows(conn: sqlite3.Connection, claim_ids: list[str]) -> list[dict[str, str]]:
    if not claim_ids:
        return []
    placeholders = ",".join("?" for _ in claim_ids)
    rows = conn.execute(
        f"""
        SELECT claim_id, object_id, claim_text
        FROM claims
        WHERE claim_id IN ({placeholders})
        ORDER BY claim_id
        """,
        tuple(claim_ids),
    ).fetchall()
    return [
        {
            "claim_id": str(claim_id),
            "object_id": str(object_id),
            "claim_text": str(claim_text),
        }
        for claim_id, object_id, claim_text in rows
    ]


def _stale_summary_items(vault_dir: Path) -> list[dict[str, object]]:
    layout = VaultLayout.from_vault(vault_dir)
    if not layout.knowledge_db.exists():
        return []

    with sqlite3.connect(layout.knowledge_db) as conn:
        rows = conn.execute(
            """
            SELECT objects.object_id, objects.title, compiled_summaries.summary_text,
                   COALESCE(rel.outgoing_count, 0) AS outgoing_count
            FROM objects
            LEFT JOIN compiled_summaries ON compiled_summaries.object_id = objects.object_id
            LEFT JOIN (
                SELECT source_object_id, COUNT(*) AS outgoing_count
                FROM relations
                GROUP BY source_object_id
            ) AS rel ON rel.source_object_id = objects.object_id
            ORDER BY objects.object_id
            """
        ).fetchall()

    items: list[dict[str, object]] = []
    for object_id, title, summary_text, outgoing_count in rows:
        summary = str(summary_text or "").strip()
        if outgoing_count > 0:
            continue
        if len(summary) >= 40 and summary.lower() != str(title).strip().lower():
            continue
        items.append(
            {
                "queue_name": "stale-summaries",
                "issue_type": "stale-compiled-summary",
                "file": f"{object_id}.json",
                "object_id": str(object_id),
                "title": str(title),
                "summary_text": summary,
                "outgoing_relation_count": int(outgoing_count or 0),
                "message": f"Compiled summary for '{title}' is weak and has no supporting relations",
                "review_required": True,
            }
        )
    return items


_OPERATION_BUILDERS: dict[str, Callable[[Path], list[dict[str, object]]]] = {
    "vault/frontmatter_audit": _frontmatter_audit_items,
    "vault/review_queue": _review_queue_items,
    "vault/bridge_recommendations": _noop_items,
    "truth/contradiction_review": _truth_contradiction_items,
    "truth/stale_summary_review": _stale_summary_items,
}


def run_operation_profile(vault_dir: Path, profile: OperationProfileSpec) -> list[Path]:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    builder = _OPERATION_BUILDERS.get(profile.name, _noop_items)
    items = builder(resolved_vault)

    written: list[Path] = []
    for item in items:
        artifact_path = review_queue_path(
            layout,
            queue_name=str(item["queue_name"]),
            subject=Path(str(item["file"])).stem,
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(artifact_path)
    return written
