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
from ._shared import (
    CRYSTAL_DIR_REL,
    load_evergreen_bodies,
    load_objects_subset,
    strip_frontmatter,
)
from ._versioning import ARCHIVE_DIR_REL, commit_crystal_version

logger = logging.getLogger(__name__)

# Backwards-compat aliases for tests that imported the underscored
# names before the helpers moved to ``_shared.py``.  External callers
# should prefer the un-prefixed surface in ``_shared``.
_strip_frontmatter = strip_frontmatter
_load_evergreen_bodies = load_evergreen_bodies
_load_objects_subset = load_objects_subset


# ----- Constants ------------------------------------------------------

# ``CRYSTAL_DIR_REL`` lives in ``_shared`` so contradiction crystals
# can use the same output root without reaching into this module's
# namespace.  Re-exported above via the import block.

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


# ``_strip_frontmatter`` lives in ``_shared`` now — see the import
# block at the top of this module.


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


# ``_load_evergreen_bodies`` lives in ``_shared`` now — see the
# import block at the top of this module.


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


# ``_load_objects_subset`` lives in ``_shared`` now — see the
# import block at the top of this module.


# INSERT SQL passed to ``commit_crystal_version`` — the helper takes
# table-specific INSERT verbatim because the shape of the row varies
# per crystal kind.
_INSERT_SQL = (
    "INSERT INTO community_crystals"
    " (pack, cluster_id, body_md, source_evergreen_slugs_json,"
    "  synthesized_at, llm_model, prompt_version)"
    " VALUES (?, ?, ?, ?, ?, ?, ?)"
)


# ----- Markdown rendering ---------------------------------------------


def _frontmatter(
    crystal: CommunityCrystal, *,
    label: str,
    community_total: int | None = None,
) -> str:
    lines: list[str] = [
        "---",
        "type: community_crystal",
        f"cluster_id: {crystal.cluster_id}",
        f"label: {json.dumps(label, ensure_ascii=False)}",
        f"synthesized_at: {crystal.synthesized_at}",
        f"llm_model: {crystal.llm_model}",
        f"prompt_version: {crystal.prompt_version}",
        f"sample_size: {len(crystal.source_evergreen_slugs)}",
    ]
    # Surface the full community size so consumers can detect that
    # this crystal is a SAMPLED synthesis, not a full coverage.
    # ``None`` means the caller didn't have the figure handy;
    # operators who renderer in isolation may pass it later.
    if community_total is not None:
        lines.append(f"community_total: {community_total}")
    lines.append("source_evergreen_slugs:")
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


def _sampling_disclosure(*, sample_size: int, community_total: int) -> str:
    """One-line visible note that the crystal is a sampled synthesis.

    Surfaces the under-coverage to the human reader — pre-fix the
    sampling was only knowable by reading the prompt code or
    counting source_evergreen_slugs.  Skipped when the sample
    covers the whole community (no under-coverage to disclose).
    """
    return (
        f"> **采样说明**: 本 crystal 基于该社区 {community_total} 个节点中"
        f"按 object_id 排序的前 {sample_size} 个 evergreen 合成,"
        f"长尾未覆盖。"
    )


def _related_notes_section(slugs: tuple[str, ...]) -> str:
    """Machine-generated ``## 相关笔记`` section appended to every
    crystal body.  Pre-fix wikilinks to source notes were optional
    and the LLM dropped them ~30% of the time, breaking Obsidian
    backlinks.  This section makes the source-note linkage
    deterministic — the LLM can still cite naturally in prose,
    but the backlink graph is no longer prompt-dependent.
    """
    lines = ["## 相关笔记", ""]
    for slug in slugs:
        lines.append(f"- [[{slug}]]")
    return "\n".join(lines)


def render_crystal_markdown(
    crystal: CommunityCrystal, *,
    label: str,
    community_total: int | None = None,
) -> str:
    parts: list[str] = [_frontmatter(
        crystal, label=label, community_total=community_total,
    )]
    if (
        community_total is not None
        and community_total > len(crystal.source_evergreen_slugs)
    ):
        parts.append(_sampling_disclosure(
            sample_size=len(crystal.source_evergreen_slugs),
            community_total=community_total,
        ))
        parts.append("")  # blank line between disclosure and body
    parts.append(crystal.body_md.rstrip())
    parts.append("")  # blank line between body and related-notes
    parts.append(_related_notes_section(crystal.source_evergreen_slugs))
    return "\n".join(parts) + "\n"


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
    skip_existing: bool = False,
    dry_run: bool = False,
    llm_model_label: str = "anthropic/MiniMax-M2.7-highspeed",
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[CommunityCrystal]:
    """Synthesize crystals for every Louvain community in ``pack_name``.

    Returns the list of crystals produced (or *would be* produced
    in dry-run).  Failures on a single community log a warning and
    skip — they don't sink the batch, which matters when the LLM
    occasionally times out on 1 of 30 communities.

    ``skip_existing=True`` skips communities that already have at
    least one row in ``community_crystals``.  Designed for resuming
    a long batch after Ctrl-C / crash / network blip — re-running
    with the flag picks up exactly where the prior run stopped
    instead of synthesizing v2 of every already-completed crystal
    (which would waste LLM budget).
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

        if skip_existing:
            # One query for the set of cluster_ids that already have
            # at least one row in community_crystals.  Filter the
            # cluster list before any LLM cost is incurred.
            existing = {
                row[0] for row in conn.execute(
                    "SELECT DISTINCT cluster_id FROM community_crystals "
                    "WHERE pack = ?",
                    (pack_name,),
                )
            }
            cluster_rows = [
                row for row in cluster_rows if row[0] not in existing
            ]

        # Decode + cap members up front so we know the exact set of
        # object_ids the loop will consume.  That set drives the
        # targeted ``_load_objects_subset`` query below — the OVP
        # vault has ~7000 objects, only a few hundred land inside
        # the top-K member slice.
        # ``community_total`` is the FULL community size (before the
        # top_k cap) — surfaced to the renderer so the on-disk
        # crystal carries an explicit sample-size disclosure.
        decoded: list[tuple[str, str, list[str], int]] = []
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
            community_total = len(members)
            picked = _select_top_members(members, top_k=top_k)
            decoded.append((cluster_id, label, picked, community_total))
            needed_object_ids.update(picked)

        objects_by_id = _load_objects_subset(
            conn, pack_name, needed_object_ids,
        )

        out: list[CommunityCrystal] = []
        for cluster_id, label, picked, community_total in decoded:
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
                # Microsecond resolution so two synthesize calls in
                # the same second don't collide on the (pack, key,
                # synth_at) PK — pre-fix, a same-second re-synthesis
                # would archive the prior live file then fail the
                # INSERT, leaving live + DB diverged.  In production
                # each LLM call takes 5-30s so collision was already
                # very unlikely; microseconds make it impossible in
                # practice and lets unit tests run back-to-back
                # without ``time.sleep(1)`` between iterations.
                synthesized_at=datetime.now(timezone.utc).isoformat(
                    timespec="microseconds",
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

            # BL-044: ``commit_crystal_version`` orchestrates the
            # supersede UPDATE + INSERT in one DB transaction, then
            # atomic-replaces the live markdown, then archives the
            # prior content (best-effort).  See ``_versioning.py``
            # for the full failure-mode rationale.
            archive_subdir = (
                vault_dir / ARCHIVE_DIR_REL / _safe_id(cluster_id)
            )
            commit_crystal_version(
                conn,
                table="community_crystals",
                key_column="cluster_id",
                pack=pack_name,
                key_value=cluster_id,
                new_synthesized_at=crystal.synthesized_at,
                insert_sql=_INSERT_SQL,
                insert_params=crystal.as_db_row(),
                new_markdown=render_crystal_markdown(
                    crystal, label=label, community_total=community_total,
                ),
                live_path=target,
                archive_subdir=archive_subdir,
            )
        return out
    finally:
        conn.close()
