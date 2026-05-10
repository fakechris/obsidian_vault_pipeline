"""``ovp-live-concept-scan`` — BL-063 PR#2 read-only trigger preview.

Walks every active live concept and reports which triggers would
fire right now.  Read-only: no ``patch_live`` calls, no agent
invocation, no audit emission.  PR#3 will replace this with the
actual fire-the-agent runner; this CLI's purpose is to let the
operator dry-run the trigger logic and audit it before flipping
the live wire.

Examples
--------

::

    # Show every concept's trigger state (not just fired ones).
    ovp-live-concept-scan

    # Only concepts where at least one trigger would fire.
    ovp-live-concept-scan --only-fired

    # JSON output for piping into jq / other tools.
    ovp-live-concept-scan --json

    # Widen the recency window for on_ingest_match to a week.
    ovp-live-concept-scan --since-hours 168
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from ..live_concept_scheduler import ConceptEvaluation, evaluate_all_concepts
from ..runtime import resolve_vault_dir


def _evaluation_to_dict(e: ConceptEvaluation) -> dict[str, object]:
    return {
        "slug": e.handle.slug,
        "relative_path": e.handle.relative_path,
        "objective": e.handle.frontmatter.objective,
        "active": e.handle.frontmatter.is_active,
        "scope_evergreens": list(e.handle.frontmatter.scope_evergreens),
        "weekly_due": e.weekly_due,
        "ingest_matches": [
            {
                "source_path": m.source_path,
                "matched_slug": m.matched_slug,
                "matched_via": m.matched_via,
                "timestamp": m.timestamp,
            }
            for m in e.ingest_matches
        ],
        "contradiction_matches": [
            {
                "contradiction_id": m.contradiction_id,
                "subject_key": m.subject_key,
                "matched_slug": m.matched_slug,
                "status": m.status,
            }
            for m in e.contradiction_matches
        ],
    }


def _print_text_report(evaluations: list[ConceptEvaluation]) -> None:
    if not evaluations:
        print("No active live concepts.")
        return
    fired = sum(1 for e in evaluations if e.has_any_trigger)
    print(
        f"Evaluated {len(evaluations)} active live concept(s); "
        f"{fired} would fire."
    )
    for e in evaluations:
        h = e.handle
        objective = h.frontmatter.objective.replace("\n", " ").strip()
        if len(objective) > 80:
            objective = objective[:77] + "..."
        print()
        print(f"  {h.slug}  ({h.relative_path})")
        print(f"    objective: {objective}")
        if e.weekly_due:
            print("    weekly_resynthesis: DUE")
        if e.ingest_matches:
            print(f"    on_ingest_match: {len(e.ingest_matches)} match(es)")
            for m in e.ingest_matches[:5]:
                print(
                    f"      - {m.source_path}  "
                    f"-> {m.matched_slug} (via {m.matched_via})"
                )
            if len(e.ingest_matches) > 5:
                print(f"      ... +{len(e.ingest_matches) - 5} more")
        if e.contradiction_matches:
            print(
                "    on_contradiction_against_view: "
                f"{len(e.contradiction_matches)} match(es)"
            )
            for m in e.contradiction_matches[:5]:
                print(
                    f"      - {m.contradiction_id}: {m.subject_key}  "
                    f"(scope: {m.matched_slug})"
                )
            if len(e.contradiction_matches) > 5:
                print(f"      ... +{len(e.contradiction_matches) - 5} more")
        if not e.has_any_trigger:
            print("    (no triggers fired)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate Live Concept triggers. Read-only — no agent "
            "fired, no patch_live calls."
        ),
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=None,
        help="Vault directory (default: $OVP_VAULT_DIR / cwd marker).",
    )
    parser.add_argument(
        "--pack",
        default=None,
        help="Truth pack name (default: pack inferred from vault).",
    )
    parser.add_argument(
        "--since-hours",
        type=int,
        default=24,
        help="Recency window for on_ingest_match (hours, default 24).",
    )
    parser.add_argument(
        "--only-fired",
        action="store_true",
        help="Only show concepts where at least one trigger would fire.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    now = datetime.now(timezone.utc)
    evaluations = evaluate_all_concepts(
        vault_dir,
        pack_name=args.pack,
        since_hours=args.since_hours,
        now=now,
    )
    if args.only_fired:
        evaluations = [e for e in evaluations if e.has_any_trigger]

    if args.json:
        payload = {
            "vault_dir": str(vault_dir),
            "pack": args.pack,
            "since_hours": args.since_hours,
            "scanned_at": now.isoformat().replace("+00:00", "Z"),
            "evaluation_count": len(evaluations),
            "evaluations": [_evaluation_to_dict(e) for e in evaluations],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_text_report(evaluations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
