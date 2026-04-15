from __future__ import annotations

from datetime import UTC, datetime
from collections import Counter
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from .runtime import VaultLayout, resolve_vault_dir

MAX_PAGE_SIZE = 500
_FENCED_FRONTMATTER_RE = re.compile(r"^```ya?ml\s*\n---\n(.*?)\n---\n```\s*\n?", re.DOTALL)
_REVIEW_AUDIT_LOG_NAME = "review-actions"
_SIGNAL_LOG_NAME = "signals"
_ACTION_LOG_NAME = "actions"
_SOURCE_NOTE_INDEX_CACHE: dict[tuple[str, tuple[tuple[str, int, int], ...]], dict[str, list[dict[str, str]]]] = {}
_PIPELINE_LOG_INDEX_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
_DEEP_DIVE_OBJECT_MAP_CACHE: dict[tuple[str, int, int], dict[str, list[dict[str, str]]]] = {}
_SIGNAL_LEDGER_SYNC_CACHE: dict[tuple[str, tuple[tuple[str, int, int], ...]], dict[str, Any]] = {}
_EVOLUTION_CANDIDATE_CACHE: dict[tuple[str, tuple[tuple[str, int, int], ...], tuple[str, ...]], list[dict[str, Any]]] = {}
CONTRADICTION_STATUS_EXPLANATIONS = {
    "open": "Active contradiction awaiting review.",
    "resolved_keep_positive": "Reviewed and the positive claim set remains the preferred interpretation.",
    "resolved_keep_negative": "Reviewed and the negative claim set remains the preferred interpretation.",
    "dismissed": "Reviewed and dismissed as not worth keeping in the active contradiction queue.",
    "needs_human": "Requires deeper human judgment before the contradiction can be considered closed.",
}
SIGNAL_TYPE_EXPLANATIONS = {
    "contradiction_open": "Open contradiction detected from the current truth store and awaiting review.",
    "stale_summary": "Compiled summary is currently weak enough to justify targeted rebuild review.",
    "production_gap": "Knowledge production chain is missing an expected downstream stage or reach surface.",
    "contradiction_reviewed": "A contradiction review action recently changed the maintenance state for one or more objects.",
    "summary_rebuilt": "A summary rebuild action recently refreshed one or more compiled summaries.",
    "source_needs_deep_dive": "A processed source note exists without any derived deep dive, so the next extraction step is still missing.",
    "deep_dive_needs_objects": "A deep dive exists without any derived evergreen objects, so absorb-style extraction has not completed yet.",
}
EVOLUTION_LINK_EXPLANATIONS = {
    "challenges": "Newer evidence is challenging the current interpretation.",
    "replaces": "A newer interpretation appears to supersede the older one.",
    "confirms": "Independent evidence is reinforcing the current interpretation.",
    "enriches": "Newer material is adding depth without overturning the core idea.",
}


def _db_path(vault_dir: Path | str) -> Path:
    resolved = resolve_vault_dir(vault_dir)
    return VaultLayout.from_vault(resolved).knowledge_db


def _path_signature(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (str(path), -1, -1)
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _search_root_signatures(vault_dir: Path) -> tuple[tuple[str, int, int], ...]:
    roots = [
        vault_dir / "50-Inbox" / "03-Processed",
        vault_dir / "50-Inbox" / "02-Processing",
        vault_dir / "50-Inbox" / "01-Raw",
    ]
    signatures: list[tuple[str, int, int]] = []
    for root in roots:
        signatures.append(_path_signature(root))
        if not root.exists():
            continue
        for child in sorted(root.iterdir(), key=lambda item: item.name):
            signatures.append(_path_signature(child))
    return tuple(signatures)


def _signal_dependency_signature(vault_dir: Path) -> tuple[tuple[str, int, int], ...]:
    layout = VaultLayout.from_vault(vault_dir)
    signatures = [
        _path_signature(layout.knowledge_db),
        _path_signature(layout.logs_dir / f"{_REVIEW_AUDIT_LOG_NAME}.jsonl"),
        _path_signature(layout.logs_dir / "pipeline.jsonl"),
    ]
    signatures.extend(_search_root_signatures(vault_dir))
    return tuple(signatures)


def _evolution_dependency_signature(vault_dir: Path) -> tuple[tuple[str, int, int], ...]:
    return _signal_dependency_signature(vault_dir)


def _vault_relative_path(vault_dir: Path | str, path: str) -> str:
    resolved = resolve_vault_dir(vault_dir).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        return path
    try:
        return str(candidate.resolve().relative_to(resolved))
    except ValueError:
        return path


def _validate_page_args(*, limit: int, offset: int = 0) -> tuple[int, int]:
    if limit < 0 or offset < 0:
        raise ValueError("limit and offset must be >= 0")
    if limit > MAX_PAGE_SIZE:
        raise ValueError(f"limit must be <= {MAX_PAGE_SIZE}")
    return limit, offset


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _parse_frontmatter(markdown: str) -> dict[str, Any]:
    fenced_match = _FENCED_FRONTMATTER_RE.match(markdown)
    if fenced_match:
        raw_frontmatter = fenced_match.group(1)
        try:
            parsed = yaml.safe_load(raw_frontmatter) or {}
        except yaml.YAMLError:
            parsed = {}
        return parsed if isinstance(parsed, dict) else {}
    if not markdown.startswith("---\n"):
        return {}
    end = markdown.find("\n---\n", 4)
    if end == -1:
        return {}
    raw_frontmatter = markdown[4:end]
    try:
        parsed = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _read_note_frontmatter(vault_dir: Path | str, relative_path: str) -> dict[str, Any]:
    resolved = resolve_vault_dir(vault_dir)
    note_path = (resolved / relative_path).resolve()
    try:
        note_path.relative_to(resolved.resolve())
    except ValueError:
        return {}
    if not note_path.is_file():
        return {}
    return _parse_frontmatter(note_path.read_text(encoding="utf-8"))


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _rewrite_jsonl(path: Path, payloads: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for payload in payloads:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def record_review_action(
    vault_dir: Path | str,
    *,
    event_type: str,
    payload: dict[str, Any],
    slug: str = "",
    session_id: str = "ovp-ui",
) -> dict[str, Any]:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    event = {
        "timestamp": timestamp,
        "session_id": session_id,
        "event_type": event_type,
        "slug": slug,
        **payload,
    }
    _append_jsonl(layout.logs_dir / f"{_REVIEW_AUDIT_LOG_NAME}.jsonl", event)
    if layout.knowledge_db.exists():
        with sqlite3.connect(layout.knowledge_db) as conn:
            conn.execute(
                """
                INSERT INTO audit_events (source_log, event_type, slug, session_id, timestamp, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _REVIEW_AUDIT_LOG_NAME,
                    event_type,
                    slug,
                    session_id,
                    timestamp,
                    json.dumps(event, ensure_ascii=False),
                ),
            )
            conn.commit()
    return event


def _is_moc_row(note_type: str, path: str) -> bool:
    return note_type == "moc" or "/10-Knowledge/Atlas/" in path or Path(path).name.startswith("MOC")


def _batch_object_rows(vault_dir: Path | str, object_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not object_ids:
        return {}
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    placeholders = ",".join("?" for _ in object_ids)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT object_id, object_kind, title, canonical_path, source_slug
            FROM objects
            WHERE object_id IN ({placeholders})
            ORDER BY object_id
            """,
            tuple(object_ids),
        ).fetchall()
    return {
        row[0]: {
            "object_id": row[0],
            "object_kind": row[1],
            "title": row[2],
            "canonical_path": _vault_relative_path(resolved_vault, row[3]),
            "source_slug": row[4],
        }
        for row in rows
    }


def get_object_provenance_map(vault_dir: Path | str, object_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not object_ids:
        return {}
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    ordered_object_ids = list(dict.fromkeys(object_ids))
    object_rows = _batch_object_rows(vault_dir, ordered_object_ids)
    placeholders = ",".join("?" for _ in ordered_object_ids)
    with sqlite3.connect(db_path) as conn:
        mention_rows = conn.execute(
            f"""
            SELECT page_links.target_slug, pages_index.slug, pages_index.title, pages_index.note_type, pages_index.path
            FROM page_links
            JOIN pages_index ON pages_index.slug = page_links.source_slug
            WHERE page_links.target_slug IN ({placeholders})
              AND pages_index.slug != page_links.target_slug
            ORDER BY page_links.target_slug, pages_index.slug
            """,
            tuple(ordered_object_ids),
        ).fetchall()

    provenance = {
        object_id: {
            "title": object_rows.get(object_id, {}).get("title", object_id),
            "evergreen_path": object_rows.get(object_id, {}).get("canonical_path", ""),
            "source_notes": [],
            "mocs": [],
        }
        for object_id in ordered_object_ids
    }
    for target_slug, slug, title, note_type, path in mention_rows:
        item = {
            "slug": slug,
            "title": title,
            "note_type": note_type,
            "path": _vault_relative_path(resolved_vault, path),
        }
        if _is_moc_row(note_type, path):
            provenance[target_slug]["mocs"].append(item)
        elif note_type != "evergreen":
            provenance[target_slug]["source_notes"].append(item)
    return provenance


def get_review_context(vault_dir: Path | str, object_ids: list[str]) -> dict[str, Any]:
    normalized_object_ids = list(dict.fromkeys(object_id for object_id in object_ids if object_id))
    if not normalized_object_ids:
        return {
            "object_count": 0,
            "source_note_count": 0,
            "moc_count": 0,
            "contradiction_count": 0,
            "open_contradiction_count": 0,
            "stale_summary_count": 0,
            "latest_event_date": "",
            "source_notes": [],
            "mocs": [],
            "stale_summary_object_ids": [],
            "contradiction_object_ids": [],
            "recent_review_actions": [],
        }

    provenance_map = get_object_provenance_map(vault_dir, normalized_object_ids)
    source_notes: dict[str, dict[str, Any]] = {}
    mocs: dict[str, dict[str, Any]] = {}
    for provenance in provenance_map.values():
        for note in provenance["source_notes"]:
            source_notes.setdefault(note["slug"], note)
        for moc in provenance["mocs"]:
            mocs.setdefault(moc["slug"], moc)

    db_path = _db_path(vault_dir)
    placeholders = ",".join("?" for _ in normalized_object_ids)
    with sqlite3.connect(db_path) as conn:
        stale_rows = conn.execute(
            f"""
            SELECT objects.object_id, objects.title, compiled_summaries.summary_text,
                   COALESCE(rel.outgoing_count, 0) AS outgoing_count
            FROM objects
            LEFT JOIN compiled_summaries ON compiled_summaries.object_id = objects.object_id
            LEFT JOIN (
                SELECT source_object_id, COUNT(*) AS outgoing_count
                FROM relations
                GROUP BY source_object_id
            ) AS rel ON rel.source_object_id = objects.object_id
            WHERE objects.object_id IN ({placeholders})
            ORDER BY objects.object_id
            """,
            tuple(normalized_object_ids),
        ).fetchall()
        event_row = conn.execute(
            f"""
            SELECT MAX(event_date)
            FROM timeline_events
            WHERE slug IN ({placeholders})
            """,
            tuple(normalized_object_ids),
        ).fetchone()
        contradiction_rows = conn.execute(
            """
            SELECT contradiction_id, positive_claim_ids_json, negative_claim_ids_json, status
            FROM contradictions
            ORDER BY contradiction_id
            """
        ).fetchall()

    stale_summaries: list[dict[str, Any]] = []
    for object_id, title, summary_text, outgoing_count in stale_rows:
        summary = str(summary_text or "").strip()
        if outgoing_count > 0:
            continue
        if len(summary) >= 40 and summary.lower() != str(title).strip().lower():
            continue
        stale_summaries.append(
            {
                "object_id": str(object_id),
                "title": str(title),
                "summary_text": summary,
                "outgoing_relation_count": int(outgoing_count or 0),
                "object_path": f"/object?id={object_id}",
            }
        )
    stale_summary_object_ids = [item["object_id"] for item in stale_summaries]

    contradiction_ids: list[str] = []
    open_contradiction_ids: list[str] = []
    contradiction_object_ids: set[str] = set()
    object_id_set = set(normalized_object_ids)
    for contradiction_id, positive_json, negative_json, status in contradiction_rows:
        claim_ids = json.loads(positive_json) + json.loads(negative_json)
        matched_object_ids = {
            claim_id.split("::", 1)[0]
            for claim_id in claim_ids
            if claim_id.split("::", 1)[0] in object_id_set
        }
        if not matched_object_ids:
            continue
        contradiction_ids.append(str(contradiction_id))
        contradiction_object_ids.update(matched_object_ids)
        if status == "open":
            open_contradiction_ids.append(str(contradiction_id))

    return {
        "object_count": len(normalized_object_ids),
        "source_note_count": len(source_notes),
        "moc_count": len(mocs),
        "contradiction_count": len(contradiction_ids),
        "open_contradiction_count": len(open_contradiction_ids),
        "stale_summary_count": len(stale_summaries),
        "latest_event_date": str(event_row[0] or ""),
        "source_notes": list(source_notes.values()),
        "mocs": list(mocs.values()),
        "stale_summary_object_ids": stale_summary_object_ids,
        "contradiction_object_ids": sorted(contradiction_object_ids),
        "recent_review_actions": list_review_actions(vault_dir, object_ids=normalized_object_ids, limit=5),
    }


def _claim_details_map(vault_dir: Path | str, claim_ids: list[str]) -> dict[str, dict[str, Any]]:
    normalized_claim_ids = list(dict.fromkeys(claim_id for claim_id in claim_ids if claim_id))
    if not normalized_claim_ids:
        return {}
    db_path = _db_path(vault_dir)
    placeholders = ",".join("?" for _ in normalized_claim_ids)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT claims.claim_id, claims.object_id, objects.title, claims.claim_kind, claims.claim_text, claims.confidence
            FROM claims
            JOIN objects ON objects.object_id = claims.object_id
            WHERE claims.claim_id IN ({placeholders})
            ORDER BY claims.claim_id
            """,
            tuple(normalized_claim_ids),
        ).fetchall()
    return {
        row[0]: {
            "claim_id": row[0],
            "object_id": row[1],
            "object_title": row[2],
            "claim_kind": row[3],
            "claim_text": row[4],
            "confidence": row[5],
        }
        for row in rows
    }


def _claim_evidence_map(vault_dir: Path | str, claim_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    normalized_claim_ids = list(dict.fromkeys(claim_id for claim_id in claim_ids if claim_id))
    if not normalized_claim_ids:
        return {}
    db_path = _db_path(vault_dir)
    placeholders = ",".join("?" for _ in normalized_claim_ids)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT claim_id, source_slug, evidence_kind, quote_text
            FROM claim_evidence
            WHERE claim_id IN ({placeholders})
            ORDER BY claim_id, source_slug, evidence_kind
            """,
            tuple(normalized_claim_ids),
        ).fetchall()
    evidence_map: dict[str, list[dict[str, Any]]] = {}
    for claim_id, source_slug, evidence_kind, quote_text in rows:
        evidence_map.setdefault(claim_id, []).append(
            {
                "source_slug": source_slug,
                "evidence_kind": evidence_kind,
                "quote_text": quote_text or "",
            }
        )
    return evidence_map


def _rank_contradiction_evidence(item: dict[str, Any]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    rank = 1
    for polarity, claims in (("positive", item["positive_claims"]), ("negative", item["negative_claims"])):
        for claim in claims:
            for evidence in claim["evidence"]:
                ranked.append(
                    {
                        "rank": rank,
                        "polarity": polarity,
                        "claim_id": claim["claim_id"],
                        "object_id": claim["object_id"],
                        "object_title": claim["object_title"],
                        "evidence_kind": evidence["evidence_kind"],
                        "quote_text": evidence["quote_text"],
                        "source_slug": evidence["source_slug"],
                    }
                )
                rank += 1
    return ranked


def _parse_iso_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _candidate_evolution_id(
    *,
    link_type: str,
    subject_kind: str,
    subject_id: str,
    earlier_ref: str,
    later_ref: str,
) -> str:
    fingerprint = "::".join([link_type, subject_kind, subject_id, earlier_ref, later_ref])
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]


def _page_paths_for_slugs(vault_dir: Path | str, slugs: list[str]) -> dict[str, str]:
    normalized_slugs = list(dict.fromkeys(slug for slug in slugs if slug))
    if not normalized_slugs:
        return {}
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    placeholders = ",".join("?" for _ in normalized_slugs)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT slug, path
            FROM pages_index
            WHERE slug IN ({placeholders})
            """,
            tuple(normalized_slugs),
        ).fetchall()
    return {
        str(slug): _vault_relative_path(resolved_vault, path)
        for slug, path in rows
    }


def _note_date_text(vault_dir: Path | str, note_path: str) -> str:
    frontmatter = _read_note_frontmatter(vault_dir, note_path)
    date_value = frontmatter.get("date")
    return str(date_value).strip() if date_value is not None else ""


def _note_date_sort_key(date_text: str) -> tuple[int, float, str]:
    parsed = _parse_iso_datetime(date_text)
    if parsed is None:
        return (0, 0.0, date_text)
    return (1, parsed.timestamp(), date_text)


def _read_note_text(vault_dir: Path | str, relative_path: str) -> str:
    resolved = resolve_vault_dir(vault_dir)
    note_path = (resolved / relative_path).resolve()
    try:
        note_path.relative_to(resolved.resolve())
    except ValueError:
        return ""
    if not note_path.is_file():
        return ""
    return note_path.read_text(encoding="utf-8")


_SUPERSESSION_CUE_RE = re.compile(
    r"\b(supersed(?:e|es|ed|ing)|replace(?:s|d|ment|ments)?|obsolete|deprecated|no longer|instead)\b",
    re.IGNORECASE,
)
_CONFIRMATION_CUE_RE = re.compile(
    r"\b(confirm(?:s|ed|ing)?|corroborat(?:e|es|ed|ing)|validated?|agrees?\s+with|supports?)\b",
    re.IGNORECASE,
)


def _has_cue(
    vault_dir: Path | str,
    note_path: str,
    pattern: re.Pattern[str],
) -> bool:
    text = _read_note_text(vault_dir, note_path).lower()
    for match in pattern.finditer(text):
        prefix = text[max(0, match.start() - 24) : match.start()]
        if re.search(r"(?:\bnot\s+|\bwithout\s+|n't\s+)$", prefix):
            continue
        return True
    return False


def _has_supersession_cue(vault_dir: Path | str, note_path: str) -> bool:
    return _has_cue(vault_dir, note_path, _SUPERSESSION_CUE_RE)


def _has_confirmation_cue(vault_dir: Path | str, note_path: str) -> bool:
    return _has_cue(vault_dir, note_path, _CONFIRMATION_CUE_RE)


def _evolution_candidate_matches_query(item: dict[str, Any], normalized_query: str) -> bool:
    haystacks = [
        str(item.get("link_type") or "").lower(),
        str(item.get("subject_kind") or "").lower(),
        str(item.get("subject_id") or "").lower(),
        str(item.get("earlier_ref") or "").lower(),
        str(item.get("later_ref") or "").lower(),
        *(str(path).lower() for path in item.get("source_paths", [])),
        *(str(code).lower() for code in item.get("reason_codes", [])),
        *(
            str(entry.get("source_slug") or entry.get("path") or entry.get("title") or "").lower()
            for entry in item.get("evidence", [])
            if isinstance(entry, dict)
        ),
    ]
    return any(normalized_query in haystack for haystack in haystacks if haystack)


def list_objects(
    vault_dir: Path | str,
    *,
    limit: int = 100,
    offset: int = 0,
    query: str | None = None,
) -> list[dict[str, Any]]:
    limit, offset = _validate_page_args(limit=limit, offset=offset)
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    normalized_query = _escape_like(query.strip().lower()) if query else ""
    with sqlite3.connect(db_path) as conn:
        sql = """
            SELECT object_id, object_kind, title, canonical_path, source_slug
            FROM objects
        """
        params: list[Any] = []
        if normalized_query:
            sql += """
                WHERE lower(object_id) LIKE ? ESCAPE '\\'
                   OR lower(title) LIKE ? ESCAPE '\\'
                   OR lower(source_slug) LIKE ? ESCAPE '\\'
            """
            params.extend(
                [
                    f"%{normalized_query}%",
                    f"%{normalized_query}%",
                    f"%{normalized_query}%",
                ]
            )
        sql += " ORDER BY object_id LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(sql, tuple(params)).fetchall()

    return [
        {
            "object_id": row[0],
            "object_kind": row[1],
            "title": row[2],
            "canonical_path": _vault_relative_path(resolved_vault, row[3]),
            "source_slug": row[4],
        }
        for row in rows
    ]


def search_vault_surface(
    vault_dir: Path | str,
    *,
    query: str,
    object_limit: int = 25,
    note_limit: int = 25,
) -> dict[str, Any]:
    normalized_query = query.strip()
    object_limit, _ = _validate_page_args(limit=object_limit, offset=0)
    note_limit, _ = _validate_page_args(limit=note_limit, offset=0)
    if not normalized_query:
        return {
            "query": "",
            "objects": [],
            "notes": [],
        }
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    escaped_query = _escape_like(normalized_query.lower())
    with sqlite3.connect(db_path) as conn:
        object_rows = conn.execute(
            """
            SELECT DISTINCT objects.object_id, objects.object_kind, objects.title, objects.canonical_path, objects.source_slug
            FROM objects
            LEFT JOIN compiled_summaries ON compiled_summaries.object_id = objects.object_id
            LEFT JOIN claims ON claims.object_id = objects.object_id
            WHERE lower(objects.object_id) LIKE ? ESCAPE '\\'
               OR lower(objects.title) LIKE ? ESCAPE '\\'
               OR lower(objects.source_slug) LIKE ? ESCAPE '\\'
               OR lower(compiled_summaries.summary_text) LIKE ? ESCAPE '\\'
               OR lower(claims.claim_text) LIKE ? ESCAPE '\\'
            ORDER BY objects.object_id
            LIMIT ?
            """,
            (
                f"%{escaped_query}%",
                f"%{escaped_query}%",
                f"%{escaped_query}%",
                f"%{escaped_query}%",
                f"%{escaped_query}%",
                object_limit,
            ),
        ).fetchall()
        note_rows = conn.execute(
            """
            SELECT slug, title, note_type, path
            FROM pages_index
            WHERE lower(slug) LIKE ? ESCAPE '\\'
               OR lower(title) LIKE ? ESCAPE '\\'
               OR lower(path) LIKE ? ESCAPE '\\'
               OR lower(body) LIKE ? ESCAPE '\\'
            ORDER BY
              CASE note_type
                WHEN 'evergreen' THEN 0
                WHEN 'deep_dive' THEN 1
                WHEN 'moc' THEN 2
                ELSE 3
              END,
              slug
            LIMIT ?
            """,
            (
                f"%{escaped_query}%",
                f"%{escaped_query}%",
                f"%{escaped_query}%",
                f"%{escaped_query}%",
                note_limit,
            ),
        ).fetchall()

    objects = [
        {
            "object_id": row[0],
            "object_kind": row[1],
            "title": row[2],
            "canonical_path": _vault_relative_path(resolved_vault, row[3]),
            "source_slug": row[4],
        }
        for row in object_rows
    ]
    notes = [
        {
            "slug": row[0],
            "title": row[1],
            "note_type": row[2],
            "path": _vault_relative_path(resolved_vault, row[3]),
        }
        for row in note_rows
    ]
    return {
        "query": normalized_query,
        "objects": objects,
        "notes": notes,
    }


def count_objects(vault_dir: Path | str, *, query: str | None = None) -> int:
    db_path = _db_path(vault_dir)
    normalized_query = _escape_like(query.strip().lower()) if query else ""
    sql = "SELECT COUNT(*) FROM objects"
    params: list[Any] = []
    if normalized_query:
        sql += """
            WHERE lower(object_id) LIKE ? ESCAPE '\\'
               OR lower(title) LIKE ? ESCAPE '\\'
               OR lower(source_slug) LIKE ? ESCAPE '\\'
        """
        params.extend(
            [
                f"%{normalized_query}%",
                f"%{normalized_query}%",
                f"%{normalized_query}%",
            ]
        )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    return int(row[0]) if row else 0


def _surface_page_query_clauses(*, note_type: str, normalized_query: str) -> tuple[str, list[Any]]:
    where = ["pages_index.note_type = ?"]
    params: list[Any] = [note_type]
    if normalized_query:
        where.append(
            """
            (
              lower(pages_index.slug) LIKE ? ESCAPE '\\'
              OR lower(pages_index.title) LIKE ? ESCAPE '\\'
              OR lower(objects.object_id) LIKE ? ESCAPE '\\'
              OR lower(objects.title) LIKE ? ESCAPE '\\'
            )
            """.strip()
        )
        params.extend([f"%{normalized_query}%"] * 4)
    return " AND ".join(where), params


def _list_surface_groups(
    vault_dir: Path | str,
    *,
    note_type: str,
    query: str | None,
    limit: int,
    object_list_key: str,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    normalized_query = _escape_like(query.strip().lower()) if query else ""
    where_sql, base_params = _surface_page_query_clauses(
        note_type=note_type,
        normalized_query=normalized_query,
    )

    with sqlite3.connect(db_path) as conn:
        selected_rows = conn.execute(
            f"""
            SELECT DISTINCT pages_index.slug
            FROM pages_index
            JOIN page_links ON page_links.source_slug = pages_index.slug
            JOIN objects ON objects.object_id = page_links.target_slug
            WHERE {where_sql}
            ORDER BY pages_index.slug
            LIMIT ?
            """,
            tuple([*base_params, limit]),
        ).fetchall()
        selected_slugs = [row[0] for row in selected_rows]
        if not selected_slugs:
            return []
        placeholders = ",".join("?" for _ in selected_slugs)
        rows = conn.execute(
            f"""
            SELECT pages_index.slug, pages_index.title, pages_index.note_type, pages_index.path, objects.object_id, objects.title
            FROM pages_index
            JOIN page_links ON page_links.source_slug = pages_index.slug
            JOIN objects ON objects.object_id = page_links.target_slug
            WHERE pages_index.slug IN ({placeholders})
            ORDER BY pages_index.slug, objects.object_id
            """,
            tuple(selected_slugs),
        ).fetchall()

    grouped: dict[str, dict[str, Any]] = {}
    for slug, title, row_note_type, path, object_id, object_title in rows:
        item = grouped.setdefault(
            slug,
            {
                "slug": slug,
                "title": title,
                "note_type": row_note_type,
                "path": _vault_relative_path(resolved_vault, path),
                object_list_key: [],
            },
        )
        item[object_list_key].append({"object_id": object_id, "title": object_title})
    return list(grouped.values())


def get_object_detail(vault_dir: Path | str, object_id: str) -> dict[str, Any]:
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    escaped = _escape_like(object_id)

    with sqlite3.connect(db_path) as conn:
        object_row = conn.execute(
            """
            SELECT object_id, object_kind, title, canonical_path, source_slug
            FROM objects
            WHERE object_id = ?
            """,
            (object_id,),
        ).fetchone()
        if object_row is None:
            raise ValueError(f"Unknown object_id: {object_id}")

        summary_row = conn.execute(
            """
            SELECT object_id, summary_text, source_slug
            FROM compiled_summaries
            WHERE object_id = ?
            """,
            (object_id,),
        ).fetchone()
        claim_rows = conn.execute(
            """
            SELECT claim_id, claim_kind, claim_text, confidence
            FROM claims
            WHERE object_id = ?
            ORDER BY claim_id
            """,
            (object_id,),
        ).fetchall()
        evidence_rows = conn.execute(
            """
            SELECT claim_id, source_slug, evidence_kind, quote_text
            FROM claim_evidence
            WHERE claim_id IN (
                SELECT claim_id FROM claims WHERE object_id = ?
            )
            ORDER BY claim_id, evidence_kind
            """,
            (object_id,),
        ).fetchall()
        relation_rows = conn.execute(
            """
            SELECT source_object_id, target_object_id, relation_type, evidence_source_slug
            FROM relations
            WHERE source_object_id = ?
            ORDER BY target_object_id
            """,
            (object_id,),
        ).fetchall()
        contradiction_rows = conn.execute(
            """
            SELECT contradiction_id, subject_key, positive_claim_ids_json, negative_claim_ids_json, status, resolution_note, resolved_at
            FROM contradictions
            WHERE positive_claim_ids_json LIKE ? ESCAPE '\\' OR negative_claim_ids_json LIKE ? ESCAPE '\\'
            ORDER BY subject_key
            """,
            (f'%"{escaped}::%', f'%"{escaped}::%'),
        ).fetchall()
        mention_rows = conn.execute(
            """
            SELECT DISTINCT pages_index.slug, pages_index.title, pages_index.note_type, pages_index.path
            FROM page_links
            JOIN pages_index ON pages_index.slug = page_links.source_slug
            WHERE page_links.target_slug = ?
              AND pages_index.slug != ?
            ORDER BY pages_index.slug
            """,
            (object_id, object_id),
        ).fetchall()

    mocs: list[dict[str, Any]] = []
    source_notes: list[dict[str, Any]] = []
    for slug, title, note_type, path in mention_rows:
        item = {
            "slug": slug,
            "title": title,
            "note_type": note_type,
            "path": _vault_relative_path(resolved_vault, path),
        }
        if _is_moc_row(note_type, path):
            mocs.append(item)
            continue
        if slug == object_id:
            continue
        if note_type != "evergreen":
            source_notes.append(item)

    return {
        "object": {
            "object_id": object_row[0],
            "object_kind": object_row[1],
            "title": object_row[2],
            "canonical_path": _vault_relative_path(resolved_vault, object_row[3]),
            "source_slug": object_row[4],
        },
        "summary": (
            {
                "object_id": summary_row[0],
                "summary_text": summary_row[1],
                "source_slug": summary_row[2],
            }
            if summary_row
            else None
        ),
        "claims": [
            {
                "claim_id": row[0],
                "claim_kind": row[1],
                "claim_text": row[2],
                "confidence": row[3],
            }
            for row in claim_rows
        ],
        "evidence": [
            {
                "claim_id": row[0],
                "source_slug": row[1],
                "evidence_kind": row[2],
                "quote_text": row[3],
            }
            for row in evidence_rows
        ],
        "relations": [
            {
                "source_object_id": row[0],
                "target_object_id": row[1],
                "relation_type": row[2],
                "evidence_source_slug": row[3],
            }
            for row in relation_rows
        ],
        "contradictions": [
            {
                "contradiction_id": row[0],
                "subject_key": row[1],
                "positive_claim_ids": json.loads(row[2]),
                "negative_claim_ids": json.loads(row[3]),
                "status": row[4],
                "resolution_note": row[5] or "",
                "resolved_at": row[6] or "",
            }
            for row in contradiction_rows
        ],
        "provenance": {
            "evergreen_path": _vault_relative_path(resolved_vault, object_row[3]),
            "source_notes": source_notes,
            "mocs": mocs,
        },
    }


def _find_note_by_source(vault_dir: Path, *, source_url: str, exclude_path: str) -> dict[str, str] | None:
    cache_key = (str(vault_dir.resolve()), _search_root_signatures(vault_dir))
    source_index = _SOURCE_NOTE_INDEX_CACHE.get(cache_key)
    if source_index is None:
        search_roots = [
            vault_dir / "50-Inbox" / "03-Processed",
            vault_dir / "50-Inbox" / "02-Processing",
            vault_dir / "50-Inbox" / "01-Raw",
        ]
        source_index = {}
        for root in search_roots:
            if not root.exists():
                continue
            for candidate in sorted(root.rglob("*.md")):
                frontmatter = _parse_frontmatter(candidate.read_text(encoding="utf-8"))
                candidate_source = str(frontmatter.get("source", "")).strip()
                if not candidate_source:
                    continue
                title = str(frontmatter.get("title") or candidate.stem).strip()
                source_index.setdefault(candidate_source, []).append(
                    {
                        "title": title,
                        "path": str(candidate.resolve().relative_to(vault_dir.resolve())),
                    }
                )
        _SOURCE_NOTE_INDEX_CACHE.clear()
        _SOURCE_NOTE_INDEX_CACHE[cache_key] = source_index

    resolved_exclude = str((vault_dir / exclude_path).resolve().relative_to(vault_dir.resolve()))
    for item in source_index.get(source_url, []):
        if item["path"] == resolved_exclude:
            continue
        return item
    return None


def _find_note_from_pipeline_log(vault_dir: Path, *, note_path: str) -> dict[str, str] | None:
    log_path = VaultLayout.from_vault(vault_dir).logs_dir / "pipeline.jsonl"
    if not log_path.exists():
        return None
    index = _pipeline_log_index(vault_dir)
    return index["original_source_by_output"].get(str((vault_dir / note_path).resolve().relative_to(vault_dir.resolve())))


def _find_derived_notes_from_pipeline_log(vault_dir: Path, *, note_path: str) -> list[dict[str, str]]:
    log_path = VaultLayout.from_vault(vault_dir).logs_dir / "pipeline.jsonl"
    if not log_path.exists():
        return []
    return list(_pipeline_log_index(vault_dir)["derived_by_source_file"].get(Path(note_path).name, []))


def list_review_actions(
    vault_dir: Path | str,
    *,
    object_ids: list[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    normalized_object_ids = set(object_id for object_id in (object_ids or []) if object_id)
    db_path = _db_path(vault_dir)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT source_log, event_type, slug, session_id, timestamp, payload_json
            FROM audit_events
            WHERE source_log = ?
            ORDER BY timestamp DESC
            LIMIT 200
            """,
            (_REVIEW_AUDIT_LOG_NAME,),
        ).fetchall()
    return _review_action_items(rows, normalized_object_ids=normalized_object_ids, limit=limit)


def _review_action_items(
    rows: list[tuple[Any, ...]],
    *,
    normalized_object_ids: set[str],
    limit: int | None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for source_log, event_type, slug, session_id, timestamp, payload_json in rows:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            payload = {}
        action_object_ids = [
            str(value)
            for value in payload.get("object_ids", [])
            if isinstance(value, str) and value
        ]
        if normalized_object_ids and not normalized_object_ids.intersection(action_object_ids):
            continue
        items.append(
            {
                "source_log": source_log,
                "event_type": event_type,
                "slug": slug,
                "session_id": session_id,
                "timestamp": timestamp,
                "object_ids": action_object_ids,
                "evolution_id": str(payload.get("evolution_id") or ""),
                "subject_kind": str(payload.get("subject_kind") or ""),
                "subject_id": str(payload.get("subject_id") or ""),
                "earlier_ref": str(payload.get("earlier_ref") or ""),
                "later_ref": str(payload.get("later_ref") or ""),
                "link_type": str(payload.get("link_type") or ""),
                "candidate_link_type": str(payload.get("candidate_link_type") or ""),
                "contradiction_ids": [
                    str(value)
                    for value in payload.get("contradiction_ids", [])
                    if isinstance(value, str) and value
                ],
                "status": str(payload.get("status") or ""),
                "note": str(payload.get("note") or ""),
                "rebuilt_object_ids": [
                    str(value)
                    for value in payload.get("rebuilt_object_ids", [])
                    if isinstance(value, str) and value
                ],
                "objects_rebuilt": int(payload.get("objects_rebuilt") or 0),
            }
        )
        if limit is not None and len(items) >= limit:
            break
    return items


def list_evolution_review_actions(
    vault_dir: Path | str,
    *,
    object_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    normalized_object_ids = set(object_id for object_id in (object_ids or []) if object_id)
    db_path = _db_path(vault_dir)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT source_log, event_type, slug, session_id, timestamp, payload_json
            FROM audit_events
            WHERE source_log = ? AND event_type = ?
            ORDER BY timestamp DESC
            """,
            (_REVIEW_AUDIT_LOG_NAME, "ui_evolution_reviewed"),
        ).fetchall()
    return _review_action_items(rows, normalized_object_ids=normalized_object_ids, limit=None)


def _signal_id(signal_type: str, key: str) -> str:
    return f"{signal_type}::{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"


def _action_id(signal_id: str, action_kind: str, target_ref: str, payload: dict[str, Any]) -> str:
    payload_key = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    key = f"{signal_id}::{action_kind}::{target_ref}::{payload_key}"
    return f"action::{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"


def _recommended_action(*, kind: str, label: str, path: str, executable: bool) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": label,
        "path": path,
        "executable": executable,
    }


def _read_action_queue_rows(vault_dir: Path | str) -> list[dict[str, Any]]:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    rows: list[tuple[str]] = []
    if layout.knowledge_db.exists():
        with sqlite3.connect(layout.knowledge_db) as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM audit_events
                WHERE source_log = ?
                ORDER BY timestamp DESC, slug
                """,
                (_ACTION_LOG_NAME,),
            ).fetchall()
    elif (layout.logs_dir / f"{_ACTION_LOG_NAME}.jsonl").exists():
        rows = [
            (line,)
            for line in (layout.logs_dir / f"{_ACTION_LOG_NAME}.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    items: list[dict[str, Any]] = []
    for (payload_json,) in rows:
        try:
            items.append(json.loads(payload_json))
        except json.JSONDecodeError:
            continue
    return items


def _write_action_queue_rows(vault_dir: Path | str, actions: list[dict[str, Any]]) -> None:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    _rewrite_jsonl(layout.logs_dir / f"{_ACTION_LOG_NAME}.jsonl", actions)
    if layout.knowledge_db.exists():
        with sqlite3.connect(layout.knowledge_db) as conn:
            conn.execute("DELETE FROM audit_events WHERE source_log = ?", (_ACTION_LOG_NAME,))
            conn.executemany(
                """
                INSERT INTO audit_events (source_log, event_type, slug, session_id, timestamp, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        _ACTION_LOG_NAME,
                        action["action_kind"],
                        action["action_id"],
                        action.get("session_id", "action-queue"),
                        action["created_at"],
                        json.dumps(action, ensure_ascii=False),
                    )
                    for action in actions
                ],
            )
            conn.commit()


def list_action_queue(
    vault_dir: Path | str,
    *,
    status: str | None = None,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    normalized_query = (query or "").strip().lower()
    items: list[dict[str, Any]] = []
    for item in _read_action_queue_rows(vault_dir):
        if status and item.get("status") != status:
            continue
        if normalized_query:
            haystacks = [
                str(item.get("title") or "").lower(),
                str(item.get("action_kind") or "").lower(),
                str(item.get("target_ref") or "").lower(),
            ]
            if not any(normalized_query in haystack for haystack in haystacks):
                continue
        items.append(item)
        if len(items) >= limit:
            break
    return items


def _signal_by_id(vault_dir: Path | str, signal_id: str) -> dict[str, Any] | None:
    ensure_signal_ledger_synced(vault_dir)
    db_path = _db_path(vault_dir)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT payload_json
            FROM audit_events
            WHERE source_log = ? AND slug = ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (_SIGNAL_LOG_NAME, signal_id),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def enqueue_signal_action(
    vault_dir: Path | str,
    *,
    signal_id: str,
    session_id: str = "ovp-ui",
) -> dict[str, Any]:
    signal = _signal_by_id(vault_dir, signal_id)
    if signal is None:
        raise ValueError("unknown signal_id")
    recommended_action = signal.get("recommended_action")
    if not isinstance(recommended_action, dict) or not recommended_action.get("kind"):
        raise ValueError("signal has no recommended action")
    target_ref = (
        next((path for path in signal.get("note_paths", []) if path), "")
        or next((object_id for object_id in signal.get("object_ids", []) if object_id), "")
        or str(signal.get("source_path") or "")
    )
    payload = {
        "recommended_action": recommended_action,
        "source_path": signal.get("source_path", ""),
        "note_paths": list(signal.get("note_paths", [])),
        "object_ids": list(signal.get("object_ids", [])),
    }
    action_id = _action_id(signal_id, str(recommended_action["kind"]), target_ref, payload)
    existing_actions = _read_action_queue_rows(vault_dir)
    existing = next((item for item in existing_actions if item.get("action_id") == action_id), None)
    if existing is not None:
        return {"created": False, "action": existing}
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    action = {
        "action_id": action_id,
        "action_kind": str(recommended_action["kind"]),
        "source_signal_id": signal_id,
        "title": str(recommended_action.get("label") or signal.get("title") or signal_id),
        "target_ref": target_ref,
        "object_ids": list(signal.get("object_ids", [])),
        "note_paths": list(signal.get("note_paths", [])),
        "status": "queued",
        "created_at": timestamp,
        "started_at": "",
        "finished_at": "",
        "error": "",
        "payload": payload,
        "session_id": session_id,
    }
    existing_actions.append(action)
    existing_actions.sort(key=lambda item: (str(item.get("created_at", "")), str(item.get("action_id", ""))), reverse=True)
    _write_action_queue_rows(vault_dir, existing_actions)
    return {"created": True, "action": action}


def _action_queue_state_map(vault_dir: Path | str) -> dict[str, dict[str, Any]]:
    state_map: dict[str, dict[str, Any]] = {}
    for item in list_action_queue(vault_dir, limit=MAX_PAGE_SIZE):
        signal_id = str(item.get("source_signal_id") or "")
        if signal_id and signal_id not in state_map:
            state_map[signal_id] = item
    return state_map


def _attach_action_queue_state(vault_dir: Path | str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queue_state = _action_queue_state_map(vault_dir)
    annotated: list[dict[str, Any]] = []
    for item in items:
        enriched = dict(item)
        recommended_action = item.get("recommended_action")
        if isinstance(recommended_action, dict):
            action = queue_state.get(str(item.get("signal_id") or ""))
            recommended = dict(recommended_action)
            if action is not None:
                recommended["queue_status"] = action.get("status", "")
                recommended["action_id"] = action.get("action_id", "")
                recommended["queue_path"] = "/actions"
            enriched["recommended_action"] = recommended
        annotated.append(enriched)
    return annotated


def list_production_gaps(
    vault_dir: Path | str,
    *,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    candidate_limit = min(MAX_PAGE_SIZE, max(limit * 5, limit))
    items = list_production_chains(vault_dir, query=query, limit=candidate_limit)
    weak_points: list[dict[str, Any]] = []
    for item in items:
        traceability = item["traceability"]
        missing: list[str] = []
        if item["stage_label"] == "source_note":
            if not traceability["deep_dives"]:
                missing.append("deep dives")
            if not traceability["objects"]:
                missing.append("objects")
            if not traceability["atlas_pages"]:
                missing.append("Atlas / MOC reach")
        else:
            if not traceability["source_notes"]:
                missing.append("source notes")
            if not traceability["objects"]:
                missing.append("objects")
            if not traceability["atlas_pages"]:
                missing.append("Atlas / MOC reach")
        if not missing:
            continue
        weak_points.append(
            {
                "signal_id": _signal_id("production_gap", item["path"]),
                "signal_type": "production_gap",
                "title": item["title"],
                "detail": ", ".join(missing),
                "stage_label": item["stage_label"],
                "note_path": item["path"],
                "missing": missing,
                "severity": len(missing),
                "traceability": item["traceability"],
            }
        )
    weak_points.sort(key=lambda item: (-item["severity"], item["stage_label"], item["title"].lower()))
    return weak_points[:limit]


def _compute_signal_entries(vault_dir: Path | str) -> list[dict[str, Any]]:
    resolved_vault = resolve_vault_dir(vault_dir)
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    signals: list[dict[str, Any]] = []

    for item in list_contradictions(resolved_vault, status="open", limit=MAX_PAGE_SIZE):
        object_ids = list(
            dict.fromkeys(
                claim_id.split("::", 1)[0]
                for claim_id in (item["positive_claim_ids"] + item["negative_claim_ids"])
            )
        )
        signals.append(
            {
                "signal_id": _signal_id("contradiction_open", item["contradiction_id"]),
                "signal_type": "contradiction_open",
                "detected_at": timestamp,
                "status": "active",
                "title": item["subject_key"],
                "detail": (
                    f"{item['scope_summary']['object_count']} objects, "
                    f"{len(item['ranked_evidence'])} ranked evidence rows"
                ),
                "explanation": SIGNAL_TYPE_EXPLANATIONS["contradiction_open"],
                "source_path": "/contradictions",
                "source_label": "Contradictions",
                "object_ids": object_ids,
                "note_paths": [],
                "downstream_effects": [
                    {"label": "Review contradiction", "path": f"/contradictions?q={quote(item['subject_key'], safe='')}"},
                    *[
                        {"label": f"Object: {claim['object_title']}", "path": f"/object?id={claim['object_id']}"}
                        for claim in item["positive_claims"][:1] + item["negative_claims"][:1]
                    ],
                ],
                "recommended_action": _recommended_action(
                    kind="review_contradiction",
                    label="Review contradiction",
                    path=f"/contradictions?q={quote(item['subject_key'], safe='')}",
                    executable=True,
                ),
                "payload": {
                    "contradiction_id": item["contradiction_id"],
                    "scope_summary": item["scope_summary"],
                    "status_bucket": item["status_bucket"],
                },
            }
        )

    for item in list_stale_summaries(resolved_vault, limit=MAX_PAGE_SIZE):
        signals.append(
            {
                "signal_id": _signal_id("stale_summary", item["object_id"]),
                "signal_type": "stale_summary",
                "detected_at": timestamp,
                "status": "active",
                "title": item["title"],
                "detail": ", ".join(item["reason_texts"]),
                "explanation": SIGNAL_TYPE_EXPLANATIONS["stale_summary"],
                "source_path": f"/summaries?q={quote(item['object_id'], safe='')}",
                "source_label": "Stale Summaries",
                "object_ids": [item["object_id"]],
                "note_paths": [],
                "downstream_effects": [
                    {"label": "Open object", "path": item["object_path"]},
                    {"label": "Review stale summary", "path": f"/summaries?q={quote(item['object_id'], safe='')}"},
                ],
                "recommended_action": _recommended_action(
                    kind="rebuild_summary",
                    label="Rebuild summary",
                    path=f"/summaries?q={quote(item['object_id'], safe='')}",
                    executable=True,
                ),
                "payload": {
                    "reason_codes": item["reason_codes"],
                    "latest_event_date": item["latest_event_date"],
                },
            }
        )

    for item in list_production_gaps(resolved_vault, limit=MAX_PAGE_SIZE):
        object_ids = [entry["object_id"] for entry in item["traceability"]["objects"]]
        signals.append(
            {
                "signal_id": item["signal_id"],
                "signal_type": "production_gap",
                "detected_at": timestamp,
                "status": "active",
                "title": item["title"],
                "detail": item["detail"],
                "explanation": SIGNAL_TYPE_EXPLANATIONS["production_gap"],
                "source_path": f"/note?path={quote(item['note_path'], safe='')}",
                "source_label": "Production",
                "object_ids": object_ids,
                "note_paths": [item["note_path"]],
                "downstream_effects": [
                    {"label": "Open note", "path": f"/note?path={quote(item['note_path'], safe='')}"},
                    {"label": "Inspect production chain", "path": f"/production?q={quote(item['title'], safe='')}"},
                ],
                "recommended_action": _recommended_action(
                    kind="inspect_production_gap",
                    label="Inspect production gap",
                    path=f"/production?q={quote(item['title'], safe='')}",
                    executable=False,
                ),
                "payload": {
                    "stage_label": item["stage_label"],
                    "missing": item["missing"],
                    "traceability_counts": item["traceability"]["counts"],
                },
            }
        )

    for item in list_production_chains(resolved_vault, limit=MAX_PAGE_SIZE):
        traceability = item["traceability"]
        if item["stage_label"] == "source_note" and not traceability["deep_dives"]:
            signals.append(
                {
                    "signal_id": _signal_id("source_needs_deep_dive", item["path"]),
                    "signal_type": "source_needs_deep_dive",
                    "detected_at": timestamp,
                    "status": "active",
                    "title": item["title"],
                    "detail": "Processed source note has no derived deep dive yet.",
                    "explanation": SIGNAL_TYPE_EXPLANATIONS["source_needs_deep_dive"],
                    "source_path": f"/note?path={quote(item['path'], safe='')}",
                    "source_label": "Production",
                    "object_ids": [],
                    "note_paths": [item["path"]],
                    "downstream_effects": [
                        {"label": "Open source note", "path": f"/note?path={quote(item['path'], safe='')}"},
                        {"label": "Inspect production chain", "path": f"/production?q={quote(item['title'], safe='')}"},
                    ],
                    "recommended_action": _recommended_action(
                        kind="deep_dive_workflow",
                        label="Create deep dive",
                        path=f"/note?path={quote(item['path'], safe='')}",
                        executable=False,
                    ),
                    "payload": {
                        "stage_label": item["stage_label"],
                        "traceability_counts": traceability["counts"],
                    },
                }
            )
        if item["stage_label"] == "deep_dive" and not traceability["objects"]:
            signals.append(
                {
                    "signal_id": _signal_id("deep_dive_needs_objects", item["path"]),
                    "signal_type": "deep_dive_needs_objects",
                    "detected_at": timestamp,
                    "status": "active",
                    "title": item["title"],
                    "detail": "Deep dive has not produced any evergreen objects yet.",
                    "explanation": SIGNAL_TYPE_EXPLANATIONS["deep_dive_needs_objects"],
                    "source_path": f"/note?path={quote(item['path'], safe='')}",
                    "source_label": "Production",
                    "object_ids": [],
                    "note_paths": [item["path"]],
                    "downstream_effects": [
                        {"label": "Open deep dive", "path": f"/note?path={quote(item['path'], safe='')}"},
                        {"label": "Inspect production chain", "path": f"/production?q={quote(item['title'], safe='')}"},
                    ],
                    "recommended_action": _recommended_action(
                        kind="object_extraction_workflow",
                        label="Extract evergreen objects",
                        path=f"/note?path={quote(item['path'], safe='')}",
                        executable=False,
                    ),
                    "payload": {
                        "stage_label": item["stage_label"],
                        "traceability_counts": traceability["counts"],
                    },
                }
            )

    for item in list_review_actions(resolved_vault, limit=MAX_PAGE_SIZE):
        if item["event_type"] == "ui_contradictions_resolved":
            status = item["status"] or "reviewed"
            signals.append(
                {
                    "signal_id": _signal_id("contradiction_reviewed", f"{item['timestamp']}::{','.join(item['contradiction_ids'])}"),
                    "signal_type": "contradiction_reviewed",
                    "detected_at": item["timestamp"],
                    "status": "active",
                    "title": "Contradiction reviewed",
                    "detail": f"{len(item['contradiction_ids'])} contradictions moved to {status}.",
                    "explanation": SIGNAL_TYPE_EXPLANATIONS["contradiction_reviewed"],
                    "source_path": "/contradictions?status=resolved",
                    "source_label": "Review Actions",
                    "object_ids": item["object_ids"],
                    "note_paths": [],
                    "downstream_effects": [
                        {"label": "Open resolved contradictions", "path": "/contradictions?status=resolved"},
                        *[
                            {"label": f"Object: {object_id}", "path": f"/object?id={object_id}"}
                            for object_id in item["object_ids"][:2]
                        ],
                    ],
                    "recommended_action": _recommended_action(
                        kind="review_resolution",
                        label="Inspect resolved contradictions",
                        path="/contradictions?status=resolved",
                        executable=False,
                    ),
                    "payload": {
                        "event_type": item["event_type"],
                        "contradiction_ids": item["contradiction_ids"],
                        "status": status,
                        "rebuilt_object_ids": item["rebuilt_object_ids"],
                    },
                }
            )
        elif item["event_type"] == "ui_summaries_rebuilt":
            rebuilt_count = item["objects_rebuilt"] or len(item["rebuilt_object_ids"])
            signals.append(
                {
                    "signal_id": _signal_id("summary_rebuilt", f"{item['timestamp']}::{','.join(item['rebuilt_object_ids'])}"),
                    "signal_type": "summary_rebuilt",
                    "detected_at": item["timestamp"],
                    "status": "active",
                    "title": "Summary rebuilt",
                    "detail": f"{rebuilt_count} summaries rebuilt.",
                    "explanation": SIGNAL_TYPE_EXPLANATIONS["summary_rebuilt"],
                    "source_path": "/summaries",
                    "source_label": "Review Actions",
                    "object_ids": item["object_ids"],
                    "note_paths": [],
                    "downstream_effects": [
                        {"label": "Open stale summaries", "path": "/summaries"},
                        *[
                            {"label": f"Object: {object_id}", "path": f"/object?id={object_id}"}
                            for object_id in item["rebuilt_object_ids"][:2]
                        ],
                    ],
                    "recommended_action": _recommended_action(
                        kind="review_rebuilt_summary",
                        label="Inspect rebuilt summaries",
                        path="/summaries",
                        executable=False,
                    ),
                    "payload": {
                        "event_type": item["event_type"],
                        "objects_rebuilt": rebuilt_count,
                        "rebuilt_object_ids": item["rebuilt_object_ids"],
                    },
                }
            )

    signals.sort(key=lambda item: (item["signal_type"], item["title"].lower(), item["signal_id"]))
    return signals


def sync_signal_ledger(vault_dir: Path | str) -> dict[str, Any]:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    signals = _compute_signal_entries(resolved_vault)
    _rewrite_jsonl(layout.logs_dir / f"{_SIGNAL_LOG_NAME}.jsonl", signals)
    if layout.knowledge_db.exists():
        with sqlite3.connect(layout.knowledge_db) as conn:
            conn.execute("DELETE FROM audit_events WHERE source_log = ?", (_SIGNAL_LOG_NAME,))
            conn.executemany(
                """
                INSERT INTO audit_events (source_log, event_type, slug, session_id, timestamp, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        _SIGNAL_LOG_NAME,
                        item["signal_type"],
                        item["signal_id"],
                        "signal-ledger",
                        item["detected_at"],
                        json.dumps(item, ensure_ascii=False),
                    )
                    for item in signals
                ],
            )
            conn.commit()
    type_counts = Counter(item["signal_type"] for item in signals)
    result = {
        "signal_count": len(signals),
        "type_counts": dict(type_counts),
    }
    cache_key = (str(resolved_vault.resolve()), _signal_dependency_signature(resolved_vault))
    _SIGNAL_LEDGER_SYNC_CACHE.clear()
    _SIGNAL_LEDGER_SYNC_CACHE[cache_key] = result
    return result


def ensure_signal_ledger_synced(vault_dir: Path | str) -> dict[str, Any]:
    resolved_vault = resolve_vault_dir(vault_dir)
    cache_key = (str(resolved_vault.resolve()), _signal_dependency_signature(resolved_vault))
    cached = _SIGNAL_LEDGER_SYNC_CACHE.get(cache_key)
    if cached is not None:
        return cached
    result = sync_signal_ledger(resolved_vault)
    _SIGNAL_LEDGER_SYNC_CACHE.clear()
    _SIGNAL_LEDGER_SYNC_CACHE[cache_key] = result
    return result


def list_signals(
    vault_dir: Path | str,
    *,
    signal_type: str | None = None,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    ensure_signal_ledger_synced(vault_dir)
    db_path = _db_path(vault_dir)
    normalized_query = (query or "").strip().lower()
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM audit_events
            WHERE source_log = ?
            ORDER BY timestamp DESC, slug
            """,
            (_SIGNAL_LOG_NAME,),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for (payload_json,) in rows:
        try:
            item = json.loads(payload_json)
        except json.JSONDecodeError:
            continue
        if signal_type and item.get("signal_type") != signal_type:
            continue
        if normalized_query:
            haystacks = [
                str(item.get("title") or "").lower(),
                str(item.get("detail") or "").lower(),
                str(item.get("source_label") or "").lower(),
            ]
            if not any(normalized_query in haystack for haystack in haystacks):
                continue
        items.append(item)
        if len(items) >= limit:
            break
    return _attach_action_queue_state(vault_dir, items)


def get_briefing_snapshot(vault_dir: Path | str, *, limit: int = 8) -> dict[str, Any]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    recent_signals = list_signals(vault_dir, limit=limit)
    unresolved_signal_types = {
        "contradiction_open",
        "stale_summary",
        "production_gap",
        "source_needs_deep_dive",
        "deep_dive_needs_objects",
    }
    unresolved_issues = [item for item in recent_signals if item["signal_type"] in unresolved_signal_types][:limit]
    changed_signals = [
        item
        for item in recent_signals
        if item["signal_type"] in {"contradiction_reviewed", "summary_rebuilt"}
    ]
    changed_object_ids = list(
        dict.fromkeys(
            object_id
            for item in changed_signals
            for object_id in item.get("object_ids", [])
            if object_id
        )
    )
    object_rows = _batch_object_rows(vault_dir, changed_object_ids)
    changed_objects = [
        {
            "object_id": object_id,
            "title": object_rows.get(object_id, {}).get("title", object_id),
            "path": f"/object?id={object_id}",
        }
        for object_id in changed_object_ids[:limit]
    ]

    topic_counts: Counter[str] = Counter()
    for item in recent_signals:
        for object_id in item.get("object_ids", []):
            topic_counts[object_id] += 1
    active_topics = [
        {
            "object_id": object_id,
            "title": object_rows.get(object_id, {}).get("title")
            or _batch_object_rows(vault_dir, [object_id]).get(object_id, {}).get("title")
            or object_id,
            "signal_count": count,
            "path": f"/topic?id={object_id}",
        }
        for object_id, count in topic_counts.most_common(limit)
    ]
    evolution_candidates = list_evolution_candidates(vault_dir, limit=min(MAX_PAGE_SIZE, limit * 3))
    evolution_object_ids = list(
        dict.fromkeys(
            object_id
            for item in evolution_candidates
            for object_id in item.get("object_ids", [])
            if object_id
        )
    )
    evolution_rows = _batch_object_rows(vault_dir, evolution_object_ids)
    insights: list[dict[str, Any]] = []
    for item in evolution_candidates:
        primary_object_id = next((object_id for object_id in item.get("object_ids", []) if object_id), "")
        primary_title = (
            evolution_rows.get(primary_object_id, {}).get("title")
            or object_rows.get(primary_object_id, {}).get("title")
            or str(item.get("subject_id") or primary_object_id)
        )
        insights.append(
            {
                "kind": f"evolution_{item['link_type']}",
                "link_type": item["link_type"],
                "title": str(primary_title),
                "detail": EVOLUTION_LINK_EXPLANATIONS.get(
                    item["link_type"], "Knowledge evolution was detected."
                ),
                "path": "/evolution?link_type="
                + quote(str(item["link_type"]), safe="")
                + "&q="
                + quote(str(primary_title), safe=""),
                "source_paths": [path for path in item.get("source_paths", []) if path][:3],
                "object_ids": list(item.get("object_ids", [])),
                "recommended_action": _recommended_action(
                    kind="review_evolution",
                    label="Review evolution",
                    path="/evolution?link_type="
                    + quote(str(item["link_type"]), safe="")
                    + "&q="
                    + quote(str(primary_title), safe=""),
                    executable=True,
                ),
            }
        )
        if len(insights) >= limit:
            break

    priority_items: list[dict[str, Any]] = []
    for item in unresolved_issues:
        priority_items.append(
            {
                "signal_id": item["signal_id"],
                "kind": item["signal_type"],
                "title": item["title"],
                "detail": item["detail"],
                "path": item["source_path"],
                "source_paths": list(item.get("note_paths", [])),
                "object_ids": list(item.get("object_ids", [])),
                "recommended_action": item.get("recommended_action"),
            }
        )
        if len(priority_items) >= limit:
            break
    seen_priority_keys = {(item["kind"], item["title"], item["path"]) for item in priority_items}
    for item in insights:
        key = (item["kind"], item["title"], item["path"])
        if key in seen_priority_keys:
            continue
        priority_items.append(item)
        seen_priority_keys.add(key)
        if len(priority_items) >= limit:
            break
    first_useful_sign = insights[0] if insights else (priority_items[0] if priority_items else None)

    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "recent_signal_count": len(recent_signals),
        "unresolved_issue_count": len(unresolved_issues),
        "changed_object_count": len(changed_objects),
        "active_topic_count": len(active_topics),
        "recent_signals": recent_signals,
        "unresolved_issues": unresolved_issues,
        "changed_objects": changed_objects,
        "active_topics": active_topics,
        "insight_count": len(insights),
        "priority_item_count": len(priority_items),
        "insights": insights,
        "priority_items": priority_items,
        "first_useful_sign": first_useful_sign,
    }


def get_note_provenance(vault_dir: Path | str, *, note_path: str) -> dict[str, Any]:
    resolved_vault = resolve_vault_dir(vault_dir)
    frontmatter = _read_note_frontmatter(resolved_vault, note_path)
    source_url = str(frontmatter.get("source", "")).strip()
    original_source_note = None
    if source_url:
        original_source_note = _find_note_by_source(
            resolved_vault,
            source_url=source_url,
            exclude_path=note_path,
        )
    if original_source_note is None:
        original_source_note = _find_note_from_pipeline_log(resolved_vault, note_path=note_path)
    derived_deep_dives = _find_derived_notes_from_pipeline_log(resolved_vault, note_path=note_path)
    return {
        "note_path": note_path,
        "original_source_note": original_source_note,
        "derived_deep_dives": derived_deep_dives,
    }


def _page_row_by_path(vault_dir: Path | str, note_path: str) -> dict[str, str]:
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT slug, title, note_type, path
            FROM pages_index
            WHERE path = ?
            LIMIT 1
            """,
            (str((resolved_vault / note_path).resolve()),),
        ).fetchone()
    if row:
        return {
            "slug": row[0],
            "title": row[1],
            "note_type": row[2],
            "path": _vault_relative_path(resolved_vault, row[3]),
        }
    return {
        "slug": Path(note_path).stem,
        "title": Path(note_path).stem,
        "note_type": "note",
        "path": note_path,
    }


def _deep_dive_objects_for_path(vault_dir: Path | str, note_path: str) -> list[dict[str, str]]:
    resolved_vault = resolve_vault_dir(vault_dir)
    normalized_target = str((resolved_vault / note_path).resolve().relative_to(resolved_vault.resolve()))
    return list(_deep_dive_object_map(vault_dir).get(normalized_target, []))


def _atlas_pages_for_object_ids(vault_dir: Path | str, object_ids: list[str]) -> list[dict[str, str]]:
    atlas_pages: dict[str, dict[str, str]] = {}
    for provenance in get_object_provenance_map(vault_dir, object_ids).values():
        for item in provenance["mocs"]:
            atlas_pages.setdefault(item["slug"], item)
    return list(atlas_pages.values())


def _pipeline_log_index(vault_dir: Path) -> dict[str, Any]:
    log_path = VaultLayout.from_vault(vault_dir).logs_dir / "pipeline.jsonl"
    cache_key = (str(vault_dir.resolve()), *(_path_signature(log_path)[1:]))
    cached = _PIPELINE_LOG_INDEX_CACHE.get(cache_key)
    if cached is not None:
        return cached

    article_outputs: dict[str, str] = {}
    derived_by_source_file: dict[str, list[dict[str, str]]] = {}
    archived_by_article_file: dict[str, str] = {}
    if log_path.exists():
        for raw_line in log_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if event.get("event_type") == "article_processed":
                file_name = str(event.get("file", "")).strip()
                output = str(event.get("output", "")).strip()
                if not file_name or not output:
                    continue
                candidate = Path(output)
                if not candidate.is_absolute():
                    candidate = (vault_dir / output).resolve()
                if not candidate.is_file():
                    continue
                relative_path = str(candidate.resolve().relative_to(vault_dir.resolve()))
                article_outputs[relative_path] = file_name
                frontmatter = _parse_frontmatter(candidate.read_text(encoding="utf-8"))
                derived_by_source_file.setdefault(file_name, [])
                if not any(item["path"] == relative_path for item in derived_by_source_file[file_name]):
                    derived_by_source_file[file_name].append(
                        {
                            "title": str(frontmatter.get("title") or candidate.stem).strip(),
                            "path": relative_path,
                        }
                    )
            elif event.get("event_type") == "source_archived_to_processed":
                archived = str(event.get("archived", "")).strip()
                source = str(event.get("source", "")).strip()
                if not archived and not source:
                    continue
                target = archived or source
                article_file = Path(target).name
                candidate = Path(target)
                if not candidate.is_absolute():
                    candidate = (vault_dir / target).resolve()
                if candidate.is_file():
                    archived_by_article_file[article_file] = str(candidate.resolve().relative_to(vault_dir.resolve()))

    original_source_by_output: dict[str, dict[str, str]] = {}
    for output_path, article_file in article_outputs.items():
        archived_path = archived_by_article_file.get(article_file)
        if not archived_path:
            continue
        candidate = (vault_dir / archived_path).resolve()
        if not candidate.is_file():
            continue
        frontmatter = _parse_frontmatter(candidate.read_text(encoding="utf-8"))
        original_source_by_output[output_path] = {
            "title": str(frontmatter.get("title") or candidate.stem).strip(),
            "path": archived_path,
        }

    result = {
        "original_source_by_output": original_source_by_output,
        "derived_by_source_file": derived_by_source_file,
    }
    _PIPELINE_LOG_INDEX_CACHE.clear()
    _PIPELINE_LOG_INDEX_CACHE[cache_key] = result
    return result


def _deep_dive_object_map(vault_dir: Path | str) -> dict[str, list[dict[str, str]]]:
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    cache_key = (str(resolved_vault.resolve()), *(_path_signature(db_path)[1:]))
    cached = _DEEP_DIVE_OBJECT_MAP_CACHE.get(cache_key)
    if cached is not None:
        return cached

    with sqlite3.connect(db_path) as conn:
        deep_dive_rows = conn.execute(
            """
            SELECT path
            FROM pages_index
            WHERE note_type = 'deep_dive'
            ORDER BY slug
            """
        ).fetchall()
        object_rows = conn.execute(
            """
            SELECT object_id, title
            FROM objects
            ORDER BY object_id
            """
        ).fetchall()
        audit_rows = conn.execute(
            """
            SELECT payload_json
            FROM audit_events
            WHERE event_type = 'evergreen_auto_promoted'
            """
        ).fetchall()

    object_titles = {row[0]: row[1] for row in object_rows}
    grouped_promotions: dict[str, dict[str, dict[str, str]]] = {}
    for (payload_json,) in audit_rows:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            continue
        source_name = str(payload.get("source") or "").strip()
        object_id = str(payload.get("mutation", {}).get("target_slug") or payload.get("concept") or "").strip()
        if not source_name or not object_id:
            continue
        grouped_promotions.setdefault(source_name, {})[object_id] = {
            "object_id": object_id,
            "title": object_titles.get(object_id, object_id),
        }

    result: dict[str, list[dict[str, str]]] = {}
    for (path,) in deep_dive_rows:
        relative_path = _vault_relative_path(resolved_vault, path)
        result[relative_path] = sorted(
            grouped_promotions.get(Path(relative_path).name, {}).values(),
            key=lambda item: item["object_id"],
        )

    _DEEP_DIVE_OBJECT_MAP_CACHE.clear()
    _DEEP_DIVE_OBJECT_MAP_CACHE[cache_key] = result
    return result


def _promoted_deep_dives_for_object(vault_dir: Path | str, object_id: str) -> list[dict[str, str]]:
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    with sqlite3.connect(db_path) as conn:
        deep_dive_rows = conn.execute(
            """
            SELECT slug, title, note_type, path
            FROM pages_index
            WHERE note_type = 'deep_dive'
            ORDER BY slug
            """
        ).fetchall()
        audit_rows = conn.execute(
            """
            SELECT payload_json
            FROM audit_events
            WHERE event_type = 'evergreen_auto_promoted'
            """
        ).fetchall()

    promoted_source_names: set[str] = set()
    for (payload_json,) in audit_rows:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            continue
        target_slug = str(payload.get("mutation", {}).get("target_slug") or payload.get("concept") or "").strip()
        source_name = str(payload.get("source") or "").strip()
        if target_slug == object_id and source_name:
            promoted_source_names.add(source_name)

    items: list[dict[str, str]] = []
    seen_slugs: set[str] = set()
    for slug, title, _note_type, path in deep_dive_rows:
        relative_path = _vault_relative_path(resolved_vault, path)
        if Path(relative_path).name not in promoted_source_names:
            continue
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        items.append(
            {
                "slug": str(slug),
                "title": str(title),
                "note_type": "deep_dive",
                "path": relative_path,
            }
        )
    return items


def get_note_traceability(vault_dir: Path | str, *, note_path: str) -> dict[str, Any]:
    note = _page_row_by_path(vault_dir, note_path)
    provenance = get_note_provenance(vault_dir, note_path=note_path)
    deep_dives: list[dict[str, str]] = []
    source_notes: list[dict[str, str]] = []
    objects: list[dict[str, str]] = []
    atlas_pages: list[dict[str, str]] = []

    if note["note_type"] == "deep_dive":
        deep_dives = [note]
        if provenance["original_source_note"]:
            source_notes = [provenance["original_source_note"]]
    elif note["note_type"] == "evergreen":
        object_traceability = get_object_traceability(vault_dir, note["slug"])
        deep_dives = object_traceability["deep_dives"]
        source_notes = object_traceability["source_notes"]
        objects = [
            {
                "object_id": object_traceability["object"]["object_id"],
                "title": object_traceability["object"]["title"],
            }
        ]
        atlas_pages = object_traceability["atlas_pages"]
    else:
        deep_dives = provenance["derived_deep_dives"]
        if provenance["original_source_note"]:
            source_notes = [provenance["original_source_note"]]

    if not objects:
        object_map: dict[str, dict[str, str]] = {}
        for deep_dive in deep_dives:
            for item in _deep_dive_objects_for_path(vault_dir, deep_dive["path"]):
                object_map.setdefault(item["object_id"], item)
        objects = list(object_map.values())
    if not atlas_pages:
        atlas_pages = _atlas_pages_for_object_ids(vault_dir, [item["object_id"] for item in objects])
    return {
        "note": note,
        "source_notes": source_notes,
        "deep_dives": deep_dives,
        "objects": objects,
        "atlas_pages": atlas_pages,
        "counts": {
            "source_notes": len(source_notes),
            "deep_dives": len(deep_dives),
            "objects": len(objects),
            "atlas_pages": len(atlas_pages),
        },
    }


def get_object_traceability(vault_dir: Path | str, object_id: str) -> dict[str, Any]:
    detail = get_object_detail(vault_dir, object_id)
    deep_dives = _promoted_deep_dives_for_object(vault_dir, object_id)
    source_note_map: dict[str, dict[str, str]] = {}
    for deep_dive in deep_dives:
        original = get_note_provenance(vault_dir, note_path=deep_dive["path"])["original_source_note"]
        if original:
            source_note_map.setdefault(original["path"], original)
    return {
        "object": detail["object"],
        "evergreen_note": {
            "title": detail["object"]["title"],
            "path": detail["provenance"]["evergreen_path"],
        },
        "source_notes": list(source_note_map.values()),
        "deep_dives": deep_dives,
        "atlas_pages": detail["provenance"]["mocs"],
        "counts": {
            "source_notes": len(source_note_map),
            "deep_dives": len(deep_dives),
            "atlas_pages": len(detail["provenance"]["mocs"]),
        },
    }


def list_production_chains(
    vault_dir: Path | str,
    *,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    normalized_query = (query or "").strip().lower()
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT slug, title, note_type, path
            FROM pages_index
            WHERE note_type = 'deep_dive'
            ORDER BY note_type, slug
            """
        ).fetchall()

    candidates: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for slug, title, note_type, path in rows:
        relative_path = _vault_relative_path(resolved_vault, path)
        seen_paths.add(relative_path)
        candidates.append(
            {
                "slug": str(slug),
                "title": str(title),
                "note_type": str(note_type),
                "path": relative_path,
                "stage_label": "deep_dive",
            }
        )

    processed_root = resolved_vault / "50-Inbox" / "03-Processed"
    if processed_root.exists():
        for candidate in sorted(processed_root.rglob("*.md")):
            relative_path = str(candidate.resolve().relative_to(resolved_vault.resolve()))
            if relative_path in seen_paths:
                continue
            frontmatter = _parse_frontmatter(candidate.read_text(encoding="utf-8"))
            candidates.append(
                {
                    "slug": candidate.stem,
                    "title": str(frontmatter.get("title") or candidate.stem).strip(),
                    "note_type": "note",
                    "path": relative_path,
                    "stage_label": "source_note",
                }
            )

    items: list[dict[str, Any]] = []
    for candidate in candidates:
        relative_path = candidate["path"]
        chain = get_note_traceability(vault_dir, note_path=relative_path)
        if normalized_query:
            haystacks = [
                str(candidate["title"]).lower(),
                str(candidate["slug"]).lower(),
                relative_path.lower(),
                *(item["title"].lower() for item in chain["deep_dives"]),
                *(item["title"].lower() for item in chain["objects"]),
                *(item["title"].lower() for item in chain["atlas_pages"]),
                *(item["title"].lower() for item in chain["source_notes"]),
            ]
            if not any(normalized_query in haystack for haystack in haystacks):
                continue
        items.append(
            {
                "slug": candidate["slug"],
                "title": candidate["title"],
                "note_type": candidate["note_type"],
                "path": relative_path,
                "stage_label": candidate["stage_label"],
                "traceability": chain,
            }
        )
        if len(items) >= limit:
            break
    return items


def list_contradictions(
    vault_dir: Path | str,
    *,
    limit: int = 100,
    status: str | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    db_path = _db_path(vault_dir)
    normalized_query = _escape_like(query.strip().lower()) if query else ""
    query = """
        SELECT contradiction_id, subject_key, positive_claim_ids_json, negative_claim_ids_json, status, resolution_note, resolved_at
        FROM contradictions
    """
    params: list[Any] = []
    where_clauses: list[str] = []
    if status:
        if status == "resolved":
            where_clauses.append("status != ?")
            params.append("open")
        else:
            where_clauses.append("status = ?")
            params.append(status)
    if normalized_query:
        where_clauses.append("lower(subject_key) LIKE ? ESCAPE '\\'")
        params.append(f"%{normalized_query}%")
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)
    query += " ORDER BY subject_key LIMIT ?"
    params.append(limit)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()

    items = [
        {
            "contradiction_id": row[0],
            "subject_key": row[1],
            "positive_claim_ids": json.loads(row[2]),
            "negative_claim_ids": json.loads(row[3]),
            "status": row[4],
            "resolution_note": row[5] or "",
            "resolved_at": row[6] or "",
        }
        for row in rows
    ]
    claim_map = _claim_details_map(
        vault_dir,
        [
            claim_id
            for item in items
            for claim_id in (item["positive_claim_ids"] + item["negative_claim_ids"])
        ],
    )
    evidence_map = _claim_evidence_map(
        vault_dir,
        [
            claim_id
            for item in items
            for claim_id in (item["positive_claim_ids"] + item["negative_claim_ids"])
        ],
    )
    for item in items:
        object_ids = list(
            dict.fromkeys(
                claim_id.split("::", 1)[0]
                for claim_id in (item["positive_claim_ids"] + item["negative_claim_ids"])
            )
        )
        item["positive_claims"] = [
            {
                **claim_map[claim_id],
                "evidence": evidence_map.get(claim_id, []),
            }
            for claim_id in item["positive_claim_ids"]
            if claim_id in claim_map
        ]
        item["negative_claims"] = [
            {
                **claim_map[claim_id],
                "evidence": evidence_map.get(claim_id, []),
            }
            for claim_id in item["negative_claim_ids"]
            if claim_id in claim_map
        ]
        item["detection_model"] = "page_summary_polarity"
        item["detection_confidence"] = "heuristic"
        item["status_bucket"] = "open" if item["status"] == "open" else "reviewed"
        item["status_explanation"] = CONTRADICTION_STATUS_EXPLANATIONS.get(
            item["status"],
            "Reviewed contradiction state.",
        )
        item["scope_summary"] = {
            "object_count": len(object_ids),
            "positive_claim_count": len(item["positive_claims"]),
            "negative_claim_count": len(item["negative_claims"]),
            "source_note_count": len(
                {
                    evidence["source_slug"]
                    for claim in item["positive_claims"] + item["negative_claims"]
                    for evidence in claim["evidence"]
                }
            ),
        }
        item["ranked_evidence"] = _rank_contradiction_evidence(item)
        item["review_history"] = list_review_actions(vault_dir, object_ids=object_ids, limit=5)
    return items


def _eligible_evolution_object_ids(vault_dir: Path | str) -> list[str]:
    promoted_object_ids = {
        item["object_id"]
        for objects in _deep_dive_object_map(vault_dir).values()
        for item in objects
        if item.get("object_id")
    }
    existing_object_ids = {item["object_id"] for item in list_objects(vault_dir, limit=MAX_PAGE_SIZE)}
    return sorted(promoted_object_ids.intersection(existing_object_ids))


def _compute_evolution_candidates(
    vault_dir: Path | str,
    *,
    object_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    normalized_object_ids = list(dict.fromkeys(object_id for object_id in (object_ids or []) if object_id))
    scoped_object_id_set = set(normalized_object_ids)

    open_contradictions = list_contradictions(vault_dir, limit=MAX_PAGE_SIZE, status="open")
    if scoped_object_id_set:
        open_contradictions = [
            item
            for item in open_contradictions
            if scoped_object_id_set.intersection(
                claim["object_id"]
                for claim in (item["positive_claims"] + item["negative_claims"])
            )
        ]
    contradiction_object_ids = sorted(
        {
            claim["object_id"]
            for item in open_contradictions
            for claim in (item["positive_claims"] + item["negative_claims"])
        }
    )
    contradiction_object_paths = _batch_object_rows(vault_dir, contradiction_object_ids)
    contradiction_source_paths = _page_paths_for_slugs(
        vault_dir,
        [
            evidence["source_slug"]
            for item in open_contradictions
            for claim in (item["positive_claims"] + item["negative_claims"])
            for evidence in claim["evidence"]
            if evidence.get("source_slug")
        ],
    )

    for item in open_contradictions:
        positive_claims = list(item["positive_claims"])
        negative_claims = list(item["negative_claims"])
        if not positive_claims or not negative_claims:
            continue
        contradiction_item_object_ids = sorted(
            {
                claim["object_id"]
                for claim in (positive_claims + negative_claims)
            }
        )

        claim_dates: dict[str, tuple[tuple[int, float, str], str]] = {}
        for claim in positive_claims + negative_claims:
            canonical_path = contradiction_object_paths.get(claim["object_id"], {}).get("canonical_path", "")
            date_text = _note_date_text(vault_dir, canonical_path) if canonical_path else ""
            claim_dates[claim["claim_id"]] = (_note_date_sort_key(date_text), date_text)

        positive_claim = positive_claims[0]
        negative_claim = negative_claims[0]
        best_pair_score: tuple[int, float, str, str] | None = None
        for positive_candidate in positive_claims:
            positive_key, _positive_date = claim_dates[positive_candidate["claim_id"]]
            for negative_candidate in negative_claims:
                negative_key, _negative_date = claim_dates[negative_candidate["claim_id"]]
                both_valid = 1 if positive_key[0] and negative_key[0] else 0
                distance = abs(positive_key[1] - negative_key[1]) if both_valid else 0.0
                pair_score = (
                    both_valid,
                    distance,
                    positive_candidate["claim_id"],
                    negative_candidate["claim_id"],
                )
                if best_pair_score is None or pair_score > best_pair_score:
                    best_pair_score = pair_score
                    positive_claim = positive_candidate
                    negative_claim = negative_candidate

        positive_key, positive_date = claim_dates[positive_claim["claim_id"]]
        negative_key, negative_date = claim_dates[negative_claim["claim_id"]]
        if positive_key > negative_key:
            earlier_claim, later_claim = negative_claim, positive_claim
            earlier_date, later_date = negative_date, positive_date
        else:
            earlier_claim, later_claim = positive_claim, negative_claim
            earlier_date, later_date = positive_date, negative_date

        record = {
            "evolution_id": _candidate_evolution_id(
                link_type="challenges",
                subject_kind="topic",
                subject_id=item["subject_key"],
                earlier_ref=f"claim://{earlier_claim['claim_id']}",
                later_ref=f"claim://{later_claim['claim_id']}",
            ),
            "status": "candidate",
            "link_type": "challenges",
            "subject_kind": "topic",
            "subject_id": item["subject_key"],
            "object_ids": contradiction_item_object_ids,
            "earlier_ref": f"claim://{earlier_claim['claim_id']}",
            "later_ref": f"claim://{later_claim['claim_id']}",
            "earlier_date": earlier_date,
            "later_date": later_date,
            "reason_codes": ["open_contradiction", "claim_polarity_divergence"],
            "confidence": 0.9,
            "evidence": item["ranked_evidence"][:4],
            "source_paths": [
                path
                for path in dict.fromkeys(
                    [
                        *(
                            contradiction_source_paths.get(evidence["source_slug"], "")
                            for claim in (item["positive_claims"] + item["negative_claims"])
                            for evidence in claim["evidence"]
                            if evidence.get("source_slug")
                        ),
                        *(
                            contradiction_object_paths.get(object_id, {}).get("canonical_path", "")
                            for object_id in contradiction_item_object_ids
                        ),
                    ]
                )
                if path
            ],
        }
        candidates.append(record)

    stale_summaries = list_stale_summaries(
        vault_dir,
        object_ids=normalized_object_ids or None,
        limit=MAX_PAGE_SIZE,
    )
    for item in stale_summaries:
        traceability = get_object_traceability(vault_dir, item["object_id"])
        earlier_path = traceability["object"]["canonical_path"]
        earlier_date = _note_date_text(vault_dir, earlier_path)
        earlier_key = _note_date_sort_key(earlier_date)
        later_choice: dict[str, str] | None = None
        later_choice_key: tuple[int, float, str] | None = None
        for note in [*traceability["deep_dives"], *traceability["source_notes"]]:
            if not _has_supersession_cue(vault_dir, note["path"]):
                continue
            candidate_date = _note_date_text(vault_dir, note["path"])
            candidate_key = _note_date_sort_key(candidate_date)
            if candidate_key <= earlier_key:
                continue
            if later_choice_key is None or candidate_key > later_choice_key:
                later_choice = note
                later_choice_key = candidate_key
        if later_choice is None:
            continue
        later_ref = f"{later_choice.get('note_type') or 'note'}://{later_choice['path']}"
        later_date = _note_date_text(vault_dir, later_choice["path"])
        record = {
            "evolution_id": _candidate_evolution_id(
                link_type="replaces",
                subject_kind="object",
                subject_id=item["object_id"],
                earlier_ref=f"object://{item['object_id']}",
                later_ref=later_ref,
            ),
            "status": "candidate",
            "link_type": "replaces",
            "subject_kind": "object",
            "subject_id": item["object_id"],
            "object_ids": [item["object_id"]],
            "earlier_ref": f"object://{item['object_id']}",
            "later_ref": later_ref,
            "earlier_date": earlier_date,
            "later_date": later_date,
            "reason_codes": ["stale_summary", "later_traceability_neighbor"],
            "confidence": 0.8,
            "evidence": [
                *[
                    {"kind": "stale_summary_reason", "code": code, "text": text}
                    for code, text in zip(item["reason_codes"], item["reason_texts"], strict=False)
                ],
                {
                    "kind": "later_traceability_neighbor",
                    "title": later_choice["title"],
                    "path": later_choice["path"],
                    "date": later_date,
                },
            ],
            "source_paths": [
                path
                for path in dict.fromkeys(
                    [
                        earlier_path,
                        *[note["path"] for note in traceability["deep_dives"]],
                        *[note["path"] for note in traceability["source_notes"]],
                    ]
                )
                if path
            ],
        }
        candidates.append(record)

    candidate_object_ids = normalized_object_ids or _eligible_evolution_object_ids(vault_dir)
    for object_id in candidate_object_ids:
        traceability = get_object_traceability(vault_dir, object_id)
        earlier_path = traceability["object"]["canonical_path"]
        earlier_date = _note_date_text(vault_dir, earlier_path)
        earlier_key = _note_date_sort_key(earlier_date)
        for note in [*traceability["deep_dives"], *traceability["source_notes"]]:
            later_date = _note_date_text(vault_dir, note["path"])
            later_key = _note_date_sort_key(later_date)
            if later_key <= earlier_key:
                continue
            if _has_supersession_cue(vault_dir, note["path"]):
                continue
            inferred_link_type = "confirms" if _has_confirmation_cue(vault_dir, note["path"]) else "enriches"
            record = {
                "evolution_id": _candidate_evolution_id(
                    link_type=inferred_link_type,
                    subject_kind="object",
                    subject_id=object_id,
                    earlier_ref=f"object://{object_id}",
                    later_ref=f"{note.get('note_type') or 'note'}://{note['path']}",
                ),
                "status": "candidate",
                "link_type": inferred_link_type,
                "subject_kind": "object",
                "subject_id": object_id,
                "object_ids": [object_id],
                "earlier_ref": f"object://{object_id}",
                "later_ref": f"{note.get('note_type') or 'note'}://{note['path']}",
                "earlier_date": earlier_date,
                "later_date": later_date,
                "reason_codes": ["later_traceability_neighbor", f"lexical_{inferred_link_type}" if inferred_link_type == "confirms" else "later_context"],
                "confidence": 0.7 if inferred_link_type == "confirms" else 0.6,
                "evidence": [
                    {
                        "kind": "later_traceability_neighbor",
                        "title": note["title"],
                        "path": note["path"],
                        "date": later_date,
                    }
                ],
                "source_paths": [path for path in dict.fromkeys([earlier_path, note["path"]]) if path],
            }
            candidates.append(record)

    candidates.sort(
        key=lambda item: (
            str(item["later_date"]),
            str(item["subject_id"]),
            str(item["link_type"]),
            str(item["evolution_id"]),
        ),
        reverse=True,
    )
    unique_candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in candidates:
        if item["evolution_id"] in seen_ids:
            continue
        seen_ids.add(item["evolution_id"])
        unique_candidates.append(item)
    return unique_candidates


def _all_evolution_candidates(
    vault_dir: Path | str,
    *,
    object_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    resolved_vault = resolve_vault_dir(vault_dir)
    normalized_object_ids = tuple(dict.fromkeys(object_id for object_id in (object_ids or []) if object_id))
    cache_key = (
        str(resolved_vault.resolve()),
        _evolution_dependency_signature(resolved_vault),
        normalized_object_ids,
    )
    cached = _EVOLUTION_CANDIDATE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    result = _compute_evolution_candidates(resolved_vault, object_ids=list(normalized_object_ids))
    _EVOLUTION_CANDIDATE_CACHE[cache_key] = result
    return result


def list_evolution_candidates(
    vault_dir: Path | str,
    *,
    object_ids: list[str] | None = None,
    query: str | None = None,
    link_type: str | None = None,
    status: str = "candidate",
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    limit, offset = _validate_page_args(limit=limit, offset=offset)
    if status != "candidate":
        return []
    normalized_query = (query or "").strip().lower()
    unique_candidates = [
        item
        for item in _all_evolution_candidates(vault_dir, object_ids=object_ids)
        if (not link_type or item["link_type"] == link_type)
        and (not normalized_query or _evolution_candidate_matches_query(item, normalized_query))
    ]
    return unique_candidates[offset : offset + limit]


def list_evolution_links(
    vault_dir: Path | str,
    *,
    object_ids: list[str] | None = None,
    query: str | None = None,
    link_type: str | None = None,
    status: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    normalized_query = (query or "").strip().lower()
    latest_by_evolution_id: dict[str, dict[str, Any]] = {}
    for action in list_evolution_review_actions(vault_dir, object_ids=object_ids):
        evolution_id = str(action.get("evolution_id") or "")
        if not evolution_id or evolution_id in latest_by_evolution_id:
            continue
        latest_by_evolution_id[evolution_id] = action
    items = list(latest_by_evolution_id.values())
    filtered: list[dict[str, Any]] = []
    for item in items:
        if status and item.get("status") != status:
            continue
        if link_type and item.get("link_type") != link_type:
            continue
        if normalized_query and not _evolution_candidate_matches_query(item, normalized_query):
            continue
        filtered.append(item)
    if limit is not None:
        limit, _ = _validate_page_args(limit=limit, offset=0)
        return filtered[:limit]
    return filtered


def review_evolution_candidate(
    vault_dir: Path | str,
    *,
    evolution_id: str,
    status: str,
    note: str = "",
    link_type: str | None = None,
) -> dict[str, Any]:
    if not evolution_id:
        raise ValueError("missing evolution_id")
    if status not in {"accepted", "rejected"}:
        raise ValueError("invalid evolution status")
    if link_type and link_type not in {"replaces", "enriches", "confirms", "challenges"}:
        raise ValueError("invalid evolution link_type")
    candidate = next(
        (item for item in _all_evolution_candidates(vault_dir) if item["evolution_id"] == evolution_id),
        None,
    )
    if candidate is None:
        raise ValueError("unknown evolution candidate")
    final_link_type = link_type or str(candidate["link_type"])
    payload = {
        "object_ids": list(candidate.get("object_ids", [])),
        "evolution_id": candidate["evolution_id"],
        "status": status,
        "note": note,
        "link_type": final_link_type,
        "candidate_link_type": candidate["link_type"],
        "subject_kind": candidate["subject_kind"],
        "subject_id": candidate["subject_id"],
        "earlier_ref": candidate["earlier_ref"],
        "later_ref": candidate["later_ref"],
    }
    record_review_action(
        vault_dir,
        event_type="ui_evolution_reviewed",
        slug=str(candidate["subject_id"]),
        payload=payload,
    )
    return {
        "reviewed_count": 1,
        "accepted_count": 1 if status == "accepted" else 0,
        "rejected_count": 1 if status == "rejected" else 0,
        "evolution_ids": [candidate["evolution_id"]],
        "candidate_count": 1,
        "status": status,
    }


def get_topic_neighborhood(vault_dir: Path | str, object_id: str, *, depth: int = 1) -> dict[str, Any]:
    if depth != 1:
        raise ValueError("Only depth=1 is currently supported")

    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    with sqlite3.connect(db_path) as conn:
        center = conn.execute(
            """
            SELECT object_id, object_kind, title, canonical_path, source_slug
            FROM objects
            WHERE object_id = ?
            """,
            (object_id,),
        ).fetchone()
        if center is None:
            raise ValueError(f"Unknown object_id: {object_id}")

        edge_rows = conn.execute(
            """
            SELECT source_object_id, target_object_id, relation_type, evidence_source_slug
            FROM relations
            WHERE source_object_id = ?
            ORDER BY target_object_id
            """,
            (object_id,),
        ).fetchall()
        neighbor_ids = [row[1] for row in edge_rows]
        if neighbor_ids:
            placeholders = ",".join("?" for _ in neighbor_ids)
            neighbor_rows = conn.execute(
                f"""
                SELECT object_id, object_kind, title, canonical_path, source_slug
                FROM objects
                WHERE object_id IN ({placeholders})
                ORDER BY object_id
                """,
                tuple(neighbor_ids),
            ).fetchall()
        else:
            neighbor_rows = []

    return {
        "center": {
            "object_id": center[0],
            "object_kind": center[1],
            "title": center[2],
            "canonical_path": _vault_relative_path(resolved_vault, center[3]),
            "source_slug": center[4],
        },
        "neighbors": [
            {
                "object_id": row[0],
                "object_kind": row[1],
                "title": row[2],
                "canonical_path": _vault_relative_path(resolved_vault, row[3]),
                "source_slug": row[4],
            }
            for row in neighbor_rows
        ],
        "edges": [
            {
                "source_object_id": row[0],
                "target_object_id": row[1],
                "relation_type": row[2],
                "evidence_source_slug": row[3],
            }
            for row in edge_rows
        ],
    }


def list_atlas_memberships(
    vault_dir: Path | str,
    *,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    items = _list_surface_groups(
        vault_dir,
        note_type="moc",
        query=query,
        limit=limit,
        object_list_key="members",
    )
    return [
        {
            "slug": item["slug"],
            "title": item["title"],
            "path": item["path"],
            "members": item["members"],
        }
        for item in items
    ]


def list_deep_dive_derivations(
    vault_dir: Path | str,
    *,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    db_path = _db_path(vault_dir)
    resolved_vault = resolve_vault_dir(vault_dir)
    normalized_query = (query or "").strip().lower()

    with sqlite3.connect(db_path) as conn:
        deep_dive_rows = conn.execute(
            """
            SELECT slug, title, note_type, path
            FROM pages_index
            WHERE note_type = 'deep_dive'
            ORDER BY slug
            """
        ).fetchall()

    derivation_map = _deep_dive_object_map(vault_dir)

    items: list[dict[str, Any]] = []
    for slug, title, note_type, path in deep_dive_rows:
        relative_path = _vault_relative_path(resolved_vault, path)
        derived_objects = list(derivation_map.get(relative_path, []))
        if normalized_query:
            haystacks = [
                slug.lower(),
                title.lower(),
                relative_path.lower(),
                *(
                    value.lower()
                    for item in derived_objects
                    for value in (item["object_id"], item["title"])
                ),
            ]
            if not any(normalized_query in haystack for haystack in haystacks):
                continue
        items.append(
            {
                "slug": slug,
                "title": title,
                "note_type": note_type,
                "path": relative_path,
                "derived_objects": sorted(derived_objects, key=lambda item: item["object_id"]),
            }
        )
        if len(items) >= limit:
            break
    return items


def list_stale_summaries(
    vault_dir: Path | str,
    *,
    query: str | None = None,
    object_ids: list[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit, _ = _validate_page_args(limit=limit, offset=0)
    db_path = _db_path(vault_dir)
    normalized_query = _escape_like(query.strip().lower()) if query else ""
    sql = """
        SELECT objects.object_id, objects.title, compiled_summaries.summary_text,
               COALESCE(rel.outgoing_count, 0) AS outgoing_count
        FROM objects
        LEFT JOIN compiled_summaries ON compiled_summaries.object_id = objects.object_id
        LEFT JOIN (
            SELECT source_object_id, COUNT(*) AS outgoing_count
            FROM relations
            GROUP BY source_object_id
        ) AS rel ON rel.source_object_id = objects.object_id
    """
    params: list[Any] = []
    where_clauses: list[str] = []
    if object_ids:
        normalized_object_ids = list(dict.fromkeys(object_id for object_id in object_ids if object_id))
        if not normalized_object_ids:
            return []
        placeholders = ",".join("?" for _ in normalized_object_ids)
        where_clauses.append(f"objects.object_id IN ({placeholders})")
        params.extend(normalized_object_ids)
    if normalized_query:
        where_clauses.append(
            """
            (
                lower(objects.object_id) LIKE ? ESCAPE '\\'
                OR lower(objects.title) LIKE ? ESCAPE '\\'
                OR lower(compiled_summaries.summary_text) LIKE ? ESCAPE '\\'
            )
            """.strip()
        )
        params.extend([f"%{normalized_query}%"] * 3)
    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)
    sql += " ORDER BY objects.object_id LIMIT ?"
    params.append(limit)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        latest_event_rows = conn.execute(
            """
            SELECT slug, MAX(event_date)
            FROM timeline_events
            GROUP BY slug
            """
        ).fetchall()
    latest_event_map = {str(slug): str(event_date or "") for slug, event_date in latest_event_rows}

    items: list[dict[str, Any]] = []
    for object_id, title, summary_text, outgoing_count in rows:
        summary = str(summary_text or "").strip()
        if outgoing_count > 0:
            continue
        if len(summary) >= 40 and summary.lower() != str(title).strip().lower():
            continue
        reason_codes: list[str] = ["no_outgoing_relations"]
        reason_texts: list[str] = ["No outgoing relations currently support this summary."]
        if not summary:
            reason_codes.append("summary_missing")
            reason_texts.append("Compiled summary is empty.")
        elif len(summary) < 40:
            reason_codes.append("summary_too_short")
            reason_texts.append("Compiled summary is too short to stand on its own.")
        if summary and summary.lower() == str(title).strip().lower():
            reason_codes.append("summary_repeats_title")
            reason_texts.append("Compiled summary repeats the title instead of adding substance.")
        items.append(
            {
                "object_id": str(object_id),
                "title": str(title),
                "summary_text": summary,
                "outgoing_relation_count": int(outgoing_count or 0),
                "object_path": f"/object?id={object_id}",
                "reason_codes": reason_codes,
                "reason_texts": reason_texts,
                "review_history": list_review_actions(vault_dir, object_ids=[str(object_id)], limit=5),
                "latest_event_date": latest_event_map.get(str(object_id), ""),
            }
        )
    return items
