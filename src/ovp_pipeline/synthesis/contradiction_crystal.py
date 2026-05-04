"""Contradiction Crystal MVP (BL-043, M13).

For every open row in the ``contradictions`` table, ask the LLM to
synthesize an "open question" crystal that explicitly lays out the
positive vs negative positions on the same subject.  Output lands
at ``40-Resources/Crystals/contradiction-<sha>.md`` and lineage is
persisted append-only in ``contradiction_crystals``.

Distinct from BL-042 community crystals along two axes:

* **Source structure**:  community crystals consume a Louvain
  community's member object_ids; contradiction crystals consume
  a paired (positive_claim_ids, negative_claim_ids) tuple keyed
  on a normalized ``subject_key``.

* **Synthesis intent**:  community crystals try to identify shared
  themes and converge them; contradiction crystals deliberately
  preserve the tension — the LLM is told NOT to resolve, only to
  lay out the open question.

Shared with BL-042: frontmatter rendering with ``projection_*``
fields, evergreen body loader, object-subset DB lookup,
filesystem write defense-in-depth.  Imported from
``community_crystal`` rather than re-implemented.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from ..projection_labels import frontmatter_projection_fields
from ._versioning import ARCHIVE_DIR_REL, supersede_and_archive_previous
from .community_crystal import (
    CRYSTAL_DIR_REL,
    _load_evergreen_bodies,
    _load_objects_subset,
)

logger = logging.getLogger(__name__)


# Bump when the system or user prompt changes materially; persisted
# on every row so future analysis can reason about regime changes.
CONTRADICTION_PROMPT_VERSION: str = "v1"

# Default token budget — open-question crystals are typically
# slightly shorter than community crystals because the source
# material is two specific positions, not a whole community.
DEFAULT_MAX_TOKENS: int = 1800


@dataclass(frozen=True, slots=True)
class ContradictionCrystal:
    """One synthesized open-question crystal — mirror of a
    ``contradiction_crystals`` row."""

    pack: str
    contradiction_id: str
    subject_key: str
    body_md: str
    positive_claim_ids: tuple[str, ...]
    negative_claim_ids: tuple[str, ...]
    source_object_ids: tuple[str, ...]
    synthesized_at: str
    llm_model: str
    prompt_version: str

    def as_db_row(self) -> tuple[str, str, str, str, str, str, str, str, str, str]:
        return (
            self.pack,
            self.contradiction_id,
            self.subject_key,
            self.body_md,
            json.dumps(list(self.positive_claim_ids), ensure_ascii=False),
            json.dumps(list(self.negative_claim_ids), ensure_ascii=False),
            json.dumps(list(self.source_object_ids), ensure_ascii=False),
            self.synthesized_at,
            self.llm_model,
            self.prompt_version,
        )


class _LLMClient(Protocol):
    def call(self, system_prompt: str, user_prompt: str,
             *, max_tokens: int = ...) -> str: ...


# ----- Prompt construction --------------------------------------------


_SYSTEM_PROMPT = """\
你是知识库的合成助手。给定同一个主题(subject)上互相对立的两组 claim—— "支持/可以/是" 的正面 claim 和 "不支持/不可以/不是" 的负面 claim —— 你的任务是产出一篇 markdown crystal 正文,把这场张力**明确摆出来**,作为一个 open question crystal 保留下来。

要求:
- 用中文输出。简洁但有信息密度,800–1500 字。
- 用 markdown 标题分节(## 争议核心 / ## 正方立场 / ## 反方立场 / ## 待解决的问题)。
- 不要写 frontmatter——它由调用方添加。
- 不要试图给出"标准答案"或调和——这是一个 open question crystal,意在保留张力。
- 不要在开头添加 "好的" / "以下是" 这类客套话。
- 不要用 ``` 代码块包裹整个输出。
- 引用源笔记时使用 ``[[note_slug]]`` 形式。
"""


# One side as a list of (object_id, title, claim_texts, body) — the
# claim_texts are grouped so a single evergreen with multiple claims
# on the same contradiction emits its body ONCE (not per claim).
_PromptSide = list[tuple[str, str, list[str], str]]


def _build_user_prompt(
    subject_key: str,
    positives: _PromptSide,
    negatives: _PromptSide,
) -> str:
    parts: list[str] = [
        f"# Subject: {subject_key}",
        "",
        "以下是同一个主题上互相冲突的两组立场。请综合它们,"
        "输出一篇明确点出张力的 open question crystal 正文。",
        "",
        "## 正面立场 (positives)",
        "",
    ]
    if not positives:
        parts.append("(无)")
    parts.extend(_render_side(positives))
    parts.extend(["", "## 反面立场 (negatives)", ""])
    if not negatives:
        parts.append("(无)")
    parts.extend(_render_side(negatives))
    return "\n".join(parts)


def _render_side(side: _PromptSide) -> list[str]:
    out: list[str] = []
    for object_id, title, claim_texts, body in side:
        out.append(f"### [[{object_id}]] — {title}")
        if len(claim_texts) == 1:
            out.append(f"**Claim:** {claim_texts[0]}")
        else:
            # Multiple claims on the same evergreen — list them
            # under a single body to avoid repeating the body text.
            out.append("**Claims:**")
            for ct in claim_texts:
                out.append(f"- {ct}")
        out.append("")
        if body:
            out.append(body.strip())
        out.append("")
    return out


# ----- DB helpers -----------------------------------------------------


_CONTRADICTION_FILTER_CHUNK = 500


def _load_open_contradictions(
    conn: sqlite3.Connection,
    pack: str,
    *,
    only_contradiction_ids: set[str] | None,
    limit: int | None,
) -> list[tuple[str, str, str, str]]:
    """Return ``[(contradiction_id, subject_key, positives_json,
    negatives_json), ...]`` for ``status='open'`` rows in ``pack``.

    Resolved contradictions don't get crystals — once an operator
    has annotated the resolution, re-synthesizing an "open question"
    crystal would muddy the audit trail.

    The ``IN`` clause is chunked at ``_CONTRADICTION_FILTER_CHUNK``
    to stay under SQLite's 999-parameter cap when an operator
    scripts many ``--contradiction-id`` flags.
    """
    base_sql = (
        "SELECT contradiction_id, subject_key,"
        " positive_claim_ids_json, negative_claim_ids_json"
        " FROM contradictions"
        " WHERE pack = ? AND status = 'open'"
    )
    rows: list[tuple[str, str, str, str]] = []
    if only_contradiction_ids:
        ids = sorted(only_contradiction_ids)
        for start in range(0, len(ids), _CONTRADICTION_FILTER_CHUNK):
            chunk = ids[start:start + _CONTRADICTION_FILTER_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            sql = f"{base_sql} AND contradiction_id IN ({placeholders})"
            cur = conn.execute(sql, (pack, *chunk))
            rows.extend((r[0], r[1], r[2], r[3]) for r in cur)
        # Stable order across chunks; LIMIT applied in Python because
        # it has to span the union, not a single chunk.
        rows.sort(key=lambda r: r[0])
        if limit is not None:
            rows = rows[:int(limit)]
        return rows
    sql = f"{base_sql} ORDER BY contradiction_id"
    params: list[object] = [pack]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    cur = conn.execute(sql, tuple(params))
    return [(r[0], r[1], r[2], r[3]) for r in cur]


def _load_claims_subset(
    conn: sqlite3.Connection,
    pack: str,
    claim_ids: set[str],
) -> dict[str, str]:
    """Targeted lookup — claim_id → claim_text for the IDs we need.

    Mirrors ``community_crystal._load_objects_subset`` chunking so we
    stay below SQLite's 999-parameter cap on heavy contradictions.
    """
    if not claim_ids:
        return {}
    out: dict[str, str] = {}
    ids = sorted(claim_ids)
    chunk_size = 500
    for start in range(0, len(ids), chunk_size):
        chunk = ids[start:start + chunk_size]
        placeholders = ",".join("?" * len(chunk))
        cur = conn.execute(
            f"SELECT claim_id, claim_text FROM claims "
            f"WHERE pack = ? AND claim_id IN ({placeholders})",
            (pack, *chunk),
        )
        for cid, text in cur:
            out[cid] = text
    return out


def _persist_crystal(
    conn: sqlite3.Connection, crystal: ContradictionCrystal,
) -> None:
    conn.execute(
        """
        INSERT INTO contradiction_crystals
            (pack, contradiction_id, subject_key, body_md,
             positive_claim_ids_json, negative_claim_ids_json,
             source_object_ids_json, synthesized_at, llm_model,
             prompt_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        crystal.as_db_row(),
    )


# ----- Object IDs from claim IDs --------------------------------------


def _claim_id_to_object_id(claim_id: str) -> str:
    """``claim_id`` format is ``{object_id}::{digest}``.

    Mirrors the split used by ``packs/research_tech/truth_projection``
    when seeding contradiction edges; both sides must agree on the
    decoding rule or claim → object provenance breaks.
    """
    return claim_id.split("::", 1)[0]


# ----- Markdown rendering ---------------------------------------------


def _frontmatter(crystal: ContradictionCrystal) -> str:
    lines: list[str] = [
        "---",
        "type: contradiction_crystal",
        f"contradiction_id: {crystal.contradiction_id}",
        f"subject_key: {json.dumps(crystal.subject_key, ensure_ascii=False)}",
        f"synthesized_at: {crystal.synthesized_at}",
        f"llm_model: {crystal.llm_model}",
        f"prompt_version: {crystal.prompt_version}",
        "positive_claim_ids:",
    ]
    for cid in crystal.positive_claim_ids:
        lines.append(f"  - {cid}")
    lines.append("negative_claim_ids:")
    for cid in crystal.negative_claim_ids:
        lines.append(f"  - {cid}")
    lines.append("source_object_ids:")
    for oid in crystal.source_object_ids:
        lines.append(f"  - {oid}")
    lines.append("tags: [crystal, contradiction, open_question]")
    lines.extend(frontmatter_projection_fields(
        surface="contradiction_crystal",
        projection_kind="compiled_wiki_projection",
        owner_pack=crystal.pack,
        generated_by="synthesize_contradiction_crystals",
        derived_from=(
            "knowledge.db.contradictions",
            "knowledge.db.claims",
            "knowledge.db.contradiction_crystals",
        ),
        rebuild_policy="on_demand_or_refresh",
    ))
    lines.extend(["---", ""])
    return "\n".join(lines)


def _safe_id(contradiction_id: str) -> str:
    """Strips the unportable ``::`` prefix and prefixes
    ``contradiction-`` so the directory listing makes the kind
    obvious at a glance (community crystals at ``<sha>.md`` vs
    contradiction crystals at ``contradiction-<sha>.md``).

    Used both for the live filename (``<safe-id>.md``) and the
    archive subdirectory (``70-Archive/Crystals/<safe-id>/...``).
    """
    if contradiction_id.startswith("contradiction::"):
        return f"contradiction-{contradiction_id[len('contradiction::'):]}"
    return f"contradiction-{contradiction_id}"


def _crystal_filename(contradiction_id: str) -> str:
    return _safe_id(contradiction_id) + ".md"


def render_crystal_markdown(crystal: ContradictionCrystal) -> str:
    return _frontmatter(crystal) + crystal.body_md.rstrip() + "\n"


# ----- Main entry point -----------------------------------------------


def synthesize_contradiction_crystals(
    vault_dir: Path,
    *,
    llm_client: _LLMClient,
    db_path: Path,
    pack_name: str = "research-tech",
    only_contradiction_ids: set[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    llm_model_label: str = "anthropic/MiniMax-M2.7-highspeed",
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[ContradictionCrystal]:
    """Synthesize one open-question crystal per open contradiction in
    ``pack_name``.

    Single connection across the function — fetches the filtered
    contradiction list, the targeted claims subset, and the
    targeted objects subset, then reuses the same connection for
    per-row INSERTs (committed individually for incremental
    durability on long batches).
    """
    crystal_dir = (vault_dir / CRYSTAL_DIR_REL).resolve()
    if not dry_run:
        crystal_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        contradictions = _load_open_contradictions(
            conn, pack_name,
            only_contradiction_ids=only_contradiction_ids,
            limit=limit,
        )

        # Decode every contradiction's claim IDs up front so we can
        # batch the claims + objects lookups.  Skipping malformed
        # JSON keeps a single bad row from sinking the batch.
        decoded: list[tuple[str, str, list[str], list[str], set[str]]] = []
        all_claim_ids: set[str] = set()
        all_object_ids: set[str] = set()
        for cid, subject, pos_json, neg_json in contradictions:
            try:
                positives = list(json.loads(pos_json))
                negatives = list(json.loads(neg_json))
            except (TypeError, json.JSONDecodeError):
                logger.warning(
                    "malformed claim_ids_json for contradiction %s; skipping",
                    cid,
                )
                continue
            obj_ids = {_claim_id_to_object_id(c) for c in positives + negatives}
            decoded.append((cid, subject, positives, negatives, obj_ids))
            all_claim_ids.update(positives)
            all_claim_ids.update(negatives)
            all_object_ids.update(obj_ids)

        claims_by_id = _load_claims_subset(conn, pack_name, all_claim_ids)
        objects_by_id = _load_objects_subset(conn, pack_name, all_object_ids)

        # Read every needed evergreen body ONCE up front.  Two
        # contradictions sharing a source object (or two sides of
        # the same contradiction sharing one) used to read its
        # markdown twice; now we load each file at most once and
        # the per-side helper just looks up from the dict.
        loaded = _load_evergreen_bodies(
            vault_dir,
            member_object_ids=sorted(all_object_ids),
            objects_by_id=objects_by_id,
        )
        title_by_object: dict[str, str] = {oid: title for oid, title, _b in loaded}
        body_by_object: dict[str, str] = {oid: body for oid, _t, body in loaded}

        out: list[ContradictionCrystal] = []
        for contradiction_id, subject, positives, negatives, obj_ids in decoded:
            pos_evergreens = _build_side(
                positives, claims_by_id, title_by_object, body_by_object,
            )
            neg_evergreens = _build_side(
                negatives, claims_by_id, title_by_object, body_by_object,
            )
            if not pos_evergreens and not neg_evergreens:
                logger.warning(
                    "no readable claims/evergreens for contradiction %s; skipping",
                    contradiction_id,
                )
                continue
            user_prompt = _build_user_prompt(
                subject, pos_evergreens, neg_evergreens,
            )
            try:
                body_md = llm_client.call(
                    _SYSTEM_PROMPT, user_prompt, max_tokens=max_tokens,
                )
            except Exception as exc:
                logger.warning(
                    "LLM call failed for contradiction %s: %s — skipping",
                    contradiction_id, exc,
                )
                continue
            body_md = body_md.strip()
            if not body_md:
                logger.warning(
                    "LLM returned empty body for contradiction %s; skipping",
                    contradiction_id,
                )
                continue

            crystal = ContradictionCrystal(
                pack=pack_name,
                contradiction_id=contradiction_id,
                subject_key=subject,
                body_md=body_md,
                positive_claim_ids=tuple(positives),
                negative_claim_ids=tuple(negatives),
                source_object_ids=tuple(sorted(obj_ids)),
                synthesized_at=datetime.now(timezone.utc).isoformat(
                    timespec="seconds",
                ),
                llm_model=llm_model_label,
                prompt_version=CONTRADICTION_PROMPT_VERSION,
            )
            out.append(crystal)

            if dry_run:
                continue

            target = crystal_dir / _crystal_filename(contradiction_id)
            try:
                target.resolve().relative_to(crystal_dir)
            except ValueError:
                logger.warning(
                    "refusing to write crystal outside %s: contradiction=%r",
                    crystal_dir, contradiction_id,
                )
                continue

            # BL-044: archive prior version + flip its supersede
            # pointer before overwriting the live markdown.
            archive_subdir = (
                vault_dir / ARCHIVE_DIR_REL / _safe_id(contradiction_id)
            )
            supersede_and_archive_previous(
                conn,
                table="contradiction_crystals",
                key_column="contradiction_id",
                pack=pack_name,
                key_value=contradiction_id,
                new_synthesized_at=crystal.synthesized_at,
                live_path=target,
                archive_subdir=archive_subdir,
            )
            target.write_text(
                render_crystal_markdown(crystal), encoding="utf-8",
            )
            _persist_crystal(conn, crystal)
            conn.commit()
        return out
    finally:
        conn.close()


def _build_side(
    claim_ids: list[str],
    claims_by_id: dict[str, str],
    title_by_object: dict[str, str],
    body_by_object: dict[str, str],
) -> _PromptSide:
    """Compose ``(object_id, title, [claim_texts], body_md)`` for one
    side of the contradiction.

    Pure function — all I/O happens in the caller.  Two design
    properties matter:

    * **Group by object_id**: when one evergreen carries multiple
      claims on the same contradiction, its body appears in the
      prompt only once with the claims listed underneath.  Pre-fix
      the body was repeated per claim, wasting prompt tokens.

    * **Drop unresolved refs**: claims whose source object is
      missing from ``title_by_object`` are skipped — the LLM gets
      the side as it actually exists today, not as it was when
      the contradiction row was first seeded.
    """
    by_object: dict[str, list[str]] = {}
    object_order: list[str] = []
    for cid in claim_ids:
        oid = _claim_id_to_object_id(cid)
        claim_text = claims_by_id.get(cid)
        if claim_text is None:
            continue
        if oid not in title_by_object:
            continue
        if oid not in by_object:
            by_object[oid] = []
            object_order.append(oid)
        by_object[oid].append(claim_text)
    return [
        (oid, title_by_object[oid], by_object[oid], body_by_object.get(oid, ""))
        for oid in object_order
    ]
