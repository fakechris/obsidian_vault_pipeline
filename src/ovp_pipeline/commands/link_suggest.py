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

After RRF the top ``--gate-prefilter`` candidates per page are sent to an
LLM second-opinion gate (Phase 38 plan §2.2 step 3). The gate classifies
each candidate as ``link`` or ``skip`` with a confidence + 1-line rationale,
and only ``link`` decisions with ``confidence >= --gate-threshold`` survive
into ``--apply``. Decisions are cached in
``60-Logs/link-suggestions/.gate-cache.jsonl`` so a second dry-run / apply
pass doesn't re-bill the LLM. When no API key / litellm is available the
gate degrades to the previous RRF-only behavior (every retrieved candidate
treated as ``link`` with confidence ``rrf_score``).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from ..runtime import VaultLayout, resolve_vault_dir
    from ..knowledge_index import (
        ensure_knowledge_db_current,
        query_knowledge_index,
        sanitize_fts_query,
        search_knowledge_index,
    )
    from ..llm_defaults import (
        DEFAULT_LITELLM_TIMEOUT_SECONDS,
        DEFAULT_MINIMAX_MODEL,
        normalize_model_for_api_base,
        resolve_api_base,
        resolve_api_key,
    )
except ImportError:
    from ovp_pipeline.runtime import VaultLayout, resolve_vault_dir  # type: ignore
    from ovp_pipeline.knowledge_index import (  # type: ignore
        ensure_knowledge_db_current,
        query_knowledge_index,
        sanitize_fts_query,
        search_knowledge_index,
    )
    from ovp_pipeline.llm_defaults import (  # type: ignore
        DEFAULT_LITELLM_TIMEOUT_SECONDS,
        DEFAULT_MINIMAX_MODEL,
        normalize_model_for_api_base,
        resolve_api_base,
        resolve_api_key,
    )

try:
    import litellm  # type: ignore

    _LITELLM_AVAILABLE = True
except ImportError:
    _LITELLM_AVAILABLE = False


DEFAULT_MIN_LINKS = 3
DEFAULT_CANDIDATES_PER_PAGE = 20
DEFAULT_SUGGESTIONS_PER_PAGE = 5
DEFAULT_GATE_PREFILTER = 8
DEFAULT_GATE_THRESHOLD = 0.6
RRF_K = 60
GATE_CACHE_FILENAME = ".gate-cache.jsonl"
BACKFILL_HEADING = "## 🔗 自动建议链接 (link-suggest)"
BACKFILL_MARKER = "<!-- link-suggest:backfill -->"

LINK_SUGGEST_GATE_PROMPT = """你是一个 wikilink 质量过滤器。给定一篇源文章和若干候选目标 evergreen 概念，判断每个候选是否值得在源文章里 backfill 一个 wikilink。

判定原则：
1. 候选概念应是源文章的 prose 真正讨论 / 例证 / 反例引用的对象，不是泛泛同领域。
2. 不要因为同领域就 link；关注是否有 substantive 的概念关联（同一现象、相同机制、互为前置 / 反例）。
3. confidence 要保守：0.8+ 强相关；0.6-0.79 合理；<0.6 一律 skip。
4. rationale 用 ≤30 字中文，说明为什么 link 或 skip。
5. 只输出 JSON，不要解释。

输入：
{"source": {"slug": "...", "title": "...", "preview": "..."}, "candidates": [{"slug": "...", "title": "..."}]}

输出：
{"decisions": [{"slug": "...", "decision": "link"|"skip", "confidence": 0.0, "rationale": "..."}]}
"""


GateClient = Callable[[str, str], str]


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _make_default_gate_client(model: str | None = None) -> GateClient | None:
    """Return a callable wrapping LiteLLM, or ``None`` if litellm/API key are
    unavailable. Tests monkey-patch by passing their own callable directly to
    ``run_link_suggest``."""
    if not _LITELLM_AVAILABLE:
        return None
    api_key = resolve_api_key(None)
    if not api_key:
        return None
    api_base = resolve_api_base(None)
    resolved_model = normalize_model_for_api_base(
        model or DEFAULT_MINIMAX_MODEL,
        api_type="anthropic",
        api_base=api_base,
        default_model=DEFAULT_MINIMAX_MODEL,
    )

    def _call(system_prompt: str, user_prompt: str) -> str:
        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 1500,
            "timeout": DEFAULT_LITELLM_TIMEOUT_SECONDS,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if api_base:
            kwargs["api_base"] = api_base
        response = litellm.completion(**kwargs)
        return response.choices[0].message.content or ""

    return _call


def _load_gate_cache(log_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Read prior gate decisions; last-write-wins per ``(source, target)`` key
    so re-running on the same vault doesn't re-bill the LLM."""
    cache_path = log_dir / GATE_CACHE_FILENAME
    cache: dict[tuple[str, str], dict[str, Any]] = {}
    if not cache_path.exists():
        return cache
    for line in cache_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        source = row.get("source_slug")
        target = row.get("target_slug")
        if not source or not target:
            continue
        cache[(source, target)] = row
    return cache


def _append_gate_cache(log_dir: Path, source_slug: str, decisions: list[dict[str, Any]]) -> None:
    if not decisions:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    cache_path = log_dir / GATE_CACHE_FILENAME
    ts = datetime.now(timezone.utc).isoformat()
    with cache_path.open("a", encoding="utf-8") as fh:
        for decision in decisions:
            row = {
                "source_slug": source_slug,
                "target_slug": decision["slug"],
                "decision": decision["decision"],
                "confidence": decision["confidence"],
                "rationale": decision.get("rationale", ""),
                "ts": ts,
            }
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _build_gate_user_prompt(page: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    body = (page.get("body") or "")[:_BODY_PREVIEW_CHARS]
    payload = {
        "source": {
            "slug": page["slug"],
            "title": page.get("title") or page["slug"],
            "preview": body,
        },
        "candidates": [
            {"slug": c["slug"], "title": c.get("title") or c["slug"]} for c in candidates
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _parse_gate_response(text: str) -> list[dict[str, Any]]:
    """Tolerant JSON parser. The model occasionally wraps output in a
    ```json fence or trailing prose; pull out the first balanced object and
    keep going."""
    if not text:
        return []
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # strip code fence
        cleaned = cleaned.lstrip("`").lstrip("json").strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        obj = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return []
    decisions = obj.get("decisions") if isinstance(obj, dict) else None
    if not isinstance(decisions, list):
        return []
    out: list[dict[str, Any]] = []
    for item in decisions:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        decision = item.get("decision")
        if not slug or decision not in {"link", "skip"}:
            continue
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        out.append(
            {
                "slug": str(slug),
                "decision": decision,
                "confidence": max(0.0, min(1.0, confidence)),
                "rationale": str(item.get("rationale", "") or ""),
            }
        )
    return out


def _llm_gate_for_page(
    page: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    gate_client: GateClient | None,
    cache: dict[tuple[str, str], dict[str, Any]],
    log_dir: Path,
) -> dict[str, dict[str, Any]]:
    """Return ``{target_slug: {"decision","confidence","rationale"}}`` for every
    candidate. ``run_link_suggest`` no longer calls this with ``gate_client=None``
    (that path is short-circuited to the legacy RRF-only branch upstream), but
    the defensive fallback below sets ``confidence=1.0`` so any direct caller
    still gets the documented ``accept all`` semantics instead of having every
    candidate silently fail the threshold filter."""
    decisions_by_slug: dict[str, dict[str, Any]] = {}
    cache_misses: list[dict[str, Any]] = []
    for candidate in candidates:
        slug = candidate["slug"]
        cached = cache.get((page["slug"], slug))
        if cached:
            decisions_by_slug[slug] = {
                "decision": cached["decision"],
                "confidence": float(cached.get("confidence", 0.0)),
                "rationale": cached.get("rationale", ""),
            }
        else:
            cache_misses.append(candidate)

    if not cache_misses:
        return decisions_by_slug

    if gate_client is None:
        for candidate in cache_misses:
            decisions_by_slug[candidate["slug"]] = {
                "decision": "link",
                "confidence": 1.0,
                "rationale": "rrf-only (no LLM gate)",
            }
        return decisions_by_slug

    user_prompt = _build_gate_user_prompt(page, cache_misses)
    try:
        raw = gate_client(LINK_SUGGEST_GATE_PROMPT, user_prompt)
    except Exception as exc:  # noqa: BLE001 — gate is best-effort
        for candidate in cache_misses:
            decisions_by_slug[candidate["slug"]] = {
                "decision": "skip",
                "confidence": 0.0,
                "rationale": f"gate error: {type(exc).__name__}",
            }
        return decisions_by_slug

    parsed = _parse_gate_response(raw)
    parsed_by_slug = {item["slug"]: item for item in parsed}
    new_cache_rows: list[dict[str, Any]] = []
    miss_slugs = {c["slug"] for c in cache_misses}
    for candidate in cache_misses:
        slug = candidate["slug"]
        decision = parsed_by_slug.get(slug)
        if decision is None:
            decision = {
                "slug": slug,
                "decision": "skip",
                "confidence": 0.0,
                "rationale": "gate omitted candidate",
            }
        decisions_by_slug[slug] = {
            "decision": decision["decision"],
            "confidence": decision["confidence"],
            "rationale": decision["rationale"],
        }
        new_cache_rows.append(decision)
    # Persist whatever the gate returned for the misses so re-runs are cheap.
    new_cache_rows = [r for r in new_cache_rows if r["slug"] in miss_slugs]
    _append_gate_cache(log_dir, page["slug"], new_cache_rows)
    return decisions_by_slug


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
    gated: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = {
        "run_id": run_id,
        "source_slug": page["slug"],
        "source_title": page.get("title", ""),
        "source_path": page.get("path", ""),
        "source_link_out_count": page.get("link_out_count", 0),
        "target_slug": suggestion["slug"],
        "target_title": suggestion.get("title", ""),
        "rrf_score": round(suggestion.get("rrf_score", 0.0), 6),
    }
    if gated is not None:
        row["decision"] = gated["decision"]
        row["confidence"] = round(float(gated["confidence"]), 4)
        row["rationale"] = gated.get("rationale", "")
    return row


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
    use_llm_gate: bool = True,
    gate_threshold: float = DEFAULT_GATE_THRESHOLD,
    gate_prefilter: int = DEFAULT_GATE_PREFILTER,
    gate_model: str | None = None,
    gate_client: GateClient | None = None,
) -> dict[str, Any]:
    """Top-level entry. Emits the JSONL log unconditionally; only mutates
    markdown when ``apply`` *and* ``confirm`` are both True.

    Gate flow: top-``gate_prefilter`` RRF candidates → LLM gate → keep only
    ``decision == 'link'`` AND ``confidence >= gate_threshold`` → cap at
    ``suggestions_per_page`` for the apply step. When ``use_llm_gate=False`` or
    no API key / litellm is available, the gate degrades cleanly to the
    legacy RRF-only path (every retrieved candidate accepted, no gate
    metadata in rows). The previous fallback returned ``confidence=rrf_score``
    inside the gate path, which then failed the threshold filter and silently
    rejected everything — the explicit fall-through avoids that trap."""
    layout = VaultLayout.from_vault(vault_dir)
    if apply and not confirm:
        raise ValueError("--apply requires --confirm")

    # Materialize knowledge.db before the first SQLite read; otherwise a
    # fresh-clone vault hits "unable to open database file" / "no such table".
    ensure_knowledge_db_current(layout.vault_dir)

    pages = _iter_under_linked_pages(
        layout, min_links=min_links, note_types=note_types, limit=limit
    )

    log_root = log_dir or (layout.vault_dir / "60-Logs" / "link-suggestions")
    log_root.mkdir(parents=True, exist_ok=True)

    # Gate setup: prefer caller-supplied client (tests) over env-resolved one.
    effective_client: GateClient | None = None
    if use_llm_gate:
        effective_client = gate_client or _make_default_gate_client(gate_model)
        if effective_client is None:
            # No API key / litellm → the gate would reject everything via the
            # threshold filter. Disable cleanly so reviewers see RRF rows
            # (no decision/confidence) instead of an empty apply.
            print(
                "ovp-link-suggest: --llm-gate requested but no LLM client available; "
                "falling back to RRF-only (no gate metadata in rows).",
                file=sys.stderr,
            )
            use_llm_gate = False
    cache = _load_gate_cache(log_root) if use_llm_gate else {}

    # gate_prefilter is the HARD cap on what the LLM sees; apply takes the
    # top-suggestions_per_page from gate-passed. If suggestions_per_page >
    # gate_prefilter, the gate becomes the bottleneck (correctly) — we don't
    # quietly send more to the LLM than --gate-prefilter requests.
    retrieve_n = gate_prefilter if use_llm_gate else suggestions_per_page

    run_id = _new_run_id()
    rows: list[dict[str, Any]] = []
    files_mutated = 0
    suggestions_total = 0
    gate_passed_total = 0
    for page in pages:
        candidates = _suggest_for_page(
            layout,
            page,
            candidates_per_page=candidates_per_page,
            suggestions_per_page=retrieve_n,
        )
        if use_llm_gate:
            decisions = _llm_gate_for_page(
                page,
                candidates,
                gate_client=effective_client,
                cache=cache,
                log_dir=log_root,
            )
            # Emit one row per retrieved candidate (link AND skip), preserving RRF
            # order so reviewers can diff what the gate filtered out.
            page_passed: list[dict[str, Any]] = []
            for candidate in candidates:
                gated = decisions.get(candidate["slug"])
                rows.append(_suggestion_row(page, candidate, run_id=run_id, gated=gated))
                if (
                    gated is not None
                    and gated["decision"] == "link"
                    and float(gated["confidence"]) >= gate_threshold
                ):
                    page_passed.append(candidate)
            suggestions_total += len(candidates)
            gate_passed_total += len(page_passed)
            to_apply = page_passed[:suggestions_per_page]
        else:
            # Legacy RRF-only path: skip the gate entirely, omit gate metadata
            # from rows, and apply the top-suggestions_per_page directly.
            for candidate in candidates:
                rows.append(_suggestion_row(page, candidate, run_id=run_id))
            suggestions_total += len(candidates)
            gate_passed_total += len(candidates)
            to_apply = candidates[:suggestions_per_page]
        if apply and confirm and _apply_to_markdown(layout, page, to_apply):
            files_mutated += 1

    log_path = log_root / f"{run_id}.jsonl"
    _emit_jsonl(rows, log_path)

    return {
        "run_id": run_id,
        "log_path": str(log_path),
        "pages_examined": len(pages),
        "suggestions_emitted": suggestions_total,
        "gate_passed": gate_passed_total,
        "files_mutated": files_mutated,
        "applied": apply and confirm,
        "gate_enabled": use_llm_gate and effective_client is not None,
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
    gate_group = parser.add_mutually_exclusive_group()
    gate_group.add_argument(
        "--llm-gate",
        dest="use_llm_gate",
        action="store_true",
        default=True,
        help="Filter RRF candidates through an LLM second-opinion gate (default).",
    )
    gate_group.add_argument(
        "--no-llm-gate",
        dest="use_llm_gate",
        action="store_false",
        help="Disable the LLM gate; emit RRF results directly (legacy behavior).",
    )
    parser.add_argument(
        "--gate-threshold",
        type=float,
        default=DEFAULT_GATE_THRESHOLD,
        help=(
            f"Min confidence for a 'link' decision to survive into --apply "
            f"(default: {DEFAULT_GATE_THRESHOLD})"
        ),
    )
    parser.add_argument(
        "--gate-prefilter",
        type=int,
        default=DEFAULT_GATE_PREFILTER,
        help=f"Top-N RRF candidates per page sent to the gate (default: {DEFAULT_GATE_PREFILTER})",
    )
    parser.add_argument(
        "--gate-model",
        type=str,
        default=None,
        help="Override the LLM model for the gate (default: env-resolved MiniMax).",
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
            use_llm_gate=args.use_llm_gate,
            gate_threshold=args.gate_threshold,
            gate_prefilter=args.gate_prefilter,
            gate_model=args.gate_model,
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
    print(
        f"Gate passed:         {summary['gate_passed']}  (gate={'on' if summary['gate_enabled'] else 'off'})"
    )
    print(f"Files mutated:       {summary['files_mutated']}")
    print(f"Mode:                {'APPLY' if summary['applied'] else 'dry-run'}")
    print(f"Log:                 {summary['log_path']}")
    if not summary["applied"]:
        print()
        print("Pass --apply --confirm to write the backfill section into source files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
