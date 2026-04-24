"""One-shot backfill: propose wikilinks for pages that are under-linked.

The Phase 38 link-density audit found 73% of deep_dives carry zero outbound
wikilinks because the extractor (pre-Phase 38) didn't ground its
``related_concepts`` in the existing registry. This command repairs the
legacy backlog: for every page with fewer than ``--min-links`` outbound
wikilinks, query the knowledge index for related concepts and emit a JSONL
suggestion log. ``--apply --confirm`` writes a clearly-labelled section of
backfilled wikilinks to the source markdown.

Two retrieval signals are merged:
- BM25 over ``page_fts`` (substring-style, picks up exact term hits)
- Vector dot-product over ``page_embeddings`` (catches paraphrases)

Each candidate gets a fused score = BM25_rank_score + vector_rank_score
(reciprocal rank, k=60). Self-references and existing outbound targets are
dropped before ranking. The naive fusion here will be replaced by the proper
RRF + bi-temporal decay layer in Stage B.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from ..runtime import VaultLayout, resolve_vault_dir
    from ..knowledge_index import (
        query_knowledge_index,
        sanitize_fts_query,
        search_knowledge_index,
    )
except ImportError:
    from ovp_pipeline.runtime import VaultLayout, resolve_vault_dir  # type: ignore
    from ovp_pipeline.knowledge_index import (  # type: ignore
        query_knowledge_index,
        sanitize_fts_query,
        search_knowledge_index,
    )


DEFAULT_MIN_LINKS = 3
DEFAULT_CANDIDATES_PER_PAGE = 20
DEFAULT_SUGGESTIONS_PER_PAGE = 5
RRF_K = 60
BACKFILL_HEADING = "## 🔗 自动建议链接 (link-suggest)"
BACKFILL_MARKER = "<!-- link-suggest:backfill -->"


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


_BODY_PREVIEW_CHARS = 800


def _iter_under_linked_pages(
    layout: VaultLayout,
    *,
    min_links: int,
    note_types: tuple[str, ...] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return pages whose outbound wikilink count is below ``min_links``.
    Body is truncated in SQL to ``_BODY_PREVIEW_CHARS`` (the suggester only
    uses the first 800 chars) and ``limit`` is pushed into the query so we
    never materialize the whole vault in memory."""
    type_clause = ""
    params: list[Any] = []
    if note_types:
        placeholders = ",".join("?" for _ in note_types)
        type_clause = f"WHERE pi.note_type IN ({placeholders})"  # noqa: S608 — placeholders only, values bound via params
        params.extend(note_types)
    params.append(min_links)
    limit_clause = ""
    if limit is not None:
        limit_clause = "LIMIT ?"
        params.append(limit)
    with sqlite3.connect(layout.knowledge_db) as conn:
        rows = conn.execute(
            f"""
            SELECT pi.slug, pi.title, pi.note_type, pi.path,
                   SUBSTR(pi.body, 1, ?) AS body,
                   COUNT(pl.target_slug) AS link_out_count
            FROM pages_index pi
            LEFT JOIN page_links pl
              ON pl.source_slug = pi.slug AND pl.link_type = 'wikilink'
            {type_clause}
            GROUP BY pi.slug
            HAVING link_out_count < ?
            ORDER BY pi.slug
            {limit_clause}
            """,  # noqa: S608 — clauses are placeholder-only, all values bound via params
            (_BODY_PREVIEW_CHARS, *params),
        ).fetchall()
    return [
        {
            "slug": slug,
            "title": title,
            "note_type": note_type,
            "path": path,
            "body": body,
            "link_out_count": link_out_count,
        }
        for slug, title, note_type, path, body, link_out_count in rows
    ]


def _existing_link_targets(layout: VaultLayout, source_slug: str) -> set[str]:
    with sqlite3.connect(layout.knowledge_db) as conn:
        rows = conn.execute(
            "SELECT target_slug FROM page_links WHERE source_slug = ?",
            (source_slug,),
        ).fetchall()
    return {row[0] for row in rows}


def _fuse_candidates(
    bm25_hits: list[dict[str, Any]],
    vector_hits: list[dict[str, Any]],
    *,
    self_slug: str,
    skip_slugs: set[str],
) -> list[dict[str, Any]]:
    """Merge BM25 + vector results via reciprocal rank fusion. Vector hits are
    keyed on ``slug`` (collapsing chunk_index — best chunk per slug wins)."""
    fused: dict[str, dict[str, Any]] = {}
    for rank, hit in enumerate(bm25_hits, start=1):
        slug = str(hit.get("slug") or "")
        if not slug or slug == self_slug or slug in skip_slugs:
            continue
        record = fused.setdefault(
            slug,
            {"slug": slug, "title": hit.get("title", ""), "rrf_score": 0.0},
        )
        record["rrf_score"] += 1.0 / (RRF_K + rank)
    seen_vector_slugs: set[str] = set()
    for rank, hit in enumerate(vector_hits, start=1):
        slug = str(hit.get("slug") or "")
        if not slug or slug == self_slug or slug in skip_slugs:
            continue
        if slug in seen_vector_slugs:
            continue
        seen_vector_slugs.add(slug)
        record = fused.setdefault(
            slug,
            {"slug": slug, "title": hit.get("section_title") or slug, "rrf_score": 0.0},
        )
        record["rrf_score"] += 1.0 / (RRF_K + rank)
    return sorted(fused.values(), key=lambda item: item["rrf_score"], reverse=True)


def _suggest_for_page(
    layout: VaultLayout,
    page: dict[str, Any],
    *,
    candidates_per_page: int,
    suggestions_per_page: int,
) -> list[dict[str, Any]]:
    title = page.get("title") or page["slug"]
    body = page.get("body") or ""
    query_text = f"{title}\n{body[:_BODY_PREVIEW_CHARS]}".strip()
    if not query_text:
        return []
    # FTS5 MATCH parses `-`/`:`/`"` as syntax (e.g. `multi-step` →
    # `multi NOT step` → "no such column: step"); without sanitizing, the
    # blind `except` below would silently collapse the BM25 branch and the
    # command would quietly degrade to vector-only retrieval. See
    # `knowledge_index.sanitize_fts_query`.
    bm25_query = sanitize_fts_query(query_text)
    bm25: list[dict[str, Any]] = []
    if bm25_query:
        try:
            bm25 = search_knowledge_index(layout.vault_dir, bm25_query, limit=candidates_per_page)
        except Exception:
            bm25 = []
    try:
        vector = query_knowledge_index(layout.vault_dir, query_text, limit=candidates_per_page)
    except Exception:
        vector = []
    skip = _existing_link_targets(layout, page["slug"])
    fused = _fuse_candidates(bm25, vector, self_slug=page["slug"], skip_slugs=skip)
    return fused[:suggestions_per_page]


def _suggestion_row(
    page: dict[str, Any],
    suggestion: dict[str, Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "source_slug": page["slug"],
        "source_title": page.get("title", ""),
        "source_path": page.get("path", ""),
        "source_link_out_count": page.get("link_out_count", 0),
        "target_slug": suggestion["slug"],
        "target_title": suggestion.get("title", ""),
        "rrf_score": round(suggestion.get("rrf_score", 0.0), 6),
    }


def _emit_jsonl(rows: list[dict[str, Any]], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _apply_to_markdown(
    layout: VaultLayout, page: dict[str, Any], suggestions: list[dict[str, Any]]
) -> bool:
    """Append a clearly-labelled section to the source markdown. Returns True
    when the file is mutated, False when the marker is already present
    (idempotent re-runs are a no-op)."""
    if not suggestions:
        return False
    source_path = layout.vault_dir / page["path"]
    if not source_path.exists():
        return False
    text = source_path.read_text(encoding="utf-8")
    if BACKFILL_MARKER in text:
        return False
    block_lines = [BACKFILL_HEADING, BACKFILL_MARKER, ""]
    for suggestion in suggestions:
        slug = suggestion["slug"]
        title = suggestion.get("title") or slug
        if title and title != slug:
            block_lines.append(f"- [[{slug}|{title}]]")
        else:
            block_lines.append(f"- [[{slug}]]")
    block_lines.append("")
    new_text = text.rstrip() + "\n\n" + "\n".join(block_lines)
    source_path.write_text(new_text, encoding="utf-8")
    return True


def run_link_suggest(
    vault_dir: Path,
    *,
    min_links: int = DEFAULT_MIN_LINKS,
    note_types: tuple[str, ...] | None = None,
    candidates_per_page: int = DEFAULT_CANDIDATES_PER_PAGE,
    suggestions_per_page: int = DEFAULT_SUGGESTIONS_PER_PAGE,
    apply: bool = False,
    confirm: bool = False,
    limit: int | None = None,
    log_dir: Path | None = None,
) -> dict[str, Any]:
    """Top-level entry. Emits the JSONL log unconditionally; only mutates
    markdown when ``apply`` *and* ``confirm`` are both True."""
    layout = VaultLayout.from_vault(vault_dir)
    if apply and not confirm:
        raise ValueError("--apply requires --confirm")

    pages = _iter_under_linked_pages(
        layout, min_links=min_links, note_types=note_types, limit=limit
    )

    run_id = _new_run_id()
    rows: list[dict[str, Any]] = []
    files_mutated = 0
    suggestions_total = 0
    for page in pages:
        suggestions = _suggest_for_page(
            layout,
            page,
            candidates_per_page=candidates_per_page,
            suggestions_per_page=suggestions_per_page,
        )
        for suggestion in suggestions:
            rows.append(_suggestion_row(page, suggestion, run_id=run_id))
        suggestions_total += len(suggestions)
        if apply and confirm and _apply_to_markdown(layout, page, suggestions):
            files_mutated += 1

    log_root = log_dir or (layout.vault_dir / "60-Logs" / "link-suggestions")
    log_path = log_root / f"{run_id}.jsonl"
    _emit_jsonl(rows, log_path)

    return {
        "run_id": run_id,
        "log_path": str(log_path),
        "pages_examined": len(pages),
        "suggestions_emitted": suggestions_total,
        "files_mutated": files_mutated,
        "applied": apply and confirm,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Propose wikilinks for under-linked pages and (optionally) backfill them.",
    )
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault root (default: cwd)")
    parser.add_argument(
        "--min-links",
        type=int,
        default=DEFAULT_MIN_LINKS,
        help=f"Pages with fewer outbound wikilinks are eligible (default: {DEFAULT_MIN_LINKS})",
    )
    parser.add_argument(
        "--note-type",
        action="append",
        default=None,
        help="Restrict to these note_type values (repeatable). Default: all.",
    )
    parser.add_argument(
        "--candidates",
        type=int,
        default=DEFAULT_CANDIDATES_PER_PAGE,
        help=f"Top-k retrieved per side before fusion (default: {DEFAULT_CANDIDATES_PER_PAGE})",
    )
    parser.add_argument(
        "--suggestions",
        type=int,
        default=DEFAULT_SUGGESTIONS_PER_PAGE,
        help=f"Suggestions to keep per page (default: {DEFAULT_SUGGESTIONS_PER_PAGE})",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Process at most N pages (smoke testing)."
    )
    parser.add_argument(
        "--apply", action="store_true", help="Rewrite source markdown to append a backfill section."
    )
    parser.add_argument(
        "--confirm", action="store_true", help="Required with --apply to actually mutate files."
    )
    parser.add_argument("--json", action="store_true", help="Print structured summary to stdout.")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    note_types = tuple(args.note_type) if args.note_type else None
    try:
        summary = run_link_suggest(
            vault_dir,
            min_links=args.min_links,
            note_types=note_types,
            candidates_per_page=args.candidates,
            suggestions_per_page=args.suggestions,
            apply=args.apply,
            confirm=args.confirm,
            limit=args.limit,
        )
    except ValueError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    print("=" * 60)
    print("LINK SUGGEST SUMMARY")
    print("=" * 60)
    print(f"Run ID:              {summary['run_id']}")
    print(f"Pages examined:      {summary['pages_examined']}")
    print(f"Suggestions emitted: {summary['suggestions_emitted']}")
    print(f"Files mutated:       {summary['files_mutated']}")
    print(f"Mode:                {'APPLY' if summary['applied'] else 'dry-run'}")
    print(f"Log:                 {summary['log_path']}")
    if not summary["applied"]:
        print()
        print("Pass --apply --confirm to write the backfill section into source files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
