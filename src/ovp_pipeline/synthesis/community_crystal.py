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
        out.append((object_id, title, body))
    return out


# ----- DB helpers -----------------------------------------------------


def _load_clusters_for_pack(
    conn: sqlite3.Connection, pack: str,
) -> list[tuple[str, str, str]]:
    """Return ``[(cluster_id, label, member_object_ids_json), ...]`` for
    every Louvain community in ``pack``."""
    rows = conn.execute(
        """
        SELECT cluster_id, label, member_object_ids_json
          FROM graph_clusters
         WHERE pack = ?
           AND cluster_kind = 'louvain_community'
         ORDER BY cluster_id
        """,
        (pack,),
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def _load_objects_index(
    conn: sqlite3.Connection, pack: str,
) -> dict[str, tuple[str, str]]:
    rows = conn.execute(
        "SELECT object_id, title, canonical_path FROM objects WHERE pack = ?",
        (pack,),
    ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


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
    lines.extend([
        "tags: [crystal, community]",
        "---",
        "",
    ])
    return "\n".join(lines)


def _crystal_filename(cluster_id: str) -> str:
    """Convert ``cluster::abc123def456`` → ``abc123def456.md``.

    Strips the ``cluster::`` prefix because ``:`` is not a portable
    filename character (Windows refuses; macOS Finder rewrites it
    silently).  The remainder is a 12-char SHA1 digest, safe by
    construction.
    """
    if cluster_id.startswith("cluster::"):
        return cluster_id[len("cluster::"):] + ".md"
    return cluster_id + ".md"


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

    conn = sqlite3.connect(db_path)
    try:
        clusters = _load_clusters_for_pack(conn, pack_name)
        objects_by_id = _load_objects_index(conn, pack_name)
    finally:
        conn.close()

    if only_cluster_ids is not None:
        clusters = [c for c in clusters if c[0] in only_cluster_ids]
    if limit_communities is not None:
        clusters = clusters[:limit_communities]

    out: list[CommunityCrystal] = []
    for cluster_id, label, members_json in clusters:
        try:
            members = json.loads(members_json)
        except (TypeError, json.JSONDecodeError):
            logger.warning("malformed member_object_ids_json for %s; skipping",
                           cluster_id)
            continue
        picked = _select_top_members(members, top_k=top_k)
        evergreens = _load_evergreen_bodies(
            vault_dir, member_object_ids=picked,
            objects_by_id=objects_by_id,
        )
        if not evergreens:
            logger.warning(
                "no readable evergreens for cluster %s; skipping", cluster_id,
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
                "LLM returned empty body for cluster %s; skipping", cluster_id,
            )
            continue

        crystal = CommunityCrystal(
            pack=pack_name,
            cluster_id=cluster_id,
            body_md=body_md,
            source_evergreen_slugs=tuple(picked),
            synthesized_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            llm_model=llm_model_label,
            prompt_version=CRYSTAL_PROMPT_VERSION,
        )
        out.append(crystal)

        if dry_run:
            continue

        # Defense-in-depth: cluster_id is `cluster::<sha1>` by
        # construction so it's already safe, but if a future
        # pipeline upstream emits something funny we still refuse
        # to write outside the crystal directory.
        target = crystal_dir / _crystal_filename(cluster_id)
        try:
            target.resolve().relative_to(crystal_dir)
        except ValueError:
            logger.warning(
                "refusing to write crystal outside %s: cluster=%r",
                crystal_dir, cluster_id,
            )
            continue
        target.write_text(
            render_crystal_markdown(crystal, label=label), encoding="utf-8",
        )

        # Persist DB row in its own connection so a long batch can
        # commit incrementally — if the LLM call hangs midway, the
        # crystals already produced stay safe.
        conn_w = sqlite3.connect(db_path)
        try:
            _persist_crystal(conn_w, crystal)
            conn_w.commit()
        finally:
            conn_w.close()

    return out
