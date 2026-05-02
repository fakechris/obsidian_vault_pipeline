#!/usr/bin/env python3
"""
Promote Candidates - Review and promote candidate concepts to active.

Handles the candidate lifecycle:
- promote_to_active: Creates formal Evergreen file
- merge_as_alias: Adds aliases to existing active concept
- keep_as_candidate: Retains in candidate queue
- reject: Removes from registry

Usage:
    python -m ovp_pipeline.promote_candidates --list
    python -m ovp_pipeline.promote_candidates --review
    python -m ovp_pipeline.promote_candidates --promote <slug>
    python -m ovp_pipeline.promote_candidates --merge <slug> --target <target_slug>
    python -m ovp_pipeline.promote_candidates --reject <slug>
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .concept_registry import (
    ConceptRegistry,
    ConceptEntry,
    STATUS_ACTIVE,
    STATUS_CANDIDATE,
    STATUS_REJECTED,
)
from .identity import canonicalize_note_id
from .runtime import resolve_vault_dir

if TYPE_CHECKING:
    from .packs.base import BaseDomainPack


EVERGREEN_DIR = Path("10-Knowledge/Evergreen")
CANDIDATES_DIR = Path("10-Knowledge/Evergreen/_Candidates")
WIKILINK_PATTERN = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


@dataclass
class LifecycleMutation:
    action: str
    slug: str
    target_slug: str | None = None
    touched_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    link_updates: dict[str, int] = field(default_factory=dict)
    atlas_refreshed: bool = False

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "slug": self.slug,
            "target_slug": self.target_slug,
            "touched_files": self.touched_files,
            "deleted_files": self.deleted_files,
            "link_updates": self.link_updates,
            "atlas_refreshed": self.atlas_refreshed,
        }


def candidate_file_path(vault_dir: Path, slug: str) -> Path:
    return vault_dir / CANDIDATES_DIR / f"{slug}.md"


def evergreen_file_path(vault_dir: Path, slug: str) -> Path:
    return vault_dir / EVERGREEN_DIR / f"{slug}.md"


def write_evergreen_file(vault_dir: Path, entry: ConceptEntry, dry_run: bool = True) -> Path | None:
    """Write a formal Evergreen file for an active concept."""
    evergreen_path = evergreen_file_path(vault_dir, entry.slug)
    candidate_path = candidate_file_path(vault_dir, entry.slug)

    if dry_run:
        print(f"  [DRY RUN] Would create: {evergreen_path}")
        return None

    evergreen_path.parent.mkdir(parents=True, exist_ok=True)

    aliases = list(dict.fromkeys([alias for alias in entry.aliases if alias]))

    body = f"""# {entry.title}

> **定义**: {entry.definition}
"""
    if candidate_path.exists():
        candidate_text = candidate_path.read_text(encoding="utf-8")
        if candidate_text.startswith("---"):
            parts = candidate_text.split("---", 2)
            if len(parts) >= 3:
                body = parts[2].strip()
        body = body.replace("*Candidate concept - pending review*", "").strip()
        if not body.startswith("# "):
            body = f"# {entry.title}\n\n{body}".strip()

    from .object_kinds import CORE_OBJECT_KINDS, KIND_CONCEPT, normalize_kind

    normalized = normalize_kind(entry.kind) if entry.kind else KIND_CONCEPT
    entity_type = normalized if normalized in CORE_OBJECT_KINDS else KIND_CONCEPT

    frontmatter = f'''---
note_id: {entry.slug}
title: "{entry.title}"
type: evergreen
entity_type: {entity_type}
date: {datetime.now().strftime("%Y-%m-%d")}
tags: [{entry.area}, evergreen]
aliases: [{", ".join(f'"{a}"' for a in aliases)}]
area: {entry.area}
---

{body}

---

*Promoted from candidate on {datetime.now().strftime("%Y-%m-%d")}*
'''

    from .workspace_promotion import WRITE_MODE_PROMOTION, enforce_zone_write

    try:
        enforce_zone_write(
            evergreen_path,
            vault_dir=vault_dir,
            mode=WRITE_MODE_PROMOTION,
        )
    except Exception:
        # Permissive packs raise nothing; strict packs honor the gate. We
        # never skip the actual write — the gate fires only on misuse from
        # callers that forgot to pass mode='promotion'.
        raise

    evergreen_path.write_text(frontmatter, encoding="utf-8")
    print(f"  Created: {evergreen_path}")
    return evergreen_path


def write_candidate_file(
    vault_dir: Path,
    entry: ConceptEntry,
    dry_run: bool = True,
    *,
    concept_data: dict | None = None,
    source_file: Path | None = None,
) -> Path | None:
    """Write a candidate file in the _Candidates directory."""
    candidates_dir = vault_dir / CANDIDATES_DIR
    candidate_path = candidate_file_path(vault_dir, entry.slug)

    if dry_run:
        print(f"  [DRY RUN] Would create candidate file: {candidate_path}")
        return None

    candidates_dir.mkdir(parents=True, exist_ok=True)

    aliases = list(dict.fromkeys([alias for alias in entry.aliases if alias]))
    explanation = str((concept_data or {}).get("explanation", "")).strip()
    importance = str((concept_data or {}).get("importance", "")).strip()
    raw_related = (concept_data or {}).get("related_concepts", []) or []
    related = []
    for item in raw_related:
        normalized = canonicalize_note_id(str(item))
        if normalized and normalized != entry.slug and normalized not in related:
            related.append(normalized)

    related_block = "\n".join(f"- [[{slug}]]" for slug in related) or "- 暂无"
    source_link = ""
    if source_file is not None:
        source_link = f"\n## 📚 来源\n- [[{source_file.stem}]]\n"

    from .object_kinds import CORE_OBJECT_KINDS, KIND_CONCEPT, normalize_kind

    candidate_kind = normalize_kind(entry.kind) if entry.kind else KIND_CONCEPT
    if candidate_kind not in CORE_OBJECT_KINDS:
        candidate_kind = KIND_CONCEPT

    frontmatter = f'''---
note_id: {entry.slug}
title: "{entry.title}"
type: candidate
entity_type: {candidate_kind}
date: {datetime.now().strftime("%Y-%m-%d")}
tags: [candidate, {entry.area}]
aliases: [{", ".join(f'"{a}"' for a in aliases)}]
area: {entry.area}
review_state: {entry.review_state}
---

# {entry.title}

> **定义**: {entry.definition}

## 📝 详细解释
{explanation or "待补充"}

## 为什么重要
{importance or "待补充"}

## 🔗 关联概念
{related_block}
{source_link}

---

*Candidate concept - pending review*
'''

    candidate_path.write_text(frontmatter, encoding="utf-8")
    print(f"  Created candidate: {candidate_path}")
    return candidate_path


def delete_candidate_file(vault_dir: Path, slug: str, dry_run: bool = True) -> Path | None:
    """Remove a candidate file if it exists."""
    path = candidate_file_path(vault_dir, slug)
    if not path.exists():
        return None
    if dry_run:
        print(f"  [DRY RUN] Would delete candidate file: {path}")
        return path
    path.unlink()
    print(f"  Deleted candidate file: {path}")
    return path


def refresh_atlas_from_registry(vault_dir: Path, dry_run: bool = True) -> bool:
    """Refresh Atlas index from registry."""
    try:
        from .auto_moc_updater import MOCUpdater, PipelineLogger
    except ImportError:
        from auto_moc_updater import MOCUpdater, PipelineLogger  # type: ignore

    logger = PipelineLogger(vault_dir / "60-Logs" / "pipeline.jsonl")
    updater = MOCUpdater(vault_dir, logger)
    result = updater.update_atlas_from_registry(dry_run=dry_run)
    return not result.get("errors")


def rewrite_candidate_links(
    vault_dir: Path,
    source_surfaces: list[str],
    target_slug: str,
    dry_run: bool = True,
) -> dict[str, int]:
    """
    Rewrite wikilinks that point to a merged candidate so they target the active slug.

    The visible display text is preserved where possible.
    """
    normalized = {canonicalize_note_id(surface) for surface in source_surfaces if surface}
    normalized = {surface for surface in normalized if surface}
    if not normalized:
        return {}

    scan_roots = [
        vault_dir / "10-Knowledge" / "Evergreen",
        vault_dir / "20-Areas",
    ]
    updates: dict[str, int] = {}

    for root in scan_roots:
        if not root.exists():
            continue
        for md_file in root.rglob("*.md"):
            if md_file.parts[-2:] == ("_Candidates", md_file.name):
                continue
            content = md_file.read_text(encoding="utf-8")
            replacements = 0

            def repl(match: re.Match[str]) -> str:
                nonlocal replacements
                target_raw = match.group(1).strip()
                display = match.group(2).strip() if match.group(2) else ""
                if canonicalize_note_id(target_raw) not in normalized:
                    return match.group(0)

                replacements += 1
                if display:
                    return f"[[{target_slug}|{display}]]"
                if canonicalize_note_id(target_raw) == target_slug:
                    return f"[[{target_slug}]]"
                return f"[[{target_slug}|{target_raw}]]"

            rewritten = WIKILINK_PATTERN.sub(repl, content)
            if replacements == 0:
                continue
            updates[str(md_file)] = replacements
            if not dry_run:
                md_file.write_text(rewritten, encoding="utf-8")

    return updates


def promote_candidate(vault_dir: Path, slug: str, dry_run: bool = True) -> LifecycleMutation:
    """Promote a candidate and synchronize filesystem side effects.

    Before creating a new Evergreen, a trigram-Jaccard similarity check runs
    against existing active slugs.  If a near-duplicate is found (>= 0.82),
    the candidate is merged into the existing Evergreen instead of creating a
    new file — preventing paraphrastic clones at the source.
    """
    registry = ConceptRegistry(vault_dir).load()
    entry = registry.find_by_slug(slug)
    if not entry:
        raise ValueError(f"Concept '{slug}' not found")
    if entry.status != STATUS_CANDIDATE:
        raise ValueError(f"'{slug}' is not a candidate (status: {entry.status})")

    try:
        from .concept_dedup import DEFAULT_THRESHOLD, find_similar_slugs

        similar = find_similar_slugs(vault_dir, slug, threshold=DEFAULT_THRESHOLD)
        if similar:
            for match_slug, match_sim in similar:
                match_entry = registry.find_by_slug(match_slug)
                if match_entry and match_entry.status == STATUS_ACTIVE:
                    print(
                        f"  [dedup-guard] '{slug}' similar to active '{match_slug}' "
                        f"(sim={match_sim:.2f}) — merging instead of promoting."
                    )
                    return merge_candidate(vault_dir, slug, match_slug, dry_run=dry_run)
    except (ImportError, FileNotFoundError, OSError) as exc:
        try:
            from .auto_moc_updater import PipelineLogger as _PL

            _PL(vault_dir / "60-Logs" / "pipeline.jsonl").log(
                "dedup_guard_failure",
                {"slug": slug, "error": str(exc), "policy": "fail-closed"},
            )
        except Exception:
            pass
        raise RuntimeError(
            f"[dedup-guard] Similarity check failed for '{slug}': {exc}. "
            f"Resolve the underlying error and retry."
        ) from exc

    mutation = LifecycleMutation(action="promote", slug=slug, target_slug=slug)

    registry.promote_to_active(slug)
    entry = registry.find_by_slug(slug)
    evergreen_path = write_evergreen_file(vault_dir, entry, dry_run=dry_run)
    if evergreen_path:
        mutation.touched_files.append(str(evergreen_path))
    deleted = delete_candidate_file(vault_dir, slug, dry_run=dry_run)
    if deleted:
        mutation.deleted_files.append(str(deleted))

    if not dry_run:
        registry.save()
    mutation.atlas_refreshed = refresh_atlas_from_registry(vault_dir, dry_run=dry_run)
    if mutation.atlas_refreshed:
        mutation.touched_files.append(str(vault_dir / "10-Knowledge" / "Atlas" / "Atlas-Index.md"))

    return mutation


def merge_candidate(vault_dir: Path, slug: str, target_slug: str, dry_run: bool = True) -> LifecycleMutation:
    """Merge a candidate as alias and migrate obvious wikilinks."""
    registry = ConceptRegistry(vault_dir).load()
    candidate = registry.find_by_slug(slug)
    target = registry.find_by_slug(target_slug)
    if not candidate:
        raise ValueError(f"Candidate '{slug}' not found")
    if not target:
        raise ValueError(f"Target '{target_slug}' not found")
    if candidate.status == STATUS_ACTIVE:
        raise ValueError(f"'{slug}' is already active, cannot merge")

    aliases_to_add = [candidate.title, *candidate.aliases]
    source_surfaces = [candidate.slug, candidate.title, *candidate.aliases]

    mutation = LifecycleMutation(action="merge", slug=slug, target_slug=target_slug)
    registry.merge_as_alias(slug, target_slug, aliases_to_add)
    mutation.link_updates = rewrite_candidate_links(
        vault_dir,
        source_surfaces=source_surfaces,
        target_slug=target_slug,
        dry_run=dry_run,
    )
    deleted = delete_candidate_file(vault_dir, slug, dry_run=dry_run)
    if deleted:
        mutation.deleted_files.append(str(deleted))

    if not dry_run:
        registry.save()
    mutation.atlas_refreshed = refresh_atlas_from_registry(vault_dir, dry_run=dry_run)
    if mutation.atlas_refreshed:
        mutation.touched_files.append(str(vault_dir / "10-Knowledge" / "Atlas" / "Atlas-Index.md"))

    return mutation


def reject_candidate(vault_dir: Path, slug: str, dry_run: bool = True) -> LifecycleMutation:
    """Reject a candidate and remove candidate filesystem artifacts."""
    registry = ConceptRegistry(vault_dir).load()
    entry = registry.find_by_slug(slug)
    if not entry:
        raise ValueError(f"Concept '{slug}' not found")

    mutation = LifecycleMutation(action="reject", slug=slug)
    registry.reject(slug)
    deleted = delete_candidate_file(vault_dir, slug, dry_run=dry_run)
    if deleted:
        mutation.deleted_files.append(str(deleted))

    if not dry_run:
        registry.save()
    mutation.atlas_refreshed = refresh_atlas_from_registry(vault_dir, dry_run=dry_run)
    if mutation.atlas_refreshed:
        mutation.touched_files.append(str(vault_dir / "10-Knowledge" / "Atlas" / "Atlas-Index.md"))

    return mutation


def list_candidates(registry: ConceptRegistry) -> None:
    """List all pending candidates."""
    candidates = registry.candidates

    if not candidates:
        print("No pending candidates")
        return

    print(f"Pending candidates: {len(candidates)}")
    print()

    for entry in candidates:
        print(f"[{entry.slug}]")
        print(f"  Title: {entry.title}")
        print(f"  Definition: {entry.definition[:100]}...")
        print(f"  Area: {entry.area}")
        print(f"  Source count: {entry.source_count}")
        print(f"  Evidence count: {entry.evidence_count}")
        print(f"  Aliases: {entry.aliases}")
        print()


def review_candidates(
    registry: ConceptRegistry,
    *,
    pack: "BaseDomainPack | None" = None,
) -> list[tuple[ConceptEntry, str, list]]:
    """
    Review candidates and suggest actions.

    Returns list of (entry, suggested_action, similar_existing) tuples.

    Phase 34: routes through ``promotion_policy.evaluate_concept`` so each
    pack's ``PromotionPolicySpec`` decides the lane. ``default-knowledge``
    short-circuits to the legacy OR rule (bit-for-bit compat); strict packs
    apply ``require_independent_sources`` / ``require_evidence_kinds`` etc.
    """
    from .packs.loader import DEFAULT_WORKFLOW_PACK_NAME, load_pack
    from .promotion_policy import (
        LANE_AUTO,
        LANE_ESCALATE,
        collect_pack_signals,
        evaluate_concept,
    )
    from .runtime import VaultLayout

    resolved_pack = pack or load_pack(DEFAULT_WORKFLOW_PACK_NAME)
    layout = VaultLayout.from_vault(registry.vault_dir)
    kinds_by_id, disputed_ids = collect_pack_signals(
        layout.knowledge_db,
        pack_name=resolved_pack.name,
        candidates_dir=registry.vault_dir / CANDIDATES_DIR,
    )
    suggestions = []

    for entry in registry.candidates:
        # Find similar active concepts
        similar = registry.search(entry.title, topk=5)
        similar = [(e, s) for e, s in similar if e.slug != entry.slug and e.status == STATUS_ACTIVE]

        decision = evaluate_concept(
            entry,
            pack=resolved_pack,
            registry=registry,
            evidence_kinds=kinds_by_id.get(entry.slug, frozenset()),
            has_open_contradiction=entry.slug in disputed_ids,
        )

        if decision.lane == LANE_AUTO:
            if similar and similar[0][1] >= 0.7:
                action = "merge_as_alias"
            else:
                action = "promote_to_active"
        elif decision.lane == LANE_ESCALATE:
            action = "escalate_to_workbench"
        else:  # LANE_HOLD or LANE_REJECT
            action = "keep_as_candidate"
            if similar and similar[0][1] >= 0.8:
                action = "merge_as_alias"

        suggestions.append((entry, action, similar))

    return suggestions


def cmd_list(args: argparse.Namespace) -> None:
    """Handle --list command."""
    registry = ConceptRegistry(args.vault_dir).load()
    list_candidates(registry)


def cmd_review(args: argparse.Namespace) -> None:
    """Handle --review command."""
    registry = ConceptRegistry(args.vault_dir).load()

    print("Reviewing candidates...")
    suggestions = review_candidates(registry)

    print(f"\nReviewed {len(suggestions)} candidates\n")

    for entry, action, similar in suggestions:
        print(f"[{entry.slug}]")
        print(f"  Title: {entry.title}")
        print(f"  Suggested action: {action}")
        if similar:
            print(f"  Similar concepts:")
            for e, score in similar[:3]:
                print(f"    - [{e.slug}] (score: {score:.2f}): {e.title}")
        print()


def cmd_promote(args: argparse.Namespace) -> None:
    """Handle --promote <slug> command."""
    dry_run = args.dry_run

    if dry_run:
        print(f"[DRY RUN] Promoting candidate: {args.slug}")

    mutation = promote_candidate(args.vault_dir, args.slug, dry_run=dry_run)
    print(json.dumps(mutation.to_dict(), ensure_ascii=False, indent=2))


def cmd_merge(args: argparse.Namespace) -> None:
    """Handle --merge <slug> --target <target_slug> command."""
    dry_run = args.dry_run

    if dry_run:
        print(f"[DRY RUN] Merging '{args.slug}' as alias of '{args.target}'")
    mutation = merge_candidate(args.vault_dir, args.slug, args.target, dry_run=dry_run)
    print(json.dumps(mutation.to_dict(), ensure_ascii=False, indent=2))


def cmd_reject(args: argparse.Namespace) -> None:
    """Handle --reject <slug> command."""
    dry_run = args.dry_run

    if dry_run:
        print(f"[DRY RUN] Rejecting candidate: {args.slug}")
    mutation = reject_candidate(args.vault_dir, args.slug, dry_run=dry_run)
    print(json.dumps(mutation.to_dict(), ensure_ascii=False, indent=2))


def cmd_write_candidates(args: argparse.Namespace) -> None:
    """Handle --write-candidates command."""
    registry = ConceptRegistry(args.vault_dir).load()
    dry_run = args.dry_run

    candidates = registry.candidates
    if not candidates:
        print("No candidates to write")
        return

    if dry_run:
        print(f"[DRY RUN] Writing {len(candidates)} candidate files")

    for entry in candidates:
        write_candidate_file(args.vault_dir, entry, dry_run=dry_run)

    print(f"Wrote {len(candidates)} candidate files")


def main():
    parser = argparse.ArgumentParser(description="Promote and manage candidate concepts")
    parser.add_argument("--vault-dir", type=Path, default=None)

    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    subparsers.add_parser("list", help="List pending candidates")

    # review
    subparsers.add_parser("review", help="Review candidates and suggest actions")

    # promote
    promote_parser = subparsers.add_parser("promote", help="Promote a candidate to active")
    promote_parser.add_argument("slug", help="Candidate slug to promote")
    promote_parser.add_argument("--dry-run", action="store_true", help="Dry run")

    # merge
    merge_parser = subparsers.add_parser("merge", help="Merge a candidate as alias")
    merge_parser.add_argument("slug", help="Candidate slug to merge")
    merge_parser.add_argument("--target", required=True, help="Target slug to merge into")
    merge_parser.add_argument("--dry-run", action="store_true", help="Dry run")

    # reject
    reject_parser = subparsers.add_parser("reject", help="Reject a candidate")
    reject_parser.add_argument("slug", help="Candidate slug to reject")
    reject_parser.add_argument("--dry-run", action="store_true", help="Dry run")

    # write-candidates
    write_parser = subparsers.add_parser("write-candidates", help="Write candidate files")
    write_parser.add_argument("--dry-run", action="store_true", help="Dry run")

    args = parser.parse_args()

    args.vault_dir = resolve_vault_dir(args.vault_dir)

    if args.command == "list":
        cmd_list(args)
    elif args.command == "review":
        cmd_review(args)
    elif args.command == "promote":
        cmd_promote(args)
    elif args.command == "merge":
        cmd_merge(args)
    elif args.command == "reject":
        cmd_reject(args)
    elif args.command == "write-candidates":
        cmd_write_candidates(args)


if __name__ == "__main__":
    main()
