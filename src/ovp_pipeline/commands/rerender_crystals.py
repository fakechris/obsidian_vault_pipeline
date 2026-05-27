"""ovp-rerender-crystals — regenerate on-disk crystal markdowns from
the DB rows without re-calling the LLM.

Use case: the renderer changed (added ``## 相关笔记`` section, added
sampling disclosure, added new ``projection_*`` fields, etc.) and we
want existing crystal files to pick up the new format.  The DB rows
are the source of truth for the LLM-generated body; the on-disk
markdown is a derived view we can rebuild any time.

The command walks both ``community_crystals`` and
``contradiction_crystals``, and for each row:

  * Current row (un-superseded) → rewrite the live markdown at
    ``40-Resources/Crystals/<safe-id>.md``.
  * Historical row (superseded) → rewrite the archive markdown at
    ``70-Archive/Crystals/<safe-id>/<sanitized-ts>.md``.

Idempotent.  ``--dry-run`` previews what would be rewritten.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from ..runtime import VaultLayout
from ..synthesis._shared import CRYSTAL_DIR_REL
from ..synthesis._versioning import ARCHIVE_DIR_REL, _safe_archive_filename
from ..synthesis.community_crystal import (
    CommunityCrystal,
    _crystal_filename as _community_filename,
    _safe_id as _community_safe_id,
    render_crystal_markdown as _render_community,
)
from ..synthesis.contradiction_crystal import (
    ContradictionCrystal,
    _crystal_filename as _contradiction_filename,
    _safe_id as _contradiction_safe_id,
    render_crystal_markdown as _render_contradiction,
)


def _resolve_target_path(
    vault_dir: Path,
    *,
    live_filename: str,
    archive_subdir: Path,
    synthesized_at: str,
    is_current: bool,
) -> Path:
    crystal_dir = (vault_dir / CRYSTAL_DIR_REL).resolve()
    if is_current:
        return crystal_dir / live_filename
    return archive_subdir / _safe_archive_filename(synthesized_at)


def _rerender_communities(
    conn: sqlite3.Connection,
    vault_dir: Path,
    *,
    pack: str,
    dry_run: bool,
) -> tuple[int, int]:
    """Rewrite every community_crystals row.  Returns
    ``(rewritten_count, skipped_count)``."""
    rewritten = 0
    skipped = 0
    # BL-114: re-render every crystal — current AND superseded.  The
    # ledger LEFT JOIN resolves a crystal's CURRENT label even after
    # re-clusters; falls back to '' for orphan crystals whose ledger
    # row has no current_cluster_id mapping anymore.
    rows = conn.execute(
        """
        SELECT cc.pack, cc.cluster_id, cc.body_md,
               cc.source_evergreen_slugs_json, cc.synthesized_at,
               cc.llm_model, cc.prompt_version,
               cc.superseded_by_synthesized_at,
               COALESCE(gc.label, '') AS label,
               COALESCE(gc.member_object_ids_json, '[]') AS members_json
          FROM community_crystals AS cc
          LEFT JOIN concept_identity_ledger cil
            ON cil.pack = cc.pack AND cil.concept_id = cc.concept_id
          LEFT JOIN graph_clusters AS gc
            ON gc.pack = cil.pack AND gc.cluster_id = cil.current_cluster_id
         WHERE cc.pack = ?
         ORDER BY cc.cluster_id, cc.synthesized_at
        """,
        (pack,),
    ).fetchall()
    for row in rows:
        (
            row_pack, cluster_id, body_md, slugs_json, synth_at,
            llm_model, prompt_version, superseded_by, label, members_json,
        ) = row
        try:
            slugs = tuple(json.loads(slugs_json))
            community_total = len(json.loads(members_json))
        except (TypeError, json.JSONDecodeError):
            skipped += 1
            continue
        crystal = CommunityCrystal(
            pack=row_pack, cluster_id=cluster_id, body_md=body_md,
            source_evergreen_slugs=slugs, synthesized_at=synth_at,
            llm_model=llm_model, prompt_version=prompt_version,
        )
        markdown = _render_community(
            crystal, label=label,
            community_total=community_total or None,
        )
        target = _resolve_target_path(
            vault_dir,
            live_filename=_community_filename(cluster_id),
            archive_subdir=(
                vault_dir / ARCHIVE_DIR_REL / _community_safe_id(cluster_id)
            ),
            synthesized_at=synth_at,
            is_current=(superseded_by == ""),
        )
        if dry_run:
            rewritten += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown, encoding="utf-8")
        rewritten += 1
    return rewritten, skipped


def _rerender_contradictions(
    conn: sqlite3.Connection,
    vault_dir: Path,
    *,
    pack: str,
    dry_run: bool,
) -> tuple[int, int]:
    rewritten = 0
    skipped = 0
    rows = conn.execute(
        """
        SELECT pack, contradiction_id, subject_key, body_md,
               positive_claim_ids_json, negative_claim_ids_json,
               source_object_ids_json, synthesized_at,
               llm_model, prompt_version,
               superseded_by_synthesized_at
          FROM contradiction_crystals
         WHERE pack = ?
         ORDER BY contradiction_id, synthesized_at
        """,
        (pack,),
    ).fetchall()
    for row in rows:
        (
            row_pack, contradiction_id, subject_key, body_md,
            pos_json, neg_json, src_json, synth_at,
            llm_model, prompt_version, superseded_by,
        ) = row
        try:
            positives = tuple(json.loads(pos_json))
            negatives = tuple(json.loads(neg_json))
            sources = tuple(json.loads(src_json))
        except (TypeError, json.JSONDecodeError):
            skipped += 1
            continue
        crystal = ContradictionCrystal(
            pack=row_pack, contradiction_id=contradiction_id,
            subject_key=subject_key, body_md=body_md,
            positive_claim_ids=positives,
            negative_claim_ids=negatives,
            source_object_ids=sources,
            synthesized_at=synth_at,
            llm_model=llm_model,
            prompt_version=prompt_version,
        )
        markdown = _render_contradiction(crystal)
        target = _resolve_target_path(
            vault_dir,
            live_filename=_contradiction_filename(contradiction_id),
            archive_subdir=(
                vault_dir / ARCHIVE_DIR_REL
                / _contradiction_safe_id(contradiction_id)
            ),
            synthesized_at=synth_at,
            is_current=(superseded_by == ""),
        )
        if dry_run:
            rewritten += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown, encoding="utf-8")
        rewritten += 1
    return rewritten, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate on-disk crystal markdowns from DB rows. "
                    "No LLM cost; uses the current renderer to refresh "
                    "format when the synthesis layer evolves.",
    )
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument(
        "--pack", type=str, default="research-tech",
        help="Pack scope (default: research-tech).",
    )
    parser.add_argument(
        "--kind", choices=["community", "contradiction", "all"],
        default="all",
        help="Crystal kind to rerender (default: all).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Count what would be rewritten, but don't touch disk.",
    )
    args = parser.parse_args(argv)

    vault = args.vault_dir.resolve()
    if not vault.is_dir():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 2
    layout = VaultLayout.from_vault(vault)
    if not layout.knowledge_db.exists():
        print(f"knowledge.db not found at {layout.knowledge_db}.",
              file=sys.stderr)
        return 2

    conn = sqlite3.connect(layout.knowledge_db)
    try:
        community_n = community_skipped = 0
        contra_n = contra_skipped = 0
        if args.kind in ("community", "all"):
            community_n, community_skipped = _rerender_communities(
                conn, vault, pack=args.pack, dry_run=args.dry_run,
            )
        if args.kind in ("contradiction", "all"):
            contra_n, contra_skipped = _rerender_contradictions(
                conn, vault, pack=args.pack, dry_run=args.dry_run,
            )
    finally:
        conn.close()

    verb = "would rewrite" if args.dry_run else "rewrote"
    print(f"=== Summary ({verb}) ===")
    print(f"  pack:                            {args.pack}")
    print(f"  community crystals {verb}:       {community_n}")
    if community_skipped:
        print(f"  community crystals skipped:      {community_skipped}")
    print(f"  contradiction crystals {verb}:   {contra_n}")
    if contra_skipped:
        print(f"  contradiction crystals skipped:  {contra_skipped}")
    if args.dry_run:
        print()
        print("--dry-run set; no files written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
