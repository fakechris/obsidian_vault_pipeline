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
    crystal_safe_id,
    load_evergreen_bodies,
    load_objects_subset,
    related_notes_section,
    strip_frontmatter,
)
from ._versioning import ARCHIVE_DIR_REL, commit_crystal_version

logger = logging.getLogger(__name__)


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
    # BL-114: ``concept_id`` is the stable cross-rebuild identity.
    # At seed time and pre-BL-115 it equals ``cluster_id``; BL-115's
    # Jaccard matcher is what makes the two diverge.
    concept_id: str = ""

    def as_db_row(self) -> tuple[str, str, str, str, str, str, str, str, str]:
        # BL-114: ``concept_id`` defaults to ``cluster_id`` so callers
        # that haven't been updated yet still produce a valid row.
        # ``supersede_reason`` always inserts as '' — BL-116 writes
        # this column via the orphan-supersede UPDATE, never INSERT.
        concept_id = self.concept_id or self.cluster_id
        return (
            self.pack,
            self.cluster_id,
            self.body_md,
            json.dumps(list(self.source_evergreen_slugs), ensure_ascii=False),
            self.synthesized_at,
            self.llm_model,
            self.prompt_version,
            concept_id,
            "",
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
    "  synthesized_at, llm_model, prompt_version,"
    "  concept_id, supersede_reason)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _upsert_concept_ledger(
    conn,
    *,
    pack: str,
    concept_id: str,
    current_cluster_id: str,
    synthesized_at: str,
) -> None:
    """BL-114: keep ``concept_identity_ledger`` in sync with every
    crystal write.  At seed time + pre-BL-115 the matcher hasn't
    landed yet, so every new synthesis is its own concept (concept_id
    == cluster_id) and the ledger row is a straight upsert with
    ``lineage_json='[]'``.  BL-115 will replace this call with the
    real matcher.  ``INSERT OR IGNORE`` keeps the seed row from being
    overwritten with an empty lineage if the migration already
    populated it; the follow-up UPDATE refreshes ``last_matched_at``
    + ``current_cluster_id`` so reads tracking the current cluster
    stay correct.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO concept_identity_ledger
            (pack, concept_id, current_cluster_id,
             last_matched_at, created_at, lineage_json)
        VALUES (?, ?, ?, ?, ?, '[]')
        """,
        (pack, concept_id, current_cluster_id,
         synthesized_at, synthesized_at),
    )
    conn.execute(
        """
        UPDATE concept_identity_ledger
           SET current_cluster_id = ?,
               last_matched_at    = ?
         WHERE pack = ? AND concept_id = ?
        """,
        (current_cluster_id, synthesized_at, pack, concept_id),
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
    """Thin shim over :func:`crystal_safe_id` for community crystals.

    Kept as a private helper so existing call sites stay readable —
    the equivalent of ``crystal_safe_id("community", cluster_id)``
    but with the kind already pinned.
    """
    return crystal_safe_id("community", cluster_id)


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


# ``_related_notes_section`` lived here as a duplicate of
# ``contradiction_crystal._related_notes_section``.  The shared
# implementation lives in ``_shared.related_notes_section`` now;
# call sites import that directly.


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
    parts.append(related_notes_section(crystal.source_evergreen_slugs))
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
            # BL-114: skip clusters whose CURRENT cluster_id already
            # maps to an active concept via the ledger.  Pre-fix this
            # filtered by ``community_crystals.cluster_id`` directly,
            # which post-BL-115 would miss inherited concepts (their
            # current_cluster_id differs from the historical cluster_id
            # stored on the crystal row).  Skip-existing should align
            # with "is there a current concept for this cluster?" not
            # "has any crystal ever been written for this cluster_id?".
            existing = {
                row[0] for row in conn.execute(
                    "SELECT DISTINCT cil.current_cluster_id "
                    "  FROM concept_identity_ledger cil "
                    "  JOIN community_crystals cc "
                    "    ON cc.pack = cil.pack AND cc.concept_id = cil.concept_id "
                    "   AND cc.superseded_by_synthesized_at = '' "
                    " WHERE cil.pack = ?",
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

        objects_by_id = load_objects_subset(
            conn, pack_name, needed_object_ids,
        )

        out: list[CommunityCrystal] = []
        for cluster_id, label, picked, community_total in decoded:
            evergreens = load_evergreen_bodies(
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
            # M20 / BL-075: prepend user identity + autonomous-action
            # rules so the synthesizer adopts the user's voice and
            # respects the contract.  Graceful degradation when the
            # vault has neither file (legacy vaults see no change).
            from ..context_loader import inject_llm_context
            system_prompt = inject_llm_context(vault_dir, _SYSTEM_PROMPT)
            try:
                body_md = llm_client.call(
                    system_prompt, user_prompt, max_tokens=max_tokens,
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

            # BL-114: pre-BL-115 the matcher hasn't landed yet, so a
            # fresh synthesis is always its own concept (concept_id
            # == cluster_id).  BL-115 replaces this seed with the
            # Jaccard-matcher result when an existing concept's
            # current_cluster_id has shifted.
            concept_id = cluster_id
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
                concept_id=concept_id,
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
            #
            # BL-114: supersede now keys on ``concept_id`` (stable
            # identity), not ``cluster_id`` (synthesis-time snapshot).
            # At seed time the two are equal so behaviour is identical;
            # post-BL-115 keying on concept_id is what lets a re-clustered
            # community supersede its prior crystal even when the new
            # cluster_id differs.
            archive_subdir = (
                vault_dir / ARCHIVE_DIR_REL / _safe_id(cluster_id)
            )
            _upsert_concept_ledger(
                conn,
                pack=pack_name,
                concept_id=concept_id,
                current_cluster_id=cluster_id,
                synthesized_at=crystal.synthesized_at,
            )
            commit_crystal_version(
                conn,
                table="community_crystals",
                key_column="concept_id",
                pack=pack_name,
                key_value=concept_id,
                new_synthesized_at=crystal.synthesized_at,
                insert_sql=_INSERT_SQL,
                insert_params=crystal.as_db_row(),
                new_markdown=render_crystal_markdown(
                    crystal, label=label, community_total=community_total,
                ),
                live_path=target,
                archive_subdir=archive_subdir,
                # BL-056: stage emit.  The provenance row carries
                # the LLM model + prompt version + sample size for
                # this synthesis run so audit can answer "which
                # MiniMax model produced this crystal".
                provenance_stage="synthesize_community_crystal",
                provenance_metadata={
                    "llm_model": crystal.llm_model,
                    "prompt_version": crystal.prompt_version,
                    "sample_size": len(crystal.source_evergreen_slugs),
                    "community_total": community_total,
                },
            )

            # M24.2 gap fixed (M25.6 dogfood): the producer-audit
            # contract declared ``community_crystal_synthesized``
            # for this producer and the M24.1 kernel reads it to
            # classify a cluster as Synthesized — but the emit was
            # never wired here, so synthesis wrote crystals to the
            # DB while the lifecycle "Synthesized" bucket stayed at
            # 0 on every vault.  Emit it now, carrying ``cluster_id``
            # at the payload top level so ``ops_lifecycle``'s audit
            # index picks it up.
            try:
                from ..event_emitter import emit as _emit_audit

                _emit_audit(
                    vault_dir,
                    "pipeline.jsonl",
                    "community_crystal_synthesized",
                    {
                        "cluster_id": cluster_id,
                        "synthesized_at": crystal.synthesized_at,
                        "llm_model": crystal.llm_model,
                        "prompt_version": crystal.prompt_version,
                        "sample_size": len(crystal.source_evergreen_slugs),
                    },
                    pack=pack_name,
                )
            except Exception:  # noqa: BLE001
                # Audit emit is best-effort: a logging failure must
                # not sink a synthesis batch that already committed
                # the crystal + provenance row.
                logger.warning(
                    "community_crystal_synthesized emit failed for "
                    "cluster=%r (crystal already committed)",
                    cluster_id,
                )
        return out
    finally:
        conn.close()
