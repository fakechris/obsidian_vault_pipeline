from __future__ import annotations

from array import array
from datetime import datetime, timezone
import hashlib
from io import TextIOBase
import json
import math
import re
import sqlite3
from pathlib import Path

from .concept_registry import ConceptRegistry, ResolutionAction
from .graph.frontmatter import FrontmatterParser, NoteMetadata
from .graph.link_parser import LinkParser
from .identity import canonicalize_note_id
from .packs.loader import DEFAULT_WORKFLOW_PACK_NAME
from .runtime import VaultLayout, knowledge_db_write_lock, resolve_vault_dir
from .truth_projection_registry import execute_truth_projection_builder, resolve_truth_projection_builder
from .truth_store import TRUTH_STORE_SCHEMA

SUMMARY_MAX_LEN = 320
SUMMARY_RELATED_LIMIT = 3


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

CREATE VIRTUAL TABLE page_fts USING fts5(
  slug UNINDEXED,
  title,
  body
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
"""

SCHEMA += "\n" + TRUTH_STORE_SCHEMA

EMBEDDING_DIMENSIONS = 128
EMBEDDING_MODEL = "local-hash-v1"
TRUTH_PROJECTION_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "objects": ("pack", "object_id", "object_kind", "title", "canonical_path", "source_slug"),
    "claims": ("pack", "claim_id", "object_id", "claim_kind", "claim_text", "confidence"),
    "claim_evidence": ("pack", "claim_id", "source_slug", "evidence_kind", "quote_text"),
    "relations": ("pack", "source_object_id", "target_object_id", "relation_type", "evidence_source_slug"),
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
    try:
        with sqlite3.connect(source_db_path) as source_conn:
            for table_name, columns in TRUTH_PROJECTION_TABLE_COLUMNS.items():
                column_sql = ", ".join(columns)
                rows = source_conn.execute(
                    f"SELECT {column_sql} FROM {table_name} WHERE pack != ? ORDER BY pack",
                    (exclude_pack,),
                ).fetchall()
                if not rows:
                    continue
                placeholders = ", ".join("?" for _ in columns)
                dest_conn.executemany(
                    f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholders})",
                    rows,
                )
                preserved_packs.update(str(row[0]) for row in rows if row and row[0])

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
    except sqlite3.DatabaseError:
        return

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
    if isinstance(slug, str):
        return canonicalize_note_id(slug)

    targets = payload.get("targets")
    if isinstance(targets, list) and len(targets) == 1 and isinstance(targets[0], str):
        return canonicalize_note_id(targets[0])
    return ""


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


def _tokenize_for_embedding(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _embed_text(text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> bytes:
    vector = [0.0] * dimensions
    for token in _tokenize_for_embedding(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    if norm > 0:
        vector = [value / norm for value in vector]
    return array("f", vector).tobytes()


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
    if not layout.knowledge_db.exists() or not _knowledge_db_supports_pack_schema(layout.knowledge_db):
        rebuild_knowledge_index(resolved_vault)
    return resolved_vault, layout


def _knowledge_db_supports_pack_schema(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    required_columns = {
        "timeline_events": {"slug", "event_date", "event_type", "heading", "payload_json"},
        "objects": {"pack"},
        "claims": {"pack"},
        "claim_evidence": {"pack"},
        "relations": {"pack"},
        "compiled_summaries": {"pack"},
        "contradictions": {"pack"},
        "graph_edges": {"pack"},
        "graph_clusters": {"pack"},
        "truth_projections": {"pack", "owner_pack", "builder_name", "built_at"},
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
    with knowledge_db_write_lock(resolved_vault):
        evergreen_dir = layout.evergreen_dir
        atlas_dir = layout.atlas_dir
        areas_dir = resolved_vault / "20-Areas"
        parser = FrontmatterParser(resolved_vault)
        link_parser = LinkParser(resolved_vault)
        registry = ConceptRegistry(resolved_vault).load()

        object_metadata_items = [
            meta
            for meta in parser.parse_directory(evergreen_dir, recursive=True)
            if "_Candidates" not in Path(meta.path).parts
        ]
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
                            EMBEDDING_MODEL,
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
                truth_projection.objects,
            )
            conn.executemany(
                """
                INSERT INTO claims (pack, claim_id, object_id, claim_kind, claim_text, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                truth_projection.claims,
            )
            conn.executemany(
                """
                INSERT INTO claim_evidence (pack, claim_id, source_slug, evidence_kind, quote_text)
                VALUES (?, ?, ?, ?, ?)
                """,
                truth_projection.claim_evidence,
            )
            conn.executemany(
                """
                INSERT INTO relations (pack, source_object_id, target_object_id, relation_type, evidence_source_slug)
                VALUES (?, ?, ?, ?, ?)
                """,
                truth_projection.relations,
            )
            conn.executemany(
                """
                INSERT INTO compiled_summaries (pack, object_id, summary_text, source_slug)
                VALUES (?, ?, ?, ?)
                """,
                truth_projection.compiled_summaries,
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
                truth_projection.contradictions,
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
                truth_projection.graph_edges,
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
                truth_projection.graph_clusters,
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
            conn.executemany(
                """
                INSERT INTO page_embeddings (slug, chunk_index, section_title, chunk_text, embedding_blob, embedding_model)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                embedding_rows,
            )
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
                "embedding_chunks_indexed": len(embedding_rows),
                "objects_indexed": len(truth_projection.objects),
                "claims_indexed": len(truth_projection.claims),
                "relations_indexed": len(truth_projection.relations),
                "compiled_summaries_indexed": len(truth_projection.compiled_summaries),
                "contradictions_indexed": len(truth_projection.contradictions),
                "graph_edges_indexed": len(truth_projection.graph_edges),
                "graph_clusters_indexed": len(truth_projection.graph_clusters),
            }


def query_knowledge_index(vault_dir: Path, query: str, limit: int = 5) -> list[dict[str, str | int | float]]:
    _, layout = _ensure_knowledge_db(vault_dir)

    query_vector = _decode_embedding(_embed_text(query))
    with sqlite3.connect(layout.knowledge_db) as conn:
        rows = conn.execute(
            """
            SELECT slug, chunk_index, section_title, chunk_text, embedding_blob
            FROM page_embeddings
            """
        ).fetchall()

    scored = []
    for slug, chunk_index, section_title, chunk_text, embedding_blob in rows:
        score = _dot_product(query_vector, _decode_embedding(embedding_blob))
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
