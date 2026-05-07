"""Shared constants, caches, and utility functions for truth_api and governance_api.

This module exists to break the circular dependency that would arise when
splitting truth_api.py into domain-focused sub-modules.  Everything here
is a leaf dependency — it imports only from stdlib, yaml, and the OVP
runtime / pack-resolution layers.
"""

from __future__ import annotations

from datetime import datetime, timezone
import functools
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from .knowledge_index import ensure_knowledge_db_current
from .pack_resolution import iter_compatible_packs
from .packs.loader import DEFAULT_WORKFLOW_PACK_NAME
from .runtime import VaultLayout, iter_jsonl, resolve_vault_dir

MAX_PAGE_SIZE = 500
LOGGER = logging.getLogger("ovp_pipeline.truth_api")
_FENCED_FRONTMATTER_RE = re.compile(r"^```ya?ml\s*\n---\n(.*?)\n---\n```\s*\n?", re.DOTALL)
_REVIEW_AUDIT_LOG_NAME = "review-actions"
_SIGNAL_LOG_NAME = "signals"
_ACTION_LOG_NAME = "actions"
_ACTION_RUNNING_STALE_AFTER_SECONDS = 3600

_SOURCE_NOTE_INDEX_CACHE: dict[
    tuple[str, tuple[tuple[str, int, int], ...]], dict[str, list[dict[str, str]]]
] = {}
_PIPELINE_LOG_INDEX_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
_DEEP_DIVE_OBJECT_MAP_CACHE: dict[tuple[str, int, int], dict[str, list[dict[str, str]]]] = {}
_SIGNAL_LEDGER_SYNC_CACHE: dict[
    tuple[str, str, tuple[tuple[str, int, int], ...]], dict[str, Any]
] = {}
_EVOLUTION_CANDIDATE_CACHE: dict[
    tuple[str, tuple[tuple[str, int, int], ...], str, tuple[str, ...]],
    list[dict[str, Any]],
] = {}

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
_LEGACY_AUTO_QUEUE_SIGNAL_TYPES = {
    "source_needs_deep_dive",
    "deep_dive_needs_objects",
}
EVOLUTION_LINK_EXPLANATIONS = {
    "challenges": "Newer evidence is challenging the current interpretation.",
    "replaces": "A newer interpretation appears to supersede the older one.",
    "confirms": "Independent evidence is reinforcing the current interpretation.",
    "enriches": "Newer material is adding depth without overturning the core idea.",
}
_BRIEFING_SIGNAL_PRIORITY = {
    "contradiction_open": 100,
    "stale_summary": 90,
    "production_gap": 80,
    "source_needs_deep_dive": 70,
    "deep_dive_needs_objects": 60,
    "contradiction_reviewed": 40,
    "summary_rebuilt": 30,
}
_BRIEFING_EVOLUTION_PRIORITY = {
    "challenges": 100,
    "replaces": 90,
    "confirms": 70,
    "enriches": 60,
}
_CANDIDATE_STRONG_EVIDENCE_COUNT = 3
_CANDIDATE_STRONG_SOURCE_COUNT = 2
_CANDIDATE_SENSITIVE_TERMS = (
    "credential",
    "medical",
    "health",
    "legal",
    "finance",
    "permission",
    "personal",
    "private",
    "user profile",
)
_NOTE_CAPTURE_EVENT_TYPES = frozenset(
    {
        "source_staged_for_processing",
        "source_archived_to_processed",
        "source_restored_to_raw",
        "article_processed",
        "article_abstained",
        "article_error",
        "candidate_upsert_error",
        "evergreen_error",
        "candidates_upserted",
        "evergreen_auto_promoted",
        "evergreen_created",
        "refine_mutation_applied",
    }
)


# ---------------------------------------------------------------------------
# Pure utility functions
# ---------------------------------------------------------------------------

def _utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coerce_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, remaining_seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m" if remaining_seconds == 0 else f"{minutes}m {remaining_seconds}s"
    hours, remaining_minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h" if remaining_minutes == 0 else f"{hours}h {remaining_minutes}m"
    days, remaining_hours = divmod(hours, 24)
    return f"{days}d" if remaining_hours == 0 else f"{days}d {remaining_hours}h"


def _parse_iso_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _action_queue_path(vault_dir: Path | str) -> Path:
    resolved = resolve_vault_dir(vault_dir)
    return VaultLayout.from_vault(resolved).actions_log


def _signal_ledger_path(vault_dir: Path | str, *, pack_name: str | None = None) -> Path:
    resolved = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved)
    normalized_pack = str(pack_name or DEFAULT_WORKFLOW_PACK_NAME)
    if normalized_pack == DEFAULT_WORKFLOW_PACK_NAME:
        return layout.signals_log
    safe_pack = re.sub(r"[^a-z0-9._-]+", "-", normalized_pack.lower()).strip("-") or "pack"
    return layout.logs_dir / f"signals.{safe_pack}.jsonl"


def _read_jsonl_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return list(iter_jsonl(path))


def _validate_page_args(*, limit: int, offset: int = 0) -> tuple[int, int]:
    if limit < 0 or offset < 0:
        raise ValueError("limit and offset must be >= 0")
    if limit > MAX_PAGE_SIZE:
        raise ValueError(f"limit must be <= {MAX_PAGE_SIZE}")
    return limit, offset


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _tokenize_for_search(query: str) -> list[str]:
    """Split a free-form query into searchable tokens.

    **English / ASCII tokens**: extracted via ``_ASCII_TOKEN_RE``
    (``[A-Za-z0-9]+``).  Single-char ASCII fragments are dropped because
    they cause FTS5 noise, but CJK single chars are kept.

    **CJK tokens**: run through ``jieba.cut()`` when the library is
    available.  This splits strings like ``"智能体记忆"`` into
    ``["智能体", "记忆"]`` instead of treating the whole run as one
    opaque blob — which is what the FTS5 ``unicode61`` tokenizer would
    otherwise see.  When jieba is not installed we fall back to
    per-character tokenisation (still useful for trigram-style matching).
    """
    if not query or not query.strip():
        return []
    lowered = query.lower()
    tokens: list[str] = []
    seen: set[str] = set()

    def _add(tok: str) -> None:
        tok = tok.strip()
        if not tok or tok in seen:
            return
        if len(tok) == 1 and not _CJK_RE.match(tok):
            return
        seen.add(tok)
        tokens.append(tok)

    for match in _ASCII_TOKEN_RE.findall(lowered):
        _add(match)

    if _CJK_RE.search(lowered):
        try:
            import jieba  # optional: gives much better CJK segmentation

            for word in jieba.cut(lowered):
                if _CJK_RE.search(word):
                    _add(word)
        except ImportError:
            # Fallback: per-char tokenisation — still useful for trigram FTS5 matching
            for ch in lowered:
                if _CJK_RE.match(ch):
                    _add(ch)
    return tokens


def _build_fts_match(tokens: list[str]) -> str:
    """Quote each token and AND them together into an FTS5 MATCH expression.

    FTS5 trigram tokenizer physically cannot match tokens shorter than 3
    characters — regardless of script.  Short CJK tokens (1-2 chars like
    "投资", "AI") are handled by the LIKE fallback in the caller instead.
    """
    long_enough = [tok for tok in tokens if len(tok) >= 3]
    if not long_enough:
        return ""
    quoted = [f'"{tok.replace(chr(34), chr(34) * 2)}"' for tok in long_enough]
    return " AND ".join(quoted)


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


def _resolve_note_path(vault_dir: Path | str, relative_path: str) -> Path | None:
    """Resolve a vault-relative path, returning None if it escapes the vault or doesn't exist."""
    resolved = resolve_vault_dir(vault_dir)
    note_path = (resolved / relative_path).resolve()
    try:
        note_path.relative_to(resolved.resolve())
    except ValueError:
        return None
    if not note_path.is_file():
        return None
    return note_path


def _read_note_frontmatter(vault_dir: Path | str, relative_path: str) -> dict[str, Any]:
    note_path = _resolve_note_path(vault_dir, relative_path)
    if note_path is None:
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


@functools.lru_cache(maxsize=64)
def _resolved_vault_dir_cached(vault_dir_str: str) -> Path:
    """Memoise ``resolve_vault_dir(...).resolve()`` per vault dir.

    Pre-fix every ``_vault_relative_path`` call (~14 K per
    ``/ops/signals`` request) re-ran ``resolve()`` on the same
    vault root, hitting ``realpath`` syscalls on every iteration.
    Caching by the ``str`` form of the vault path is enough — any
    given UI request runs against a single vault.
    """
    return resolve_vault_dir(vault_dir_str).resolve()


@functools.lru_cache(maxsize=8192)
def _vault_relative_path_cached(vault_dir_str: str, path: str) -> str:
    """Memoised core: ``_vault_relative_path`` defers here once
    inputs have been coerced to ``str``.  Call frequency on the
    slow Ops pages is dominated by a small set of repeated paths
    (the same evergreen referenced by N events), so a 8 K-entry
    cache covers a full request without filling RAM.
    """
    resolved = _resolved_vault_dir_cached(vault_dir_str)
    candidate = Path(path)
    if not candidate.is_absolute():
        return path
    try:
        return str(candidate.resolve().relative_to(resolved))
    except ValueError:
        return path


def _vault_relative_path(vault_dir: Path | str, path: str) -> str:
    return _vault_relative_path_cached(str(vault_dir), str(path))


def _read_note_text(vault_dir: Path | str, relative_path: str) -> str:
    note_path = _resolve_note_path(vault_dir, relative_path)
    if note_path is None:
        return ""
    return note_path.read_text(encoding="utf-8")


def _note_date_text(vault_dir: Path | str, note_path: str) -> str:
    frontmatter = _read_note_frontmatter(vault_dir, note_path)
    date_value = frontmatter.get("date")
    return str(date_value).strip() if date_value is not None else ""


def _note_date_sort_key(date_text: str) -> tuple[int, float, str]:
    parsed = _parse_iso_datetime(date_text)
    if parsed is None:
        return (0, 0.0, date_text)
    return (1, parsed.timestamp(), date_text)


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


def _briefing_priority_score(item: dict[str, Any]) -> tuple[int, int, int]:
    signal_type = str(item.get("signal_type") or item.get("kind") or "")
    recommended_action = item.get("recommended_action")
    executable = 0
    if isinstance(recommended_action, dict) and recommended_action.get("executable"):
        executable = 1
    object_count = len([value for value in item.get("object_ids", []) if value])
    return (_BRIEFING_SIGNAL_PRIORITY.get(signal_type, 0), executable, object_count)


def _briefing_evolution_score(item: dict[str, Any]) -> tuple[int, int]:
    return (
        _BRIEFING_EVOLUTION_PRIORITY.get(str(item.get("link_type") or ""), 0),
        len([value for value in item.get("object_ids", []) if value]),
    )


def _db_path(vault_dir: Path | str) -> Path:
    return ensure_knowledge_db_current(vault_dir)


def _truth_pack_name(pack_name: str | None = None) -> str:
    return str(pack_name or DEFAULT_WORKFLOW_PACK_NAME)


def _truth_pack_candidates(pack_name: str | None = None) -> list[str]:
    return [pack.name for pack in iter_compatible_packs(pack_name or DEFAULT_WORKFLOW_PACK_NAME)]


_ALLOWED_TRUTH_TABLES: frozenset[str] = frozenset({
    "objects",
    "object_relations",
    "truth_projections",
    "semantic_relations",
    "evidence_notes",
    "graph_clusters",
    "graph_edges",
    "contradictions",
})


def _materialized_truth_packs(
    vault_dir: Path | str,
    *,
    pack_name: str | None,
    table_name: str,
) -> list[str]:
    if table_name not in _ALLOWED_TRUTH_TABLES:
        raise ValueError(f"table_name must be one of {sorted(_ALLOWED_TRUTH_TABLES)}")
    candidates = _truth_pack_candidates(pack_name)
    requested_pack = candidates[0]
    db = _db_path(vault_dir)
    row = None
    try:
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT 1 FROM truth_projections WHERE pack = ? LIMIT 1",
                (requested_pack,),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    f"SELECT 1 FROM {table_name} WHERE pack = ? LIMIT 1",
                    (requested_pack,),
                ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        with sqlite3.connect(db) as conn2:
            row = conn2.execute(
                f"SELECT 1 FROM {table_name} WHERE pack = ? LIMIT 1",
                (requested_pack,),
            ).fetchone()
    if row is not None:
        return [requested_pack]
    return candidates
