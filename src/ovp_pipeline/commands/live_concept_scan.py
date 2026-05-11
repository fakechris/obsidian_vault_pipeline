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
        "--fire",
        action="store_true",
        help=(
            "BL-063 PR#3: actually invoke the agent on fired concepts "
            "instead of just reporting.  Each agent run hits an LLM, "
            "rewrites the agent-owned sections, and stamps lastRunAt + "
            "lastRunSummary.  Implies --only-fired."
        ),
    )
    parser.add_argument(
        "--max-fires",
        type=int,
        default=10,
        help="Cap on how many agent runs to issue in one scan "
             "(default 10).  Backstop against runaway cost when many "
             "triggers fire at once.",
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
    fire_mode = args.fire
    if args.only_fired or fire_mode:
        evaluations = [e for e in evaluations if e.has_any_trigger]

    outcomes: list[dict] = []
    if fire_mode and evaluations:
        outcomes = _fire_agent_for_evaluations(
            vault_dir, evaluations, max_fires=args.max_fires,
        )

    if args.json:
        payload = {
            "vault_dir": str(vault_dir),
            "pack": args.pack,
            "since_hours": args.since_hours,
            "scanned_at": now.isoformat().replace("+00:00", "Z"),
            "evaluation_count": len(evaluations),
            "fire_mode": fire_mode,
            "evaluations": [_evaluation_to_dict(e) for e in evaluations],
            "fire_outcomes": outcomes,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_text_report(evaluations)
        if outcomes:
            print()
            print(f"Fired {len(outcomes)} agent run(s):")
            for o in outcomes:
                marker = "✓" if o["status"] == "ok" else "✗"
                summary = o.get("summary", "") or o.get("error", "")
                print(f"  {marker} {o['slug']} → {o['status']}  {summary[:80]}")
    return 0


def _fire_agent_for_evaluations(
    vault_dir: Path,
    evaluations: list[ConceptEvaluation],
    *,
    max_fires: int,
) -> list[dict]:
    """For each evaluation that has any trigger firing, invoke the
    BL-063 PR#3 agent.  Caps total fires at ``max_fires`` to avoid
    runaway LLM costs when many concepts trigger at once.

    Each agent run:
    1. Stamps lastAttemptAt on the live concept frontmatter
       (backoff anchor).
    2. Calls the synthesis LLM with concept context.
    3. Writes section deltas via patch_agent_section.
    4. Stamps lastRunAt + lastRunSummary on success, lastRunError
       on failure.
    5. Emits a ``live_concept_agent_run`` audit event.

    Best-effort: one concept failing does not abort the batch.
    Returns one outcome dict per fire so the JSON output / text
    report can surface per-concept status.
    """
    from ..auto_article_processor import PipelineLogger
    from ..live_concept_agent import fire_agent_for_concept
    from ..llm_client import get_litellm_client
    from ..runtime import VaultLayout

    layout = VaultLayout.from_vault(vault_dir)
    pipeline_logger = PipelineLogger(layout.pipeline_log)
    llm_client = get_litellm_client(vault_dir)
    if llm_client is None:
        return [{
            "slug": e.handle.slug,
            "status": "skip",
            "error": "no API key configured; agent cannot run",
        } for e in evaluations[:max_fires]]

    out: list[dict] = []
    for e in evaluations[:max_fires]:
        outcome = fire_agent_for_concept(
            e.handle,
            llm_client=llm_client,
            recent_route_decisions=[
                # The trigger evaluator stored IngestMatch dataclasses;
                # the agent prompt prefers the raw audit shape.  Build
                # a minimal payload-shaped row so the prompt template
                # gets what it expects.
                {"payload": {
                    "source": m.source_path,
                    "update_slugs": [m.matched_slug],
                    "create_titles": [],
                    "source_value_summary": "",
                }}
                for m in e.ingest_matches
            ],
            open_contradictions=[
                {
                    "contradiction_id": m.contradiction_id,
                    "subject_key": m.subject_key,
                    "positive_claim_ids": [],
                    "negative_claim_ids": [],
                }
                for m in e.contradiction_matches
            ],
            pipeline_logger=pipeline_logger,
        )
        out.append({
            "slug": outcome.handle.slug,
            "status": outcome.status,
            "summary": outcome.summary,
            "error": outcome.error,
            "sections_written": outcome.sections_written,
        })
    return out


if __name__ == "__main__":
    raise SystemExit(main())
