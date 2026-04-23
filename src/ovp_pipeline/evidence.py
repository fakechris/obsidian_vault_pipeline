from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
import sqlite3
from itertools import islice
from pathlib import Path
from typing import Any

from .discovery import discover_related
from .extraction.artifacts import iter_run_results
from .knowledge_index import knowledge_index_stats, recent_audit_events
from .packs.base import BaseDomainPack
from .packs.loader import DEFAULT_PACK_NAME, load_pack
from .runtime import VaultLayout, resolve_vault_dir
from .truth_store import (
    EVIDENCE_STATUS_BROKEN,
    EVIDENCE_STATUS_STALE,
    EVIDENCE_STATUS_UNVERIFIED,
    EVIDENCE_STATUS_VERIFIED,
)

_EVIDENCE_CONTEXT_CHARS = 200


def _resolve_pack(pack: str | BaseDomainPack | None) -> BaseDomainPack:
    if isinstance(pack, BaseDomainPack):
        return pack
    return load_pack(pack or DEFAULT_PACK_NAME)


def _slug_object_kinds(vault_dir: Path, registry: Any | None = None) -> dict[str, str]:
    if registry is None:
        from .concept_registry import ConceptRegistry

        registry = ConceptRegistry(vault_dir).load()
    return {entry.slug: entry.kind for entry in registry.entries}


def _build_identity_evidence(
    vault_dir: Path,
    mentions: list[str],
    registry: Any | None = None,
    *,
    pack: BaseDomainPack,
) -> list[dict[str, object]]:
    if not mentions:
        return []
    if registry is None:
        from .concept_registry import ConceptRegistry

        registry = ConceptRegistry(vault_dir).load()

    evidence = []
    for mention in mentions:
        result = registry.resolve_mention(mention)
        evidence.append(
            {
                "channel": "identity",
                "mention": mention,
                "action": result.action.value if hasattr(result.action, "value") else str(result.action),
                "confidence": result.confidence,
                "entry_slug": result.entry.slug if result.entry else "",
                "pack": pack.name,
                "object_kind": getattr(registry.find_by_slug(result.entry.slug), "kind", "document") if result.entry else "",
                "ambiguous_slugs": [entry.slug for entry in result.ambiguous_entries],
            }
        )
    return evidence


def _build_retrieval_evidence(
    vault_dir: Path,
    query: str | None,
    mentions: list[str],
    limit: int,
    *,
    pack: BaseDomainPack,
) -> list[dict[str, object]]:
    retrieval_queries = []
    if query:
        retrieval_queries.append(query)
    retrieval_queries.extend(mention for mention in mentions if mention and mention not in retrieval_queries)

    results: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for item_query in retrieval_queries[:3]:
        for row in discover_related(vault_dir, item_query, engine="knowledge", limit=limit, pack=pack):
            normalized = {
                "channel": "retrieval",
                "query": item_query,
                "engine": row.get("engine", "knowledge"),
                "kind": row.get("kind", "semantic"),
                "slug": row.get("slug", ""),
                "title": row.get("title", ""),
                "pack": row.get("pack", pack.name),
                "object_kind": row.get("object_kind", "document"),
                "score": float(row.get("score") or 0.0),
                "snippet": row.get("snippet", ""),
                "path": row.get("path", ""),
            }
            key = (str(normalized["query"]), str(normalized["kind"]), str(normalized["slug"]))
            if key in seen:
                continue
            seen.add(key)
            results.append(normalized)
    return results[:limit]


def _build_graph_evidence(
    vault_dir: Path,
    slugs: list[str],
    limit: int,
    *,
    pack: BaseDomainPack,
    slug_kinds: dict[str, str],
) -> list[dict[str, object]]:
    if not slugs:
        return []

    knowledge_index_stats(vault_dir)
    layout = VaultLayout.from_vault(vault_dir)
    placeholders = ",".join("?" for _ in slugs)
    query = f"""
        SELECT source_slug, target_slug, link_type
        FROM page_links
        WHERE source_slug IN ({placeholders}) OR target_slug IN ({placeholders})
        LIMIT ?
    """
    with sqlite3.connect(layout.knowledge_db) as conn:
        rows = conn.execute(query, (*slugs, *slugs, limit)).fetchall()

    return [
        {
            "channel": "graph",
            "pack": pack.name,
            "source_slug": source_slug,
            "target_slug": target_slug,
            "source_kind": slug_kinds.get(source_slug, "document"),
            "target_kind": slug_kinds.get(target_slug, "document"),
            "link_type": link_type,
        }
        for source_slug, target_slug, link_type in rows
    ]


def _build_audit_evidence(
    vault_dir: Path,
    slugs: list[str],
    limit: int,
    *,
    pack: BaseDomainPack,
    slug_kinds: dict[str, str],
) -> list[dict[str, object]]:
    rows = recent_audit_events(vault_dir, limit=max(limit * 5, 10))
    if slugs:
        rows = [row for row in rows if row.get("slug") in slugs]
    return [
        {
            "channel": "audit",
            "pack": pack.name,
            "source_log": row.get("source_log", ""),
            "event_type": row.get("event_type", ""),
            "slug": row.get("slug", ""),
            "object_kind": slug_kinds.get(str(row.get("slug") or ""), "document"),
            "timestamp": row.get("timestamp", ""),
        }
        for row in rows[:limit]
    ]


def _build_extraction_evidence(
    vault_dir: Path,
    limit: int,
    *,
    pack: BaseDomainPack,
    extraction_profile: str | None,
) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for result in iter_run_results(VaultLayout.from_vault(vault_dir), pack_name=pack.name, profile_name=extraction_profile):
        for record in result.records:
            if len(evidence) >= limit:
                return evidence
            span = record.spans[0] if record.spans else None
            evidence.append(
                {
                    "channel": "extraction",
                    "pack": pack.name,
                    "profile": result.profile_name,
                    "object_kind": "document",
                    "source_path": result.source_path,
                    "quote": span.quote if span else "",
                    "char_start": span.char_start if span else 0,
                    "char_end": span.char_end if span else 0,
                    "section_title": span.section_title if span else "",
                    "values": record.values,
                }
            )
    return list(islice(evidence, limit))


def build_evidence_payload(
    vault_dir: Path,
    *,
    query: str | None = None,
    mentions: list[str] | None = None,
    slugs: list[str] | None = None,
    limit: int = 5,
    registry: Any | None = None,
    pack: str | BaseDomainPack | None = None,
    include_extraction: bool = False,
    extraction_profile: str | None = None,
) -> dict[str, list[dict[str, object]]]:
    resolved_vault = resolve_vault_dir(vault_dir)
    resolved_pack = _resolve_pack(pack)
    mentions = [mention for mention in (mentions or []) if mention]
    identity_evidence = _build_identity_evidence(
        resolved_vault,
        mentions or ([query] if query else []),
        registry=registry,
        pack=resolved_pack,
    )
    retrieval_evidence = _build_retrieval_evidence(
        resolved_vault,
        query,
        mentions,
        limit=limit,
        pack=resolved_pack,
    )

    derived_slugs = [str(row.get("slug") or "") for row in retrieval_evidence if row.get("slug")]
    graph_targets = list(dict.fromkeys([*(slugs or []), *derived_slugs]))
    slug_kinds = _slug_object_kinds(resolved_vault, registry=registry)

    payload = {
        "identity_evidence": identity_evidence,
        "retrieval_evidence": retrieval_evidence,
        "graph_evidence": _build_graph_evidence(
            resolved_vault,
            graph_targets,
            limit=limit,
            pack=resolved_pack,
            slug_kinds=slug_kinds,
        ),
        "audit_evidence": _build_audit_evidence(
            resolved_vault,
            graph_targets,
            limit=limit,
            pack=resolved_pack,
            slug_kinds=slug_kinds,
        ),
    }
    if include_extraction:
        payload["extraction_evidence"] = _build_extraction_evidence(
            resolved_vault,
            limit=limit,
            pack=resolved_pack,
            extraction_profile=extraction_profile,
        )
    return payload


# ---------------------------------------------------------------------------
# Phase 33 — re-locatable evidence (locator / hash / context / verify)
# ---------------------------------------------------------------------------


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_source_path(source_path: Path | str, vault_dir: Path | str | None) -> Path:
    """Resolve an evidence ``source_path`` against the vault root.

    For relative paths, only the vault-rooted resolution is honored — falling
    back to a CWD-relative path would silently hash a different file (or a
    file outside the vault) when the verifier is invoked from a non-vault
    working directory.
    """
    candidate = Path(source_path)
    if candidate.is_absolute():
        return candidate
    if vault_dir is not None:
        return (Path(vault_dir) / candidate).resolve()
    return candidate


def compute_content_hash(source_path: Path | str, *, vault_dir: Path | str | None = None) -> str:
    """SHA-256 of the source file's bytes, or empty string when unreadable.

    The hash anchors a ``claim_evidence`` row to a specific snapshot of its
    source. When the source mutates the hash diverges and the verifier flips
    status to ``stale``.
    """
    if not source_path:
        return ""
    path = _resolve_source_path(source_path, vault_dir)
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def compute_locator(
    source_path: Path | str,
    quote_text: str,
    *,
    vault_dir: Path | str | None = None,
) -> str:
    """Best-effort ``section#heading@paragraph_index`` pointer for a quote.

    Returns ``""`` when the quote cannot be located (e.g. paraphrased) — in
    that case the verifier still has ``content_hash`` to fall back on.
    """
    if not quote_text:
        return ""
    path = _resolve_source_path(source_path, vault_dir)
    if not path.exists() or not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    needle = quote_text.strip()
    if not needle:
        return ""

    section_heading = ""
    paragraph_index = 0
    matched_paragraph = -1
    paragraphs_in_section: list[str] = []
    sections: list[tuple[str, list[str]]] = [("", [])]

    paragraph_buffer: list[str] = []

    def flush_paragraph() -> None:
        if paragraph_buffer:
            sections[-1][1].append("\n".join(paragraph_buffer).strip())
            paragraph_buffer.clear()

    for line in text.splitlines():
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            flush_paragraph()
            heading_title = heading_match.group(2).strip()
            sections.append((heading_title, []))
            continue
        if line.strip():
            paragraph_buffer.append(line)
        else:
            flush_paragraph()
    flush_paragraph()

    for heading, paragraphs in sections:
        for index, paragraph in enumerate(paragraphs):
            if needle in paragraph:
                section_heading = heading
                paragraph_index = index
                matched_paragraph = index
                paragraphs_in_section = paragraphs
                break
        if matched_paragraph >= 0:
            break

    _ = paragraphs_in_section  # informational; reserved for future locator metadata
    if matched_paragraph < 0:
        return ""
    safe_heading = re.sub(r"\s+", "-", section_heading.strip().lower()) if section_heading else ""
    return f"section#{safe_heading}@{paragraph_index}"


def compute_retrieval_context(
    source_path: Path | str,
    quote_text: str,
    *,
    vault_dir: Path | str | None = None,
    radius: int = _EVIDENCE_CONTEXT_CHARS,
) -> str:
    """Surrounding ``±radius`` characters around ``quote_text`` in the source.

    Empty string when source missing or the quote can't be found verbatim. The
    UI uses this to render an evidence preview without re-reading the file.
    """
    if not quote_text:
        return ""
    path = _resolve_source_path(source_path, vault_dir)
    if not path.exists() or not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    needle = quote_text.strip()
    if not needle:
        return ""
    location = text.find(needle)
    if location == -1:
        return ""
    start = max(0, location - radius)
    end = min(len(text), location + len(needle) + radius)
    return text[start:end]


def verify_evidence_row(
    row: dict[str, Any],
    vault_dir: Path | str,
) -> tuple[str, str]:
    """Return ``(status, verified_at)`` for a single ``claim_evidence`` row.

    ``row`` is keyed dict-style (``source_slug``, ``quote_text``,
    ``content_hash``). Resolution rule:

    * source path missing / unreadable → ``broken`` (timestamped)
    * stored ``content_hash`` empty → ``unverified`` (no anchor to compare)
    * recomputed hash matches → ``verified``
    * recomputed hash diverges → ``stale``

    Quote re-location (locator drift) is intentionally not promoted to
    ``stale`` — quotes can be edited cosmetically without invalidating the
    underlying claim. Hash drift is the load-bearing signal.
    """
    source_path = str(row.get("source_slug") or row.get("source_path") or "")
    if not source_path:
        return EVIDENCE_STATUS_UNVERIFIED, ""

    resolved = _resolve_source_path(source_path, vault_dir)
    if not resolved.exists() or not resolved.is_file():
        return EVIDENCE_STATUS_BROKEN, _utc_now_text()

    stored_hash = str(row.get("content_hash") or "")
    if not stored_hash:
        return EVIDENCE_STATUS_UNVERIFIED, ""

    actual_hash = compute_content_hash(resolved)
    if actual_hash and actual_hash == stored_hash:
        return EVIDENCE_STATUS_VERIFIED, _utc_now_text()
    return EVIDENCE_STATUS_STALE, _utc_now_text()
