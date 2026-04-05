#!/usr/bin/env python3
"""
Promote Candidates - Review and promote candidate concepts to active.

Handles the candidate lifecycle:
- promote_to_active: Creates formal Evergreen file
- merge_as_alias: Adds aliases to existing active concept
- keep_as_candidate: Retains in candidate queue
- reject: Removes from registry

Usage:
    python -m openclaw_pipeline.promote_candidates --list
    python -m openclaw_pipeline.promote_candidates --review
    python -m openclaw_pipeline.promote_candidates --promote <slug>
    python -m openclaw_pipeline.promote_candidates --merge <slug> --target <target_slug>
    python -m openclaw_pipeline.promote_candidates --reject <slug>
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .concept_registry import (
    ConceptRegistry,
    ConceptEntry,
    STATUS_ACTIVE,
    STATUS_CANDIDATE,
    STATUS_REJECTED,
)


EVERGREEN_DIR = Path("10-Knowledge/Evergreen")
CANDIDATES_DIR = Path("10-Knowledge/Evergreen/_Candidates")


def write_evergreen_file(vault_dir: Path, entry: ConceptEntry, dry_run: bool = True) -> Path | None:
    """Write a formal Evergreen file for an active concept."""
    evergreen_path = vault_dir / EVERGREEN_DIR / f"{entry.slug}.md"

    if dry_run:
        print(f"  [DRY RUN] Would create: {evergreen_path}")
        return None

    evergreen_path.parent.mkdir(parents=True, exist_ok=True)

    frontmatter = f'''---
title: "{entry.title}"
type: evergreen
date: {datetime.now().strftime("%Y-%m-%d")}
tags: [{entry.area}, evergreen]
aliases: [{", ".join(f'"{a}"' for a in entry.aliases)}]
area: {entry.area}
---

# {entry.title}

> **定义**: {entry.definition}

---

*Promoted from candidate on {datetime.now().strftime("%Y-%m-%d")}*
'''

    evergreen_path.write_text(frontmatter, encoding="utf-8")
    print(f"  Created: {evergreen_path}")
    return evergreen_path


def write_candidate_file(vault_dir: Path, entry: ConceptEntry, dry_run: bool = True) -> Path | None:
    """Write a candidate file in the _Candidates directory."""
    candidates_dir = vault_dir / CANDIDATES_DIR
    candidate_path = candidates_dir / f"{entry.slug}.md"

    if dry_run:
        print(f"  [DRY RUN] Would create candidate file: {candidate_path}")
        return None

    candidates_dir.mkdir(parents=True, exist_ok=True)

    frontmatter = f'''---
title: "{entry.title}"
type: candidate
date: {datetime.now().strftime("%Y-%m-%d")}
tags: [candidate, {entry.area}]
aliases: [{", ".join(f'"{a}"' for a in entry.aliases)}]
area: {entry.area}
review_state: {entry.review_state}
---

# {entry.title}

> **定义**: {entry.definition}

---

*Candidate concept - pending review*
'''

    candidate_path.write_text(frontmatter, encoding="utf-8")
    print(f"  Created candidate: {candidate_path}")
    return candidate_path


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


def review_candidates(registry: ConceptRegistry) -> list[tuple[ConceptEntry, str, list]]:
    """
    Review candidates and suggest actions.

    Returns list of (entry, suggested_action, similar_existing) tuples.
    """
    suggestions = []

    for entry in registry.candidates:
        # Find similar active concepts
        similar = registry.search(entry.title, topk=5)
        similar = [(e, s) for e, s in similar if e.slug != entry.slug and e.status == STATUS_ACTIVE]

        # Determine suggested action based on criteria
        action = "keep_as_candidate"

        # If appears in multiple sources, consider promote
        if entry.source_count >= 2 or entry.evidence_count >= 3:
            if similar and similar[0][1] >= 0.7:
                action = "merge_as_alias"
            else:
                action = "promote_to_active"
        elif similar and similar[0][1] >= 0.8:
            # Very similar to existing concept
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
    registry = ConceptRegistry(args.vault_dir).load()
    dry_run = args.dry_run

    if dry_run:
        print(f"[DRY RUN] Promoting candidate: {args.slug}")

    entry = registry.find_by_slug(args.slug)
    if not entry:
        print(f"Error: Concept '{args.slug}' not found")
        return

    if entry.status != STATUS_CANDIDATE:
        print(f"Error: '{args.slug}' is not a candidate (status: {entry.status})")
        return

    # Promote
    registry.promote_to_active(args.slug)
    entry = registry.find_by_slug(args.slug)

    # Write Evergreen file
    write_evergreen_file(args.vault_dir, entry, dry_run=dry_run)

    # Save registry
    if not dry_run:
        registry.save()
        print(f"Promoted '{args.slug}' to active")
    else:
        print(f"[DRY RUN] Would promote and create Evergreen file")


def cmd_merge(args: argparse.Namespace) -> None:
    """Handle --merge <slug> --target <target_slug> command."""
    registry = ConceptRegistry(args.vault_dir).load()
    dry_run = args.dry_run

    if dry_run:
        print(f"[DRY RUN] Merging '{args.slug}' as alias of '{args.target}'")

    candidate = registry.find_by_slug(args.slug)
    target = registry.find_by_slug(args.target)

    if not candidate:
        print(f"Error: Candidate '{args.slug}' not found")
        return

    if not target:
        print(f"Error: Target '{args.target}' not found")
        return

    if candidate.status == STATUS_ACTIVE:
        print(f"Error: '{args.slug}' is already active, cannot merge")
        return

    # Aliases to add from candidate
    aliases_to_add = [candidate.title] + candidate.aliases

    # Merge
    registry.merge_as_alias(args.slug, args.target, aliases_to_add)

    # Save
    if not dry_run:
        registry.save()
        print(f"Merged '{args.slug}' as alias of '{args.target}'")
        print(f"  Aliases added: {aliases_to_add}")
    else:
        print(f"[DRY RUN] Would merge with aliases: {aliases_to_add}")


def cmd_reject(args: argparse.Namespace) -> None:
    """Handle --reject <slug> command."""
    registry = ConceptRegistry(args.vault_dir).load()
    dry_run = args.dry_run

    if dry_run:
        print(f"[DRY RUN] Rejecting candidate: {args.slug}")

    entry = registry.find_by_slug(args.slug)
    if not entry:
        print(f"Error: Concept '{args.slug}' not found")
        return

    registry.reject(args.slug)

    if not dry_run:
        registry.save()
        print(f"Rejected '{args.slug}'")
    else:
        print(f"[DRY RUN] Would reject '{args.slug}'")


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
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())

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
