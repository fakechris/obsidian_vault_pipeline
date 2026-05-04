"""Community Crystal MVP (BL-042, M13).

For each Louvain community in ``graph_clusters``: select up to
``top_k`` member evergreens (deterministic order — authority
weighting is a v2 follow-up), ask the LLM to synthesize a single
concept markdown that captures shared themes / tensions /
takeaways, write it to ``40-Resources/Crystals/<sha>.md``, and
persist lineage in the ``community_crystals`` table.

The DB schema for ``community_crystals`` has ``synthesized_at``
in the primary key so re-running creates a NEW append-only row
rather than overwriting — that's how BL-044 (versioning) will
land later.  Reading "the current crystal" is just
``ORDER BY synthesized_at DESC LIMIT 1`` per cluster.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Protocol

from ..projection_labels import frontmatter_projection_fields
from ._versioning import ARCHIVE_DIR_REL, supersede_and_archive_previous

logger = logging.getLogger(__name__)


# ----- Constants ------------------------------------------------------

# Output directory (relative to vault root) where the crystal markdowns
# land.  Distinct from ``40-Resources/clusters/`` (existing cluster-
# detail materializer output) and ``40-Resources/Briefings/`` (briefing
# crystals) — community crystals are LLM-synthesized concept pages,
# not deterministic detail dumps.
CRYSTAL_DIR_REL: Path = Path("40-Resources") / "Crystals"

# Prompt version — bump when the system or user prompt changes
# materially.  Persisted on every row so future analysis can
# distinguish crystals synthesized under different prompt regimes.
CRYSTAL_PROMPT_VERSION: str = "v1"

# Default community-member cap.  Larger communities are truncated to
# keep the LLM context bounded; deterministic ordering by object_id
# means re-runs produce stable inputs.
DEFAULT_TOP_K_EVERGREENS: int = 8

# Default LLM token budget for the synthesis response.  Enough for
# a 800–1500 character Chinese crystal body with frontmatter
# already supplied by us.
DEFAULT_MAX_TOKENS: int = 2000


# ----- Data shapes ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommunityCrystal:
    """One synthesized crystal — mirror of a ``community_crystals`` row."""

    pack: str
    cluster_id: str
    body_md: str
    source_evergreen_slugs: tuple[str, ...]
    synthesized_at: str
    llm_model: str
    prompt_version: str

    def as_db_row(self) -> tuple[str, str, str, str, str, str, str]:
        return (
            self.pack,
            self.cluster_id,
            self.body_md,
            json.dumps(list(self.source_evergreen_slugs), ensure_ascii=False),
            self.synthesized_at,
            self.llm_model,
            self.prompt_version,
        )


class _LLMClient(Protocol):
    """Subset of the project's LLM client surface used by this module.

    Matches ``llm_client._CallableLLMClient.call`` — see
    ``src/ovp_pipeline/llm_client.py``.
    """

    def call(self, system_prompt: str, user_prompt: str,
             *, max_tokens: int = ...) -> str: ...


# ----- Prompt construction --------------------------------------------


_SYSTEM_PROMPT = """\
你是知识库的合成助手。给定一组语义相关的 atomic Evergreen 笔记,你的任务是产出一篇 markdown crystal 正文,捕捉这些笔记共同指向的核心概念、对立观点、以及可执行启发。

要求:
- 用中文输出。简洁但有信息密度,800–1500 字。
- 用 markdown 标题分节(## 概念核心 / ## 关键张力 / ## 可执行启发)。
- 不要写 frontmatter——它由调用方添加。
- 不要在开头添加 "好的" / "以下是" 这类客套话。
- 不要用 ``` 代码块包裹整个输出。
- 引用源笔记时使用 ``[[note_slug]]`` 形式。
"""


def _build_user_prompt(
    community_label: str,
    evergreens: list[tuple[str, str, str]],  # (slug, title, body_md)
) -> str:
    parts: list[str] = [
        f"# Community: {community_label}",
        "",
        f"以下是属于这个社区的 {len(evergreens)} 篇笔记。请综合它们的内容,"
        f"输出一篇 crystal 正文。",
        "",
    ]
    for slug, title, body in evergreens:
        parts.append("---")
        parts.append(f"## [[{slug}]] — {title}")
        parts.append("")
        parts.append(body.strip())
        parts.append("")
    return "\n".join(parts)


def _strip_frontmatter(text: str) -> str:
    """Remove the YAML frontmatter block from an evergreen markdown.

    Frontmatter is bounded by ``---`` on its own line at the very
    start of the file and a closing ``---`` on its own line.  When
    absent or malformed, return the text unchanged.

    Stripping saves ~10 lines × top_k notes of LLM tokens per
    crystal call — on a vault where every evergreen carries the
    standard ``note_id / title / type / date / tags / aliases / area``
    block, that's a meaningful slice of the prompt budget.
    """
    if not text.startswith("---"):
        return text
    # Find the closing fence after the opening one.  Search starts
    # at index 3 to skip past the opening "---".
    closer = text.find("\n---", 3)
    if closer == -1:
        return text
    # Skip past the closing fence and any trailing newlines.
    return text[closer + 4:].lstrip("\n")


# ----- Member selection -----------------------------------------------


def _select_top_members(
    member_object_ids: Iterable[str],
    *,
    top_k: int,
) -> list[str]:
    """Pick up to ``top_k`` members from the community.

    MVP ordering is deterministic by object_id — authority-weighted
    selection is a v2 follow-up (would read ``source_authority`` and
    join through ``objects.source_slug``).  Deterministic order means
    re-runs produce identical prompts, which is the property we need
    most right now.
    """
    if top_k <= 0:
        return []
    return sorted(member_object_ids)[:top_k]


def _load_evergreen_bodies(
    vault_dir: Path,
    *,
    member_object_ids: list[str],
    objects_by_id: dict[str, tuple[str, str]],  # object_id -> (title, canonical_path)
) -> list[tuple[str, str, str]]:
    """Read the evergreen markdown bodies for ``member_object_ids``.

    Skips missing files / read errors with a structured warning so
    a single corrupt file doesn't sink the whole batch.
    """
    out: list[tuple[str, str, str]] = []
    for object_id in member_object_ids:
        title_path = objects_by_id.get(object_id)
        if title_path is None:
            logger.warning(
                "object_id %r not found in objects table; skipping member",
                object_id,
            )
            continue
        title, canonical_path = title_path
        full_path = vault_dir / canonical_path
        try:
            body = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning(
                "failed to read evergreen %s for crystal synthesis: %s",
                full_path, exc,
            )
            continue
        out.append((object_id, title, _strip_frontmatter(body)))
    return out


# ----- DB helpers -----------------------------------------------------


def _load_filtered_clusters(
    conn: sqlite3.Connection,
    pack: str,
    *,
    only_cluster_ids: set[str] | None,
    limit_communities: int | None,
) -> list[tuple[str, str, str]]:
    """Return ``[(cluster_id, label, member_object_ids_json), ...]``
    for Louvain communities in ``pack``, with the caller's filters
    pushed into SQL so we don't materialize the whole 312-row table
    just to slice it.
    """
    sql_parts = [
        "SELECT cluster_id, label, member_object_ids_json",
        "  FROM graph_clusters",
        " WHERE pack = ?",
        "   AND cluster_kind = 'louvain_community'",
    ]
    params: list[object] = [pack]
    if only_cluster_ids:
        # Sorted ID list keeps the query plan stable and the
        # parameter order matches subsequent debug logs.
        ids = sorted(only_cluster_ids)
        placeholders = ",".join("?" * len(ids))
        sql_parts.append(f"   AND cluster_id IN ({placeholders})")
        params.extend(ids)
    sql_parts.append(" ORDER BY cluster_id")
    if limit_communities is not None:
        sql_parts.append(" LIMIT ?")
        params.append(int(limit_communities))
    cur = conn.execute("\n".join(sql_parts), tuple(params))
    return [(r[0], r[1], r[2]) for r in cur]


# SQLite caps parameterised IN clauses at ~999 items by default.  The
# subset loader chunks below this floor so a vault with hundreds of
# communities doesn't trip the limit.
_OBJECTS_LOOKUP_CHUNK = 500


def _load_objects_subset(
    conn: sqlite3.Connection,
    pack: str,
    object_ids: set[str],
) -> dict[str, tuple[str, str]]:
    """Targeted lookup — only the object_ids the synthesis loop will
    actually consume.  Avoids loading all 7000 objects into memory
    just to read the few hundred we need."""
    if not object_ids:
        return {}
    out: dict[str, tuple[str, str]] = {}
    ids_list = sorted(object_ids)
    for start in range(0, len(ids_list), _OBJECTS_LOOKUP_CHUNK):
        chunk = ids_list[start:start + _OBJECTS_LOOKUP_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        cur = conn.execute(
            f"SELECT object_id, title, canonical_path FROM objects "
            f"WHERE pack = ? AND object_id IN ({placeholders})",
            (pack, *chunk),
        )
        for object_id, title, canonical_path in cur:
            out[object_id] = (title, canonical_path)
    return out


def _persist_crystal(
    conn: sqlite3.Connection, crystal: CommunityCrystal,
) -> None:
    conn.execute(
        """
        INSERT INTO community_crystals
            (pack, cluster_id, body_md, source_evergreen_slugs_json,
             synthesized_at, llm_model, prompt_version)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        crystal.as_db_row(),
    )


# ----- Markdown rendering ---------------------------------------------


def _frontmatter(crystal: CommunityCrystal, *, label: str) -> str:
    lines: list[str] = [
        "---",
        "type: community_crystal",
        f"cluster_id: {crystal.cluster_id}",
        f"label: {json.dumps(label, ensure_ascii=False)}",
        f"synthesized_at: {crystal.synthesized_at}",
        f"llm_model: {crystal.llm_model}",
        f"prompt_version: {crystal.prompt_version}",
        "source_evergreen_slugs:",
    ]
    for slug in crystal.source_evergreen_slugs:
        lines.append(f"  - {slug}")
    lines.append("tags: [crystal, community]")
    # Standard ``projection_*`` metadata so the crystal frontmatter
    # is consistent with the other materialized projections
    # (cluster_crystal, topic_view, briefing crystal).  These keys
    # are governed by ``projection_labels.frontmatter_projection_fields``
    # — bumping the schema version there propagates automatically.
    lines.extend(frontmatter_projection_fields(
        surface="community_crystal",
        projection_kind="compiled_wiki_projection",
        owner_pack=crystal.pack,
        generated_by="synthesize_community_crystals",
        derived_from=(
            "knowledge.db.graph_clusters",
            "knowledge.db.community_crystals",
        ),
        rebuild_policy="on_demand_or_refresh",
    ))
    lines.extend(["---", ""])
    return "\n".join(lines)


def _safe_id(cluster_id: str) -> str:
    """Strip the ``cluster::`` prefix so the result is safe as a
    filename (Windows refuses ``:``; macOS Finder rewrites it
    silently).  The remainder is a 12-char SHA1 digest, safe by
    construction.

    Used both for the live filename (``<safe-id>.md``) and the
    archive subdirectory (``70-Archive/Crystals/<safe-id>/...``) —
    one source of truth for the safe form.
    """
    if cluster_id.startswith("cluster::"):
        return cluster_id[len("cluster::"):]
    return cluster_id


def _crystal_filename(cluster_id: str) -> str:
    return _safe_id(cluster_id) + ".md"


def render_crystal_markdown(
    crystal: CommunityCrystal, *, label: str,
) -> str:
    return _frontmatter(crystal, label=label) + crystal.body_md.rstrip() + "\n"


# ----- Main entry point -----------------------------------------------


def synthesize_community_crystals(
    vault_dir: Path,
    *,
    llm_client: _LLMClient,
    db_path: Path,
    pack_name: str = "research-tech",
    top_k: int = DEFAULT_TOP_K_EVERGREENS,
    limit_communities: int | None = None,
    only_cluster_ids: set[str] | None = None,
    dry_run: bool = False,
    llm_model_label: str = "anthropic/MiniMax-M2.7-highspeed",
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[CommunityCrystal]:
    """Synthesize crystals for every Louvain community in ``pack_name``.

    Returns the list of crystals produced (or *would be* produced
    in dry-run).  Failures on a single community log a warning and
    skip — they don't sink the batch, which matters when the LLM
    occasionally times out on 1 of 30 communities.
    """
    crystal_dir = (vault_dir / CRYSTAL_DIR_REL).resolve()
    if not dry_run:
        crystal_dir.mkdir(parents=True, exist_ok=True)

    # Single connection across the function — opened once for the
    # filtered cluster fetch + targeted object lookup, reused for
    # the per-row INSERT inside the synthesis loop with a commit
    # after each row (incremental durability without the per-row
    # connect+close handshake the original code paid for).
    conn = sqlite3.connect(db_path)
    try:
        cluster_rows = _load_filtered_clusters(
            conn, pack_name,
            only_cluster_ids=only_cluster_ids,
            limit_communities=limit_communities,
        )

        # Decode + cap members up front so we know the exact set of
        # object_ids the loop will consume.  That set drives the
        # targeted ``_load_objects_subset`` query below — the OVP
        # vault has ~7000 objects, only a few hundred land inside
        # the top-K member slice.
        decoded: list[tuple[str, str, list[str]]] = []  # (id, label, picked)
        needed_object_ids: set[str] = set()
        for cluster_id, label, members_json in cluster_rows:
            try:
                members = json.loads(members_json)
            except (TypeError, json.JSONDecodeError):
                logger.warning(
                    "malformed member_object_ids_json for %s; skipping",
                    cluster_id,
                )
                continue
            picked = _select_top_members(members, top_k=top_k)
            decoded.append((cluster_id, label, picked))
            needed_object_ids.update(picked)

        objects_by_id = _load_objects_subset(
            conn, pack_name, needed_object_ids,
        )

        out: list[CommunityCrystal] = []
        for cluster_id, label, picked in decoded:
            evergreens = _load_evergreen_bodies(
                vault_dir, member_object_ids=picked,
                objects_by_id=objects_by_id,
            )
            if not evergreens:
                logger.warning(
                    "no readable evergreens for cluster %s; skipping",
                    cluster_id,
                )
                continue
            user_prompt = _build_user_prompt(label, evergreens)
            try:
                body_md = llm_client.call(
                    _SYSTEM_PROMPT, user_prompt, max_tokens=max_tokens,
                )
            except Exception as exc:
                logger.warning(
                    "LLM call failed for cluster %s: %s — skipping",
                    cluster_id, exc,
                )
                continue
            body_md = body_md.strip()
            if not body_md:
                logger.warning(
                    "LLM returned empty body for cluster %s; skipping",
                    cluster_id,
                )
                continue

            crystal = CommunityCrystal(
                pack=pack_name,
                cluster_id=cluster_id,
                body_md=body_md,
                source_evergreen_slugs=tuple(picked),
                synthesized_at=datetime.now(timezone.utc).isoformat(
                    timespec="seconds",
                ),
                llm_model=llm_model_label,
                prompt_version=CRYSTAL_PROMPT_VERSION,
            )
            out.append(crystal)

            if dry_run:
                continue

            # Defense-in-depth: cluster_id is `cluster::<sha1>` by
            # construction so it's already safe, but if a future
            # pipeline upstream emits something funny we still
            # refuse to write outside the crystal directory.
            target = crystal_dir / _crystal_filename(cluster_id)
            try:
                target.resolve().relative_to(crystal_dir)
            except ValueError:
                logger.warning(
                    "refusing to write crystal outside %s: cluster=%r",
                    crystal_dir, cluster_id,
                )
                continue

            # BL-044: archive the prior current version (if any) and
            # mark its DB row as superseded BEFORE overwriting the
            # live markdown.  If supersede + INSERT happened in
            # different orders, a crash between them would leave an
            # orphaned live file or an unarchived history.
            archive_subdir = (
                vault_dir / ARCHIVE_DIR_REL / _safe_id(cluster_id)
            )
            supersede_and_archive_previous(
                conn,
                table="community_crystals",
                key_column="cluster_id",
                pack=pack_name,
                key_value=cluster_id,
                new_synthesized_at=crystal.synthesized_at,
                live_path=target,
                archive_subdir=archive_subdir,
            )
            target.write_text(
                render_crystal_markdown(crystal, label=label),
                encoding="utf-8",
            )
            _persist_crystal(conn, crystal)
            conn.commit()
        return out
    finally:
        conn.close()
