from __future__ import annotations

from array import array
from datetime import datetime, timezone
import hashlib
from io import TextIOBase
import json
import logging
import math
import re
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from .concept_registry import ConceptRegistry, ResolutionAction
from .event_emitter import iter_for_index
from .graph.frontmatter import FrontmatterParser, NoteMetadata
from .graph.link_parser import LinkParser
from .identity import canonicalize_note_id
from .packs.loader import DEFAULT_WORKFLOW_PACK_NAME
from .projection_lifecycle import close_projection_repair_marker, write_projection_repair_marker
from .runtime import VaultLayout, knowledge_db_write_lock, resolve_vault_dir
from .truth_projection_registry import execute_truth_projection_builder, resolve_truth_projection_builder
from .truth_store import TRUTH_STORE_SCHEMA

SUMMARY_MAX_LEN = 320
SUMMARY_RELATED_LIMIT = 3
AUTHORITY_SCHEMA_VERSION = 1
KNOWLEDGE_DB_PROJECTION_KIND = "knowledge_db"
KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION = 5


_FTS_QUERY_SCRUB = re.compile(r"[^\w\u4e00-\u9fff]+", flags=re.UNICODE)


def sanitize_fts_query(query_text: str) -> str:
    """Strip FTS5 syntax characters (`-`, `:`, `"`, etc.) from free-text input
    so prose like ``multi-step`` doesn't parse as ``multi NOT step`` and crash
    with ``no such column: step``. Keeps alphanumerics + CJK and collapses
    whitespace; returns ``""`` when nothing usable remains."""
    cleaned = _FTS_QUERY_SCRUB.sub(" ", query_text or "")
    return " ".join(cleaned.split())


SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE pages_index (
  slug TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  note_type TEXT NOT NULL,
  path TEXT NOT NULL,
  day_id TEXT NOT NULL,
  frontmatter_json TEXT NOT NULL,
  body TEXT NOT NULL
);

-- Trigram tokenizer gives substring-style matching that works for English
-- and CJK alike (the default unicode61 tokenizer treats consecutive Chinese
-- characters as one opaque token, missing every mid-sentence query).
CREATE VIRTUAL TABLE page_fts USING fts5(
  slug UNINDEXED,
  title,
  body,
  tokenize='trigram'
);

CREATE TABLE page_links (
  source_slug TEXT NOT NULL,
  target_slug TEXT NOT NULL,
  target_raw TEXT NOT NULL DEFAULT '',
  link_type TEXT NOT NULL,
  line_number INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_page_links_source ON page_links(source_slug);
CREATE INDEX idx_page_links_target ON page_links(target_slug);

CREATE TABLE raw_data (
  slug TEXT NOT NULL,
  source_name TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  source_path TEXT NOT NULL,
  PRIMARY KEY (slug, source_name, source_path)
);

CREATE TABLE timeline_events (
  slug TEXT NOT NULL,
  event_date TEXT NOT NULL,
  event_type TEXT NOT NULL,
  heading TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_timeline_events_slug ON timeline_events(slug);
CREATE INDEX idx_timeline_events_date ON timeline_events(event_date);

CREATE TABLE audit_events (
  source_log TEXT NOT NULL,
  event_type TEXT NOT NULL,
  slug TEXT NOT NULL DEFAULT '',
  session_id TEXT NOT NULL DEFAULT '',
  timestamp TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL
);

CREATE INDEX idx_audit_events_log ON audit_events(source_log);
CREATE INDEX idx_audit_events_type ON audit_events(event_type);

CREATE TABLE page_embeddings (
  slug TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  section_title TEXT NOT NULL,
  chunk_text TEXT NOT NULL,
  embedding_blob BLOB NOT NULL,
  embedding_model TEXT NOT NULL,
  PRIMARY KEY (slug, chunk_index)
);

CREATE INDEX idx_page_embeddings_slug ON page_embeddings(slug);

CREATE TABLE page_metrics (
  slug TEXT PRIMARY KEY,
  last_seen_ts INTEGER NOT NULL DEFAULT 0,
  reuse_count INTEGER NOT NULL DEFAULT 0,
  citation_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_page_metrics_last_seen ON page_metrics(last_seen_ts);

CREATE TABLE projection_metadata (
  projection_kind TEXT PRIMARY KEY,
  authority_schema_version INTEGER NOT NULL,
  projection_schema_version INTEGER NOT NULL,
  built_at TEXT NOT NULL
);

CREATE TABLE entity_mentions (
  entity_slug TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  source_slug TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  detection_method TEXT NOT NULL DEFAULT 'wikilink',
  mention_text TEXT NOT NULL DEFAULT '',
  snippet TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_entity_mentions_entity ON entity_mentions(entity_slug);
CREATE INDEX idx_entity_mentions_source ON entity_mentions(source_slug);
CREATE INDEX idx_entity_mentions_type ON entity_mentions(entity_type);
"""

SCHEMA += "\n" + TRUTH_STORE_SCHEMA

from .embedding import (
    assert_consistent_with as _assert_embedding_consistent,
    embed_text as _embed_text_semantic,
    get_dimensions,
    get_model_name,
)
TRUTH_PROJECTION_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "objects": ("pack", "object_id", "object_kind", "title", "canonical_path", "source_slug"),
    "claims": ("pack", "claim_id", "object_id", "claim_kind", "claim_text", "confidence"),
    "claim_evidence": (
        "pack",
        "claim_id",
        "source_slug",
        "evidence_kind",
        "quote_text",
        "locator",
        "content_hash",
        "retrieval_context",
        "quote_start_line",
        "quote_end_line",
        "quote_start_char",
        "quote_end_char",
        "status",
        "verified_at",
    ),
    "relations": (
        "pack",
        "source_object_id",
        "target_object_id",
        "relation_type",
        "evidence_source_slug",
        "quote_text",
        "locator",
        "content_hash",
        "retrieval_context",
        "quote_start_line",
        "quote_end_line",
        "quote_start_char",
        "quote_end_char",
        "status",
        "verified_at",
    ),
    "compiled_summaries": ("pack", "object_id", "summary_text", "source_slug"),
    "contradictions": (
        "pack",
        "contradiction_id",
        "subject_key",
        "positive_claim_ids_json",
        "negative_claim_ids_json",
        "status",
        "resolution_note",
        "resolved_at",
    ),
    "graph_edges": (
        "pack",
        "edge_id",
        "source_object_id",
        "target_object_id",
        "edge_kind",
        "weight",
        "evidence_source_slug",
    ),
    "graph_clusters": (
        "pack",
        "cluster_id",
        "cluster_kind",
        "label",
        "center_object_id",
        "member_object_ids_json",
        "score",
    ),
}


def _truth_pack_name(pack_name: str | None = None) -> str:
    return str(pack_name or DEFAULT_WORKFLOW_PACK_NAME)


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_authority_schema_version(vault_dir: Path | str) -> int:
    path = resolve_vault_dir(vault_dir) / ".ovp" / "schema_version"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{AUTHORITY_SCHEMA_VERSION}\n", encoding="utf-8")
        return AUTHORITY_SCHEMA_VERSION
    try:
        return int(path.read_text(encoding="utf-8").strip() or "0")
    except ValueError:
        path.write_text(f"{AUTHORITY_SCHEMA_VERSION}\n", encoding="utf-8")
        return AUTHORITY_SCHEMA_VERSION


def _read_knowledge_db_projection_metadata(db_path: Path) -> tuple[int, int] | None:
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT authority_schema_version, projection_schema_version
                FROM projection_metadata
                WHERE projection_kind = ?
                """,
                (KNOWLEDGE_DB_PROJECTION_KIND,),
            ).fetchone()
    except sqlite3.DatabaseError:
        return None
    if row is None:
        return None
    return int(row[0] or 0), int(row[1] or 0)


def _projection_metadata(pack_name: str) -> tuple[str, str]:
    try:
        spec = resolve_truth_projection_builder(pack_name=pack_name)
    except Exception:
        return pack_name, ""
    return spec.pack, getattr(spec, "name", "")


def _preserve_existing_truth_rows(
    source_db_path: Path,
    dest_conn: sqlite3.Connection,
    *,
    exclude_pack: str,
) -> None:
    if not source_db_path.exists():
        return
    preserved_packs: set[str] = set()
    preserved_metadata_packs: set[str] = set()
    metadata_rows: list[tuple[Any, ...]] = []
    try:
        source_conn = sqlite3.connect(source_db_path)
    except sqlite3.DatabaseError:
        return
    try:
        for table_name, columns in TRUTH_PROJECTION_TABLE_COLUMNS.items():
            column_sql = ", ".join(columns)
            try:
                rows = source_conn.execute(
                    f"SELECT {column_sql} FROM {table_name} WHERE pack != ? ORDER BY pack",
                    (exclude_pack,),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                error_text = str(exc).lower()
                if "no such table" in error_text or "no such column" in error_text:
                    continue
                raise
            if not rows:
                continue
            placeholders = ", ".join("?" for _ in columns)
            dest_conn.executemany(
                f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholders})",
                rows,
            )
            preserved_packs.update(str(row[0]) for row in rows if row and row[0])

        try:
            metadata_rows = source_conn.execute(
                """
                SELECT pack, owner_pack, builder_name, built_at
                FROM truth_projections
                WHERE pack != ?
                ORDER BY pack
                """,
                (exclude_pack,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            error_text = str(exc).lower()
            if "no such table" not in error_text and "no such column" not in error_text:
                raise
            metadata_rows = []
    finally:
        source_conn.close()

    if metadata_rows:
        dest_conn.executemany(
            """
            INSERT INTO truth_projections (pack, owner_pack, builder_name, built_at)
            VALUES (?, ?, ?, ?)
            """,
            metadata_rows,
        )
        preserved_metadata_packs.update(str(row[0]) for row in metadata_rows if row and row[0])

    missing_metadata_packs = sorted(preserved_packs - preserved_metadata_packs)
    if not missing_metadata_packs:
        return

    built_at = _utc_now_text()
    for pack in missing_metadata_packs:
        owner_pack, builder_name = _projection_metadata(pack)
        dest_conn.execute(
            """
            INSERT INTO truth_projections (pack, owner_pack, builder_name, built_at)
            VALUES (?, ?, ?, ?)
            """,
            (pack, owner_pack, builder_name, built_at),
        )


def _split_frontmatter_body(content: str) -> str:
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) == 3:
            return parts[2].lstrip("\n")
    return content


def _build_surface_map(metadata_items: list[NoteMetadata]) -> dict[str, str]:
    surfaces: dict[str, str] = {}
    for meta in metadata_items:
        surfaces[canonicalize_note_id(meta.note_id)] = meta.note_id
        surfaces[canonicalize_note_id(meta.title)] = meta.note_id
        for alias in meta.aliases:
            normalized = canonicalize_note_id(str(alias))
            if normalized:
                surfaces[normalized] = meta.note_id
    return surfaces


def _resolve_target_slug(raw_target: str, registry: ConceptRegistry, surface_map: dict[str, str]) -> str | None:
    resolved = registry.resolve_mention(raw_target, include_related_context=False)
    if resolved.action == ResolutionAction.LINK_EXISTING and resolved.entry:
        return resolved.entry.slug

    normalized = canonicalize_note_id(raw_target)
    if normalized in surface_map:
        return surface_map[normalized]
    return None


def _extract_timeline_events(meta: NoteMetadata, body: str) -> list[tuple[str, str, str, str, str]]:
    events: list[tuple[str, str, str, str, str]] = []

    if meta.day_id:
        events.append(
            (
                meta.note_id,
                meta.day_id,
                "page_date",
                "",
                json.dumps({"path": meta.path, "title": meta.title}, ensure_ascii=False),
            )
        )

    heading_date_pattern = re.compile(r"^#{2,3}\s+(\d{4}-\d{2}(?:-\d{2})?)\s*$")
    for line in body.splitlines():
        match = heading_date_pattern.match(line.strip())
        if not match:
            continue
        event_date = match.group(1)
        events.append(
            (
                meta.note_id,
                event_date,
                "heading_date",
                line.strip().lstrip("#").strip(),
                json.dumps({"path": meta.path, "title": meta.title}, ensure_ascii=False),
            )
        )
    return events


def _collect_entity_mention_rows(
    vault_dir: Path,
    link_rows: list[tuple[str, str, str, str, int]],
    known_slugs: set[str],
) -> list[tuple[str, str, str, float, str, str, str]]:
    """Collect entity mentions from two sources:

    1. Wikilinks whose target resolves to an entity in EntityRegistry
    2. Stored LLM extraction results from entity_extractor (JSONL sidecar)
    """
    from .entity_registry import EntityRegistry

    entity_dir = vault_dir / "10-Knowledge" / "Entity"
    if not entity_dir.exists():
        return []

    registry = EntityRegistry(vault_dir).load()
    if len(registry) == 0:
        return []

    entity_slugs = {
        e.slug for e in registry.all_entries()
        if e.status in ("active", "candidate")
    }
    entity_map = {e.slug: e for e in registry.all_entries() if e.slug in entity_slugs}

    rows: list[tuple[str, str, str, float, str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    for source_slug, target_slug, target_raw, link_type, _line in link_rows:
        if target_slug not in entity_slugs:
            continue
        key = (target_slug, source_slug)
        if key in seen:
            continue
        seen.add(key)
        entry = entity_map[target_slug]
        rows.append((
            target_slug,
            entry.entity_type,
            source_slug,
            1.0,
            "wikilink",
            target_raw or target_slug,
            "",
        ))

    for source_slug in known_slugs:
        for entry in registry.all_entries():
            if entry.slug not in entity_slugs:
                continue
            if entry.slug == source_slug:
                continue
            match = registry.resolve_mention(source_slug)
            if match and match.slug == entry.slug:
                key = (entry.slug, source_slug)
                if key not in seen:
                    seen.add(key)
                    rows.append((
                        entry.slug,
                        entry.entity_type,
                        source_slug,
                        entry.confidence_avg or 0.8,
                        "alias_match",
                        source_slug,
                        "",
                    ))

    extraction_log = vault_dir / "60-Logs" / "entity-extractions.jsonl"
    if extraction_log.exists():
        try:
            for line in extraction_log.read_text(encoding="utf-8").strip().splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                src = record.get("source_slug", "")
                for m in record.get("mentions", []):
                    e_slug = m.get("resolved_slug", "")
                    if not e_slug or e_slug not in entity_slugs:
                        continue
                    key = (e_slug, src)
                    if key in seen:
                        continue
                    seen.add(key)
                    entry = entity_map.get(e_slug)
                    e_type = entry.entity_type if entry else m.get("kind", "")
                    rows.append((
                        e_slug,
                        e_type,
                        src,
                        m.get("confidence", 0.8),
                        m.get("resolution", "llm_ner"),
                        m.get("text", ""),
                        m.get("snippet", ""),
                    ))
        except (json.JSONDecodeError, OSError):
            pass

    return rows


def _collect_raw_rows(layout: VaultLayout) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    if not layout.link_resolution_dir.exists():
        return rows

    for sidecar_path in sorted(layout.link_resolution_dir.glob("*.json")):
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
        slug = canonicalize_note_id(payload.get("article") or sidecar_path.stem)
        if not slug:
            continue
        rows.append(
            (
                slug,
                "link_resolution",
                json.dumps(payload, ensure_ascii=False),
                str(sidecar_path),
            )
        )
    return rows


def _infer_audit_slug(payload: dict[str, object]) -> str:
    slug = payload.get("slug")
    if isinstance(slug, str) and slug:
        return canonicalize_note_id(slug)

    targets = payload.get("targets")
    if isinstance(targets, list) and len(targets) == 1 and isinstance(targets[0], str):
        return canonicalize_note_id(targets[0])

    # promotion / zone_violation events carry a `target_path`; index it as-is
    # (vault-relative when the caller passed a relative path) so that lint
    # check_zone_boundary can match by the same key without the lossy
    # path.stem collision (e.g. 30-Projects/*/Plan.md).
    target_path = payload.get("target_path")
    if isinstance(target_path, str) and target_path:
        return target_path
    return ""


def _collect_reuse_rows(layout: VaultLayout) -> list[tuple[str, str, str, str, str, str, str, int, int, int, str]]:
    rows: list[tuple[str, str, str, str, str, str, str, int, int, int, str]] = []
    seen_event_ids: set[str] = set()
    for payload in iter_for_index(layout, "reuse-events.jsonl"):
        event_id = str(payload.get("event_id") or "")
        if not event_id or event_id in seen_event_ids:
            continue
        seen_event_ids.add(event_id)
        rows.append(
            (
                event_id,
                str(payload.get("ts") or ""),
                str(payload.get("pack") or ""),
                str(payload.get("object_id") or ""),
                str(payload.get("object_kind") or ""),
                str(payload.get("surface") or ""),
                str(payload.get("consumer_ref") or ""),
                int(bool(payload.get("evidence_present"))),
                int(bool(payload.get("provenance_clean"))),
                int(bool(payload.get("trusted"))),
                json.dumps(payload, ensure_ascii=False),
            )
        )
    return rows


def _collect_audit_rows(layout: VaultLayout) -> list[tuple[str, str, str, str, str, str]]:
    rows: list[tuple[str, str, str, str, str, str]] = []
    log_specs = [
        ("pipeline", layout.pipeline_log),
        ("refine", layout.logs_dir / "refine-mutations.jsonl"),
        ("review-actions", layout.logs_dir / "review-actions.jsonl"),
    ]
    for source_log, path in log_specs:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            rows.append(
                (
                    source_log,
                    str(payload.get("event_type") or "unknown"),
                    _infer_audit_slug(payload),
                    str(payload.get("session_id") or ""),
                    str(payload.get("timestamp") or ""),
                    json.dumps(payload, ensure_ascii=False),
                )
            )
    return rows


def _chunk_page_body(body: str, fallback_title: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_title: str | None = None
    current_lines: list[str] = []

    for line in body.splitlines():
        match = re.match(r"^##\s+(.+)$", line.strip())
        if match:
            if current_title is not None and "\n".join(current_lines).strip():
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = match.group(1).strip()
            current_lines = []
            continue
        if current_title is not None:
            current_lines.append(line)

    if current_title is not None and "\n".join(current_lines).strip():
        sections.append((current_title, "\n".join(current_lines).strip()))

    if sections:
        return sections

    normalized_body = body.strip()
    if not normalized_body:
        return []
    return [(fallback_title, normalized_body)]


def _embed_text(text: str) -> bytes:
    """Delegate to the semantic embedding backend (Qwen3-Embedding MLX or hash fallback)."""
    return _embed_text_semantic(text)


def _get_embedding_model_name() -> str:
    from .embedding import get_model_name
    return get_model_name()


def _decode_embedding(blob: bytes) -> list[float]:
    decoded = array("f")
    decoded.frombytes(blob)
    return list(decoded)


def _dot_product(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _remove_sqlite_artifacts(db_path: Path) -> None:
    for candidate in (
        db_path,
        db_path.with_name(f"{db_path.name}-wal"),
        db_path.with_name(f"{db_path.name}-shm"),
    ):
        if candidate.exists():
            candidate.unlink()


def _initialize_database(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _remove_sqlite_artifacts(db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def _ensure_knowledge_db(vault_dir: Path) -> tuple[Path, VaultLayout]:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    authority_schema_version = _ensure_authority_schema_version(resolved_vault)
    rebuild_reason = ""
    projection_schema_version = 0
    if not layout.knowledge_db.exists():
        rebuild_reason = "knowledge_db_missing"
    elif not _knowledge_db_supports_pack_schema(layout.knowledge_db):
        rebuild_reason = "knowledge_db_schema_incompatible"
    else:
        projection_metadata = _read_knowledge_db_projection_metadata(layout.knowledge_db)
        if projection_metadata is None:
            rebuild_reason = "knowledge_db_projection_metadata_missing"
        else:
            built_authority_schema_version, projection_schema_version = projection_metadata
            if authority_schema_version > built_authority_schema_version:
                rebuild_reason = "authority_schema_version_newer_than_projection"
            elif KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION > projection_schema_version:
                rebuild_reason = "projection_schema_version_newer_than_metadata"

    if rebuild_reason:
        marker = write_projection_repair_marker(
            resolved_vault,
            kind="full_rebuild",
            scope={"projection_kind": KNOWLEDGE_DB_PROJECTION_KIND},
            reason=rebuild_reason,
            caused_by="ensure_knowledge_db_current",
            authority_schema_version=authority_schema_version,
            projection_schema_version=projection_schema_version,
        )
        rebuild_knowledge_index(resolved_vault)
        close_projection_repair_marker(resolved_vault, marker.marker_id)
    return resolved_vault, layout


def _knowledge_db_supports_pack_schema(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    required_columns = {
        "timeline_events": {"slug", "event_date", "event_type", "heading", "payload_json"},
        "objects": {"pack"},
        "claims": {"pack"},
        "claim_evidence": {
            "pack",
            "locator",
            "content_hash",
            "quote_start_line",
            "quote_end_line",
            "quote_start_char",
            "quote_end_char",
            "status",
            "verified_at",
        },
        "relations": {
            "pack",
            "quote_text",
            "locator",
            "content_hash",
            "quote_start_line",
            "quote_end_line",
            "quote_start_char",
            "quote_end_char",
            "status",
            "verified_at",
        },
        "compiled_summaries": {"pack"},
        "contradictions": {"pack"},
        "graph_edges": {"pack"},
        "graph_clusters": {"pack"},
        "truth_projections": {"pack", "owner_pack", "builder_name", "built_at"},
        "reuse_events": {"event_id", "ts", "pack", "surface", "trusted"},
        "page_metrics": {"slug", "last_seen_ts", "reuse_count", "citation_count"},
        "projection_metadata": {
            "projection_kind",
            "authority_schema_version",
            "projection_schema_version",
            "built_at",
        },
        "entity_mentions": {
            "entity_slug",
            "entity_type",
            "source_slug",
            "confidence",
            "detection_method",
        },
    }
    try:
        with sqlite3.connect(db_path) as conn:
            for table_name, expected_columns in required_columns.items():
                rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
                if not rows:
                    return False
                existing_columns = {str(row[1]) for row in rows}
                if not expected_columns.issubset(existing_columns):
                    return False
    except sqlite3.DatabaseError:
        return False
    return True


def ensure_knowledge_db_current(vault_dir: Path | str) -> Path:
    _, layout = _ensure_knowledge_db(resolve_vault_dir(vault_dir))
    return layout.knowledge_db


def _read_jsonl_items(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    items: list[dict[str, object]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            items.append(payload)
    return items


def _latest_contradiction_review_overrides(vault_dir: Path) -> dict[str, dict[str, str]]:
    layout = VaultLayout.from_vault(vault_dir)
    items = sorted(
        [
            item
            for item in _read_jsonl_items(layout.logs_dir / "review-actions.jsonl")
            if item.get("event_type") == "ui_contradictions_resolved"
        ],
        key=lambda item: str(item.get("timestamp") or ""),
    )
    overrides: dict[str, dict[str, str]] = {}
    for item in items:
        status = str(item.get("status") or "")
        note = str(item.get("note") or "")
        resolved_at = str(item.get("timestamp") or "")
        for contradiction_id in item.get("contradiction_ids", []) or []:
            contradiction_key = str(contradiction_id or "")
            if contradiction_key:
                overrides[contradiction_key] = {
                    "status": status,
                    "resolution_note": note,
                    "resolved_at": resolved_at,
                }
    return overrides


def rebuild_knowledge_index(
    vault_dir: Path,
    *,
    pack_name: str | None = None,
) -> dict[str, int | str]:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    truth_pack = _truth_pack_name(pack_name)
    authority_schema_version = _ensure_authority_schema_version(resolved_vault)

    # Detect embedding-backend mismatch with existing page_embeddings rows.
    # A rebuild rewrites every row (so post-rebuild is consistent regardless),
    # but warning surfaces the implicit migration to operators.
    if layout.knowledge_db.exists():
        try:
            with sqlite3.connect(layout.knowledge_db) as _check_conn:
                row = _check_conn.execute(
                    "SELECT embedding_model, length(embedding_blob) "
                    "FROM page_embeddings LIMIT 1"
                ).fetchone()
            if row is not None:
                stored_model = row[0] or ""
                # Each float32 takes 4 bytes; embedding_blob length / 4 = dim
                stored_dim = (row[1] or 0) // 4
                ok, msg = _assert_embedding_consistent(stored_model, stored_dim)
                if not ok:
                    logger.warning("[rebuild] %s", msg)
        except sqlite3.OperationalError:
            # page_embeddings doesn't exist yet — first-time rebuild
            pass

    with knowledge_db_write_lock(resolved_vault):
        evergreen_dir = layout.evergreen_dir
        atlas_dir = layout.atlas_dir
        areas_dir = resolved_vault / "20-Areas"
        parser = FrontmatterParser(resolved_vault)
        link_parser = LinkParser(resolved_vault)
        registry = ConceptRegistry(resolved_vault).load()

        object_metadata_items: list[NoteMetadata] = []
        entity_dir = resolved_vault / "10-Knowledge" / "Entity"
        if entity_dir.exists():
            for meta in parser.parse_directory(entity_dir, recursive=True):
                if "_Candidates" not in Path(meta.path).parts:
                    object_metadata_items.append(meta)
        for meta in parser.parse_directory(evergreen_dir, recursive=True):
            if "_Candidates" not in Path(meta.path).parts:
                object_metadata_items.append(meta)
        page_metadata_items = list(object_metadata_items)
        for extra_dir in (atlas_dir, areas_dir):
            if not extra_dir.exists():
                continue
            for meta in parser.parse_directory(extra_dir, recursive=True):
                if "_Candidates" in Path(meta.path).parts:
                    continue
                page_metadata_items.append(meta)

        deduped_page_metadata_items: list[NoteMetadata] = []
        seen_page_keys: set[str] = set()
        for meta in page_metadata_items:
            key = meta.note_id
            if key in seen_page_keys:
                continue
            seen_page_keys.add(key)
            deduped_page_metadata_items.append(meta)

        surface_map = _build_surface_map(object_metadata_items)
        known_slugs = {meta.note_id for meta in object_metadata_items}

        temp_db_path = layout.knowledge_db.with_name(f"{layout.knowledge_db.name}.tmp")
        _remove_sqlite_artifacts(temp_db_path)

        conn = None
        try:
            conn = _initialize_database(temp_db_path)
            _preserve_existing_truth_rows(layout.knowledge_db, conn, exclude_pack=truth_pack)
            page_rows = []
            timeline_rows = []
            embedding_rows = []
            for meta in deduped_page_metadata_items:
                file_path = Path(meta.path)
                body = _split_frontmatter_body(file_path.read_text(encoding="utf-8"))
                page_rows.append(
                    (
                        meta.note_id,
                        meta.title,
                        meta.note_type,
                        str(file_path),
                        meta.day_id,
                        json.dumps(meta.to_dict(), ensure_ascii=False),
                        body,
                    )
                )
                timeline_rows.extend(_extract_timeline_events(meta, body))
                for chunk_index, (section_title, chunk_text) in enumerate(_chunk_page_body(body, meta.title)):
                    embedding_rows.append(
                        (
                            meta.note_id,
                            chunk_index,
                            section_title,
                            chunk_text,
                            _embed_text(f"{section_title}\n{chunk_text}"),
                            get_model_name(),
                        )
                    )

            conn.executemany(
                """
                INSERT INTO pages_index (slug, title, note_type, path, day_id, frontmatter_json, body)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                page_rows,
            )
            conn.executemany(
                "INSERT INTO page_fts (slug, title, body) VALUES (?, ?, ?)",
                [(slug, title, body) for slug, title, _, _, _, _, body in page_rows],
            )

            link_rows = []
            for meta in deduped_page_metadata_items:
                file_path = Path(meta.path)
                for link in link_parser.parse_file(file_path):
                    target_slug = _resolve_target_slug(link.target_raw or link.target, registry, surface_map)
                    if not target_slug or target_slug not in known_slugs:
                        continue
                    link_rows.append(
                        (
                            link.source,
                            target_slug,
                            link.target_raw,
                            link.link_type,
                            link.line_number,
                        )
                    )

            conn.executemany(
                """
                INSERT INTO page_links (source_slug, target_slug, target_raw, link_type, line_number)
                VALUES (?, ?, ?, ?, ?)
                """,
                link_rows,
            )

            object_page_rows = [row for row in page_rows if row[0] in known_slugs]
            object_link_rows = [row for row in link_rows if row[0] in known_slugs]
            projection_spec, truth_projection = execute_truth_projection_builder(
                vault_dir=resolved_vault,
                page_rows=object_page_rows,
                link_rows=object_link_rows,
                pack_name=pack_name,
            )
            conn.executemany(
                """
                INSERT INTO objects (pack, object_id, object_kind, title, canonical_path, source_slug)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [row.to_row() for row in truth_projection.objects],
            )
            conn.executemany(
                """
                INSERT INTO claims (pack, claim_id, object_id, claim_kind, claim_text, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [row.to_row() for row in truth_projection.claims],
            )
            conn.executemany(
                """
                INSERT INTO claim_evidence (
                    pack, claim_id, source_slug, evidence_kind, quote_text,
                    locator, content_hash, retrieval_context,
                    quote_start_line, quote_end_line, quote_start_char, quote_end_char,
                    status, verified_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [row.to_row() for row in truth_projection.claim_evidence],
            )
            conn.executemany(
                """
                INSERT INTO relations (
                    pack, source_object_id, target_object_id, relation_type, evidence_source_slug,
                    quote_text, locator, content_hash, retrieval_context,
                    quote_start_line, quote_end_line, quote_start_char, quote_end_char,
                    status, verified_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [row.to_row() for row in truth_projection.relations],
            )
            conn.executemany(
                """
                INSERT INTO compiled_summaries (pack, object_id, summary_text, source_slug)
                VALUES (?, ?, ?, ?)
                """,
                [row.to_row() for row in truth_projection.compiled_summaries],
            )
            conn.executemany(
                """
                INSERT INTO contradictions (
                    pack,
                    contradiction_id,
                    subject_key,
                    positive_claim_ids_json,
                    negative_claim_ids_json,
                    status,
                    resolution_note,
                    resolved_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [row.to_row() for row in truth_projection.contradictions],
            )
            conn.executemany(
                """
                INSERT INTO graph_edges (
                    pack,
                    edge_id,
                    source_object_id,
                    target_object_id,
                    edge_kind,
                    weight,
                    evidence_source_slug
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [row.to_row() for row in truth_projection.graph_edges],
            )
            conn.executemany(
                """
                INSERT INTO graph_clusters (
                    pack,
                    cluster_id,
                    cluster_kind,
                    label,
                    center_object_id,
                    member_object_ids_json,
                    score
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [row.to_row() for row in truth_projection.graph_clusters],
            )
            conn.execute(
                """
                INSERT INTO truth_projections (pack, owner_pack, builder_name, built_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    truth_pack,
                    projection_spec.pack,
                    getattr(projection_spec, "name", ""),
                    _utc_now_text(),
                ),
            )
            conn.execute(
                """
                INSERT INTO projection_metadata (
                    projection_kind,
                    authority_schema_version,
                    projection_schema_version,
                    built_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    KNOWLEDGE_DB_PROJECTION_KIND,
                    authority_schema_version,
                    KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION,
                    _utc_now_text(),
                ),
            )

            raw_rows = _collect_raw_rows(layout)
            conn.executemany(
                """
                INSERT INTO raw_data (slug, source_name, payload_json, source_path)
                VALUES (?, ?, ?, ?)
                """,
                raw_rows,
            )

            conn.executemany(
                """
                INSERT INTO timeline_events (slug, event_date, event_type, heading, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                timeline_rows,
            )

            audit_rows = _collect_audit_rows(layout)
            conn.executemany(
                """
                INSERT INTO audit_events (source_log, event_type, slug, session_id, timestamp, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                audit_rows,
            )
            reuse_rows = _collect_reuse_rows(layout)
            conn.executemany(
                """
                INSERT INTO reuse_events (
                    event_id, ts, pack, object_id, object_kind, surface,
                    consumer_ref, evidence_present, provenance_clean, trusted,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                reuse_rows,
            )
            conn.executemany(
                """
                INSERT INTO page_embeddings (slug, chunk_index, section_title, chunk_text, embedding_blob, embedding_model)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                embedding_rows,
            )

            entity_mention_rows = _collect_entity_mention_rows(
                resolved_vault, link_rows, known_slugs
            )
            conn.executemany(
                """
                INSERT INTO entity_mentions (
                    entity_slug, entity_type, source_slug,
                    confidence, detection_method, mention_text, snippet
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                entity_mention_rows,
            )

            from .relation_promotion import replay_relation_promotions
            from .evidence_replay import replay_evidence_verifications

            relations_replayed = replay_relation_promotions(
                conn, layout, pack_name=truth_pack
            )
            evidence_updates = replay_evidence_verifications(
                conn, layout, pack_name=truth_pack
            )

            page_metrics_indexed = _rebuild_page_metrics(conn)

            conn.commit()
        except Exception:
            if conn is not None:
                conn.close()
            _remove_sqlite_artifacts(temp_db_path)
            raise
        else:
            assert conn is not None
            conn.close()
            _remove_sqlite_artifacts(layout.knowledge_db)
            temp_db_path.replace(layout.knowledge_db)

            return {
                "db_path": str(layout.knowledge_db),
                "projection_pack": truth_pack,
                "pages_indexed": len(page_rows),
                "links_indexed": len(link_rows),
                "raw_records_indexed": len(raw_rows),
                "timeline_events_indexed": len(timeline_rows),
                "audit_events_indexed": len(audit_rows),
                "reuse_events_indexed": len(reuse_rows),
                "embedding_chunks_indexed": len(embedding_rows),
                "objects_indexed": len(truth_projection.objects),
                "claims_indexed": len(truth_projection.claims),
                "relations_indexed": len(truth_projection.relations),
                "relations_replayed": relations_replayed,
                "evidence_updates_replayed": evidence_updates,
                "page_metrics_indexed": page_metrics_indexed,
                "compiled_summaries_indexed": len(truth_projection.compiled_summaries),
                "contradictions_indexed": len(truth_projection.contradictions),
                "graph_edges_indexed": len(truth_projection.graph_edges),
                "graph_clusters_indexed": len(truth_projection.graph_clusters),
                "entity_mentions_indexed": len(entity_mention_rows),
            }


def _iso_to_epoch(value: object) -> int:
    """Best-effort ISO-8601 → unix epoch. Returns 0 for unparseable inputs.

    Both ``audit_events.timestamp`` and ``reuse_events.ts`` are stored as
    text; we want a single integer to drive the recency decay in
    ``search_fused``. Stripping a trailing ``Z`` makes ``fromisoformat`` happy
    on Python <3.11.
    """
    if not value:
        return 0
    text = str(value).strip()
    if not text:
        return 0
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return 0


def _rebuild_page_metrics(conn: sqlite3.Connection) -> int:
    """Aggregate per-slug recency / reuse / citation signals into ``page_metrics``.

    Called from ``rebuild_knowledge_index`` after ``audit_events``,
    ``reuse_events``, and ``page_links`` have been populated. The table is
    consumed by ``search_fused`` to apply bi-temporal decay on top of RRF.

    ``last_seen_ts`` is the max of any audit-event timestamp on this slug and
    any reuse-event timestamp where ``object_id == slug``. ``reuse_count``
    counts reuse events; ``citation_count`` counts inbound wikilinks. Slugs
    with no signal still get a row (zeros) so callers can left-join freely.
    """
    conn.execute("DELETE FROM page_metrics")

    audit_max: dict[str, int] = {}
    for slug, ts in conn.execute(
        "SELECT slug, MAX(timestamp) FROM audit_events WHERE slug != '' GROUP BY slug"
    ):
        audit_max[str(slug)] = _iso_to_epoch(ts)

    reuse_stats: dict[str, tuple[int, int]] = {}
    for object_id, max_ts, count in conn.execute(
        "SELECT object_id, MAX(ts), COUNT(*) FROM reuse_events "
        "WHERE object_id != '' GROUP BY object_id"
    ):
        reuse_stats[str(object_id)] = (_iso_to_epoch(max_ts), int(count or 0))

    citations: dict[str, int] = {}
    for target_slug, count in conn.execute(
        "SELECT target_slug, COUNT(*) FROM page_links "
        "WHERE target_slug != '' GROUP BY target_slug"
    ):
        citations[str(target_slug)] = int(count or 0)

    rows: list[tuple[str, int, int, int]] = []
    seen: set[str] = set()
    for (slug,) in conn.execute("SELECT slug FROM pages_index"):
        slug = str(slug)
        seen.add(slug)
        audit_ts = audit_max.get(slug, 0)
        reuse_ts, reuse_count = reuse_stats.get(slug, (0, 0))
        last_seen = max(audit_ts, reuse_ts)
        rows.append((slug, last_seen, reuse_count, citations.get(slug, 0)))

    # Cover slugs that only appear via reuse / link tables (e.g. an object_id
    # that doesn't map to a current page yet) so the metrics view is complete.
    for slug in set(reuse_stats) | set(citations) | set(audit_max):
        if slug in seen:
            continue
        audit_ts = audit_max.get(slug, 0)
        reuse_ts, reuse_count = reuse_stats.get(slug, (0, 0))
        rows.append((slug, max(audit_ts, reuse_ts), reuse_count, citations.get(slug, 0)))

    conn.executemany(
        "INSERT OR REPLACE INTO page_metrics "
        "(slug, last_seen_ts, reuse_count, citation_count) VALUES (?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def query_knowledge_index(vault_dir: Path, query: str, limit: int = 5) -> list[dict[str, str | int | float]]:
    _, layout = _ensure_knowledge_db(vault_dir)

    query_vector = _decode_embedding(_embed_text(query))
    current_model = _get_embedding_model_name()
    query_dim = len(query_vector)
    with sqlite3.connect(layout.knowledge_db) as conn:
        rows = conn.execute(
            """
            SELECT slug, chunk_index, section_title, chunk_text, embedding_blob
            FROM page_embeddings
            WHERE embedding_model = ?
            """,
            (current_model,),
        ).fetchall()

    scored = []
    for slug, chunk_index, section_title, chunk_text, embedding_blob in rows:
        stored_vector = _decode_embedding(embedding_blob)
        if len(stored_vector) != query_dim:
            continue
        score = _dot_product(query_vector, stored_vector)
        scored.append(
            {
                "slug": slug,
                "chunk_index": chunk_index,
                "section_title": section_title,
                "chunk_text": chunk_text,
                "score": score,
            }
        )
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:limit]


def search_knowledge_index(vault_dir: Path, query: str, limit: int = 10) -> list[dict[str, str | float]]:
    _, layout = _ensure_knowledge_db(vault_dir)
    with sqlite3.connect(layout.knowledge_db) as conn:
        matched_rows = conn.execute(
            """
            SELECT slug, title, bm25(page_fts) AS score
            FROM page_fts
            WHERE page_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        matched_slugs = {row[0] for row in matched_rows}
        remaining = max(limit - len(matched_rows), 0)
        fallback_rows = []
        if remaining:
            if matched_slugs:
                placeholders = ",".join("?" for _ in matched_slugs)
                fallback_rows = conn.execute(
                    f"""
                    SELECT slug, title
                    FROM pages_index
                    WHERE slug NOT IN ({placeholders})
                    ORDER BY title
                    LIMIT ?
                    """,
                    (*matched_slugs, remaining),
                ).fetchall()
            else:
                fallback_rows = conn.execute(
                    """
                    SELECT slug, title
                    FROM pages_index
                    ORDER BY title
                    LIMIT ?
                    """,
                    (remaining,),
                ).fetchall()

    results = [
        {
            "slug": slug,
            "title": title,
            "score": float(-score),
        }
        for slug, title, score in matched_rows
    ]
    results.extend(
        {
            "slug": slug,
            "title": title,
            "score": 0.0,
        }
        for slug, title in fallback_rows
    )
    return results


def search_fused(
    vault_dir: Path,
    query: str,
    *,
    limit: int = 10,
    rrf_k: int = 60,
    tau_days: float = 30.0,
    now_ts: int | None = None,
) -> list[dict[str, str | float]]:
    """Hybrid retrieval: RRF over BM25 + vector, then bi-temporal decay.

    The two existing retrievers (``search_knowledge_index`` BM25 and
    ``query_knowledge_index`` vector) each emit a ranked list. We fuse them
    with reciprocal rank fusion — robust to per-engine score scale
    differences — then multiply by:

    * **Recency**: ``exp(-(now - last_seen_ts) / (tau_days * 86400))``
    * **Frequency**: ``1 + log(1 + reuse_count)``
    * **Importance**: ``1 + log(1 + citation_count)``

    Slugs missing from ``page_metrics`` get a 1.0 multiplier (no penalty), so
    a cold vault still returns its BM25/vector top-N. We over-fetch each
    branch by 3x ``limit`` so the fused ranking has room to reorder.
    """
    import math

    _, layout = _ensure_knowledge_db(vault_dir)

    fetch = max(limit * 3, limit)
    try:
        bm25_results = search_knowledge_index(vault_dir, query, limit=fetch)
    except sqlite3.OperationalError:
        normalized_terms = re.findall(r"[\w\u4e00-\u9fff]+", query, flags=re.UNICODE)
        if normalized_terms:
            safe_query = " ".join(f'"{term}"' for term in normalized_terms)
            bm25_results = search_knowledge_index(vault_dir, safe_query, limit=fetch)
        else:
            bm25_results = []
    vector_chunks = query_knowledge_index(vault_dir, query, limit=fetch)

    rrf_scores: dict[str, float] = {}
    titles: dict[str, str] = {}

    for rank, item in enumerate(bm25_results):
        slug = str(item.get("slug") or "")
        if not slug:
            continue
        rrf_scores[slug] = rrf_scores.get(slug, 0.0) + 1.0 / (rrf_k + rank + 1)
        if item.get("title"):
            titles.setdefault(slug, str(item["title"]))

    seen_vector: set[str] = set()
    vector_rank = 0
    for chunk in vector_chunks:
        slug = str(chunk.get("slug") or "")
        if not slug or slug in seen_vector:
            continue
        seen_vector.add(slug)
        rrf_scores[slug] = rrf_scores.get(slug, 0.0) + 1.0 / (rrf_k + vector_rank + 1)
        vector_rank += 1

    if not rrf_scores:
        return []

    if now_ts is None:
        from datetime import datetime, timezone

        now_ts = int(datetime.now(timezone.utc).timestamp())
    tau_seconds = max(tau_days * 86400.0, 1.0)

    metrics: dict[str, tuple[int, int, int]] = {}
    titles_db: dict[str, str] = {}
    with sqlite3.connect(layout.knowledge_db) as conn:
        placeholders = ",".join("?" for _ in rrf_scores)
        for slug, last_seen_ts, reuse_count, citation_count in conn.execute(
            f"SELECT slug, last_seen_ts, reuse_count, citation_count "
            f"FROM page_metrics WHERE slug IN ({placeholders})",
            list(rrf_scores.keys()),
        ):
            metrics[str(slug)] = (
                int(last_seen_ts or 0),
                int(reuse_count or 0),
                int(citation_count or 0),
            )
        missing_titles = [s for s in rrf_scores if s not in titles]
        if missing_titles:
            placeholders = ",".join("?" for _ in missing_titles)
            for slug, title in conn.execute(
                f"SELECT slug, title FROM pages_index WHERE slug IN ({placeholders})",
                missing_titles,
            ):
                titles_db[str(slug)] = str(title or "")

    fused: list[dict[str, str | float]] = []
    for slug, base in rrf_scores.items():
        last_seen_ts, reuse_count, citation_count = metrics.get(slug, (0, 0, 0))
        if last_seen_ts > 0:
            recency = math.exp(-max(now_ts - last_seen_ts, 0) / tau_seconds)
        else:
            recency = 1.0
        frequency = 1.0 + math.log(1.0 + reuse_count)
        importance = 1.0 + math.log(1.0 + citation_count)
        fused.append(
            {
                "slug": slug,
                "title": titles.get(slug) or titles_db.get(slug) or slug,
                "score": base * recency * frequency * importance,
                "rrf_score": base,
                "recency": recency,
                "frequency": frequency,
                "importance": importance,
            }
        )

    fused.sort(key=lambda item: item["score"], reverse=True)
    return fused[:limit]


def search_truth_store(
    vault_dir: Path,
    query: str,
    limit: int = 10,
    *,
    pack_name: str | None = None,
) -> list[dict[str, object]]:
    _, layout = _ensure_knowledge_db(vault_dir)
    truth_pack = _truth_pack_name(pack_name)
    like_query = f"%{query.strip()}%"
    with sqlite3.connect(layout.knowledge_db) as conn:
        rows = conn.execute(
            """
            SELECT claims.object_id, objects.title, claims.claim_kind, claims.claim_text, compiled_summaries.summary_text
            FROM claims
            JOIN objects ON objects.pack = claims.pack AND objects.object_id = claims.object_id
            LEFT JOIN compiled_summaries
              ON compiled_summaries.pack = claims.pack
             AND compiled_summaries.object_id = claims.object_id
            WHERE claims.pack = ?
              AND (claims.claim_text LIKE ? OR compiled_summaries.summary_text LIKE ? OR objects.title LIKE ?)
            ORDER BY claims.object_id
            LIMIT ?
            """,
            (truth_pack, like_query, like_query, like_query, limit),
        ).fetchall()

    return [
        {
            "object_id": row[0],
            "title": row[1],
            "claim_kind": row[2],
            "claim_text": row[3],
            "summary_text": row[4] or "",
        }
        for row in rows
    ]


def list_contradictions(
    vault_dir: Path,
    limit: int = 20,
    subject: str | None = None,
    *,
    pack_name: str | None = None,
) -> list[dict[str, object]]:
    _, layout = _ensure_knowledge_db(vault_dir)
    truth_pack = _truth_pack_name(pack_name)
    query = """
        SELECT contradiction_id, subject_key, positive_claim_ids_json, negative_claim_ids_json, status, resolution_note, resolved_at
        FROM contradictions
    """
    params: tuple[object, ...]
    if subject:
        query += " WHERE pack = ? AND subject_key LIKE ?"
        params = (truth_pack, f"%{subject}%", limit)
    else:
        query += " WHERE pack = ?"
        params = (truth_pack, limit)
    query += " ORDER BY subject_key LIMIT ?"

    try:
        with sqlite3.connect(layout.knowledge_db) as conn:
            rows = conn.execute(query, params).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return []
        raise
    overrides = _latest_contradiction_review_overrides(vault_dir)
    items = []
    for row in rows:
        contradiction_id = str(row[0])
        item = {
            "contradiction_id": contradiction_id,
            "subject_key": row[1],
            "positive_claim_ids": json.loads(row[2]),
            "negative_claim_ids": json.loads(row[3]),
            "status": row[4],
            "resolution_note": row[5] or "",
            "resolved_at": row[6] or "",
        }
        override = overrides.get(contradiction_id)
        if override:
            item["status"] = override["status"]
            item["resolution_note"] = override["resolution_note"]
            item["resolved_at"] = override["resolved_at"]
        items.append(item)
    return items


def resolve_contradictions(
    vault_dir: Path,
    contradiction_ids: list[str],
    *,
    status: str,
    note: str = "",
    pack_name: str | None = None,
) -> dict[str, object]:
    _, layout = _ensure_knowledge_db(vault_dir)
    truth_pack = _truth_pack_name(pack_name)
    resolved_ids = list(dict.fromkeys(contradiction_ids))
    if not resolved_ids:
        return {
            "resolved_count": 0,
            "contradiction_ids": [],
            "status": status,
            "resolution_note": note,
            "db_path": str(layout.knowledge_db),
        }

    placeholders = ",".join("?" for _ in resolved_ids)
    with sqlite3.connect(layout.knowledge_db) as conn:
        existing = conn.execute(
            f"""
            SELECT contradiction_id
            FROM contradictions
            WHERE pack = ? AND contradiction_id IN ({placeholders})
            ORDER BY contradiction_id
            """,
            (truth_pack, *resolved_ids),
        ).fetchall()
        found_ids = [row[0] for row in existing]

    return {
        "resolved_count": len(found_ids),
        "contradiction_ids": found_ids,
        "status": status,
        "resolution_note": note,
        "db_path": str(layout.knowledge_db),
    }


def contradiction_object_ids(
    vault_dir: Path,
    contradiction_ids: list[str],
    *,
    pack_name: str | None = None,
) -> list[str]:
    _, layout = _ensure_knowledge_db(vault_dir)
    truth_pack = _truth_pack_name(pack_name)
    resolved_ids = list(dict.fromkeys(contradiction_ids))
    if not resolved_ids:
        return []

    placeholders = ",".join("?" for _ in resolved_ids)
    with sqlite3.connect(layout.knowledge_db) as conn:
        rows = conn.execute(
            f"""
            SELECT positive_claim_ids_json, negative_claim_ids_json
            FROM contradictions
            WHERE pack = ? AND contradiction_id IN ({placeholders})
            """,
            (truth_pack, *resolved_ids),
        ).fetchall()

        claim_ids: list[str] = []
        for positive_json, negative_json in rows:
            claim_ids.extend(json.loads(positive_json))
            claim_ids.extend(json.loads(negative_json))
        claim_ids = list(dict.fromkeys(claim_ids))
        if not claim_ids:
            return []

        claim_placeholders = ",".join("?" for _ in claim_ids)
        object_rows = conn.execute(
            f"""
            SELECT DISTINCT object_id
            FROM claims
            WHERE pack = ? AND claim_id IN ({claim_placeholders})
            ORDER BY object_id
            """,
            (truth_pack, *claim_ids),
        ).fetchall()

    return [row[0] for row in object_rows]


def rebuild_compiled_summaries(
    vault_dir: Path,
    object_ids: list[str] | None = None,
    *,
    pack_name: str | None = None,
) -> dict[str, object]:
    _, layout = _ensure_knowledge_db(vault_dir)
    truth_pack = _truth_pack_name(pack_name)
    with sqlite3.connect(layout.knowledge_db) as conn:
        if object_ids:
            placeholders = ",".join("?" for _ in object_ids)
            object_rows = conn.execute(
                f"""
                SELECT object_id
                FROM objects
                WHERE pack = ? AND object_id IN ({placeholders})
                ORDER BY object_id
                """,
                (truth_pack, *object_ids),
            ).fetchall()
        else:
            object_rows = conn.execute(
                """
                SELECT object_id
                FROM objects
                WHERE pack = ?
                ORDER BY object_id
                """
                ,
                (truth_pack,),
            ).fetchall()

    rebuilt_ids = [str(row[0]) for row in object_rows]
    rebuild_knowledge_index(vault_dir, pack_name=truth_pack)

    return {
        "objects_rebuilt": len(rebuilt_ids),
        "object_ids": rebuilt_ids,
        "db_path": str(layout.knowledge_db),
    }


def get_knowledge_page(vault_dir: Path, slug: str) -> dict[str, object] | None:
    _, layout = _ensure_knowledge_db(vault_dir)
    canonical_slug = canonicalize_note_id(slug)
    with sqlite3.connect(layout.knowledge_db) as conn:
        row = conn.execute(
            """
            SELECT slug, title, note_type, path, day_id, frontmatter_json, body
            FROM pages_index
            WHERE slug = ?
            """,
            (canonical_slug,),
        ).fetchone()
    if row is None:
        return None
    page_slug, title, note_type, path, day_id, frontmatter_json, body = row
    return {
        "slug": page_slug,
        "title": title,
        "note_type": note_type,
        "path": path,
        "day_id": day_id,
        "frontmatter": json.loads(frontmatter_json),
        "body": body,
    }


def knowledge_index_stats(vault_dir: Path, *, pack_name: str | None = None) -> dict[str, object]:
    _, layout = _ensure_knowledge_db(vault_dir)
    truth_pack = _truth_pack_name(pack_name)
    queries = {
        "pages": "SELECT COUNT(*) FROM pages_index",
        "links": "SELECT COUNT(*) FROM page_links",
        "raw_records": "SELECT COUNT(*) FROM raw_data",
        "timeline_events": "SELECT COUNT(*) FROM timeline_events",
        "audit_events": "SELECT COUNT(*) FROM audit_events",
        "reuse_events": "SELECT COUNT(*) FROM reuse_events",
        "embedding_chunks": "SELECT COUNT(*) FROM page_embeddings",
        "objects": "SELECT COUNT(*) FROM objects WHERE pack = ?",
        "claims": "SELECT COUNT(*) FROM claims WHERE pack = ?",
        "relations": "SELECT COUNT(*) FROM relations WHERE pack = ?",
        "compiled_summaries": "SELECT COUNT(*) FROM compiled_summaries WHERE pack = ?",
        "contradictions": "SELECT COUNT(*) FROM contradictions WHERE pack = ?",
        "graph_edges": "SELECT COUNT(*) FROM graph_edges WHERE pack = ?",
        "graph_clusters": "SELECT COUNT(*) FROM graph_clusters WHERE pack = ?",
    }
    stats: dict[str, object] = {"db_path": str(layout.knowledge_db)}
    with sqlite3.connect(layout.knowledge_db) as conn:
        for key, query in queries.items():
            stats[key] = int(conn.execute(query, (truth_pack,)).fetchone()[0]) if "pack = ?" in query else int(conn.execute(query).fetchone()[0])
        try:
            rows = conn.execute(
                """
                SELECT pack, owner_pack, builder_name, built_at
                FROM truth_projections
                ORDER BY pack
                """
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            rows = []
        stats["materialized_truth_packs"] = [
            {
                "pack": str(pack),
                "owner_pack": str(owner_pack),
                "builder_name": str(builder_name or ""),
                "built_at": str(built_at or ""),
            }
            for pack, owner_pack, builder_name, built_at in rows
        ]
    return stats


def recent_audit_events(vault_dir: Path, limit: int = 20, source_log: str | None = None) -> list[dict[str, object]]:
    _, layout = _ensure_knowledge_db(vault_dir)
    query = """
        SELECT source_log, event_type, slug, session_id, timestamp, payload_json
        FROM audit_events
    """
    params: tuple[object, ...]
    if source_log:
        query += " WHERE source_log = ?"
        params = (source_log, limit)
    else:
        params = (limit,)
    query += " ORDER BY timestamp DESC, rowid DESC LIMIT ?"

    with sqlite3.connect(layout.knowledge_db) as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        {
            "source_log": row[0],
            "event_type": row[1],
            "slug": row[2],
            "session_id": row[3],
            "timestamp": row[4],
            "payload": json.loads(row[5]),
        }
        for row in rows
    ]


def knowledge_tools_json() -> list[dict[str, object]]:
    return [
        {
            "name": "knowledge_search",
            "description": "Keyword search against the derived knowledge index",
            "args": {"query": "string", "limit": "integer?"},
        },
        {
            "name": "knowledge_query",
            "description": "Read-only semantic-style chunk retrieval from local embeddings",
            "args": {"query": "string", "limit": "integer?"},
        },
        {
            "name": "knowledge_truth_search",
            "description": "Search truth-store claims and compiled summaries",
            "args": {"query": "string", "limit": "integer?"},
        },
        {
            "name": "knowledge_contradictions",
            "description": "List contradiction records from the truth store",
            "args": {"limit": "integer?", "subject": "string?"},
        },
        {
            "name": "knowledge_get",
            "description": "Fetch a canonical page payload by slug",
            "args": {"slug": "string"},
        },
        {
            "name": "knowledge_stats",
            "description": "Return knowledge index table counts and db path",
            "args": {},
        },
        {
            "name": "knowledge_audit_recent",
            "description": "Return recent audit events from the derived knowledge index",
            "args": {"limit": "integer?", "source_log": "string?"},
        },
    ]


def dispatch_knowledge_tool(vault_dir: Path, tool_name: str, args: dict[str, object]) -> dict[str, object]:
    if tool_name == "knowledge_search":
        query = str(args.get("query") or "")
        limit = int(args.get("limit") or 10)
        return {"results": search_knowledge_index(vault_dir, query, limit=limit)}
    if tool_name == "knowledge_query":
        query = str(args.get("query") or "")
        limit = int(args.get("limit") or 5)
        return {"results": query_knowledge_index(vault_dir, query, limit=limit)}
    if tool_name == "knowledge_truth_search":
        query = str(args.get("query") or "")
        limit = int(args.get("limit") or 10)
        return {"results": search_truth_store(vault_dir, query, limit=limit)}
    if tool_name == "knowledge_contradictions":
        limit = int(args.get("limit") or 20)
        subject = args.get("subject")
        subject_value = str(subject) if subject else None
        return {"items": list_contradictions(vault_dir, limit=limit, subject=subject_value)}
    if tool_name == "knowledge_get":
        slug = str(args.get("slug") or "")
        return {"page": get_knowledge_page(vault_dir, slug)}
    if tool_name == "knowledge_stats":
        return {"stats": knowledge_index_stats(vault_dir)}
    if tool_name == "knowledge_audit_recent":
        limit = int(args.get("limit") or 20)
        source_log = args.get("source_log")
        source_log_value = str(source_log) if source_log else None
        return {"events": recent_audit_events(vault_dir, limit=limit, source_log=source_log_value)}
    raise ValueError(f"unknown tool: {tool_name}")


def serve_knowledge_index(vault_dir: Path, stdin: TextIOBase, stdout: TextIOBase) -> None:
    for line in stdin:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            request = json.loads(stripped)
            tool_name = str(request.get("tool") or "")
            args = request.get("args") or {}
            if not isinstance(args, dict):
                raise ValueError("args must be an object")
            result = dispatch_knowledge_tool(vault_dir, tool_name, args)
            response = {"ok": True, "result": result}
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        stdout.flush()
