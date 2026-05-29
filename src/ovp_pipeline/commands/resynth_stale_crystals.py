"""``ovp-resynth-stale-crystals`` — BL-117 budget-capped delta synthesis.

Pre-BL-117 the only re-synthesis path was ``ovp-synthesize-community-crystals``
which writes a crystal for EVERY cluster (skipping only those that
already have a row).  That CLI is fine for cold starts but burns
hundreds of LLM dollars on a vault that's mostly fresh — the
operator wants "find the worst-stale 20 concepts and refresh those".

This CLI:

  1. Calls ``compute_crystal_staleness`` on the pack — the same
     four-signal stale set the nightly pipeline step uses.
  2. Slices to ``--max`` (default 20, ~$1/day at MiniMax pricing).
  3. Hands those concepts to a thin wrapper around
     ``synthesize_community_crystals`` that bypasses the cluster
     discovery loop and just re-runs LLM synthesis for the chosen
     concepts.
  4. Emits ``crystal_staleness_evaluated`` (count + signal breakdown)
     + ``crystal_resynthesized`` (one per concept) audit events.

The same module is invoked by the ``synthesize`` pipeline step the
nightly DAG runs after ``knowledge_index``.  CLI + step share the
same entrypoint so a manual catch-up and an automated run produce
byte-identical state transitions.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

from ..runtime import VaultLayout, resolve_vault_dir

logger = logging.getLogger(__name__)


DEFAULT_BUDGET = 20


def _emit(vault_dir: Path, event_type: str, payload: dict, pack: str) -> None:
    """Thin wrapper around event_emitter.emit so the import lives in
    one place and audit failures degrade gracefully (logged, never
    raised — the canonical state is the crystals table, not the
    audit log)."""
    try:
        from ..event_emitter import emit as _emit_audit
        _emit_audit(vault_dir, "pipeline.jsonl", event_type, payload, pack=pack)
    except Exception:  # noqa: BLE001
        logger.warning("audit emit failed for %s", event_type)


def resynth_stale_crystals(
    *,
    vault_dir: Path,
    pack: str,
    budget: int = DEFAULT_BUDGET,
    dry_run: bool = False,
) -> dict:
    """Compute the stale set, slice to budget, synthesize.

    Returns a summary dict: ``{evaluated, scheduled, synthesized,
    skipped, signal_breakdown}``.  Caller (CLI or pipeline step)
    decides what to print.
    """
    from ..synthesis.staleness import compute_crystal_staleness

    layout = VaultLayout.from_vault(vault_dir)
    if not layout.knowledge_db.exists():
        logger.warning(
            "resynth-stale: knowledge.db missing at %s — run "
            "ovp-knowledge-index first",
            layout.knowledge_db,
        )
        return {
            "evaluated": 0, "scheduled": 0, "synthesized": 0,
            "skipped": 0, "signal_breakdown": {},
        }

    with sqlite3.connect(layout.knowledge_db) as conn:
        stale = compute_crystal_staleness(conn, pack=pack)

    signal_breakdown: dict[str, int] = {}
    for s in stale:
        signal_breakdown[s.primary_signal] = (
            signal_breakdown.get(s.primary_signal, 0) + 1
        )

    _emit(vault_dir, "crystal_staleness_evaluated", {
        "pack": pack,
        "evaluated": len(stale),
        "budget": budget,
        "signal_breakdown": signal_breakdown,
    }, pack=pack)

    if not stale:
        return {
            "evaluated": 0, "scheduled": 0, "synthesized": 0,
            "skipped": 0, "signal_breakdown": signal_breakdown,
        }

    scheduled = stale[:budget]

    if dry_run:
        return {
            "evaluated": len(stale),
            "scheduled": len(scheduled),
            "synthesized": 0,
            "skipped": 0,
            "signal_breakdown": signal_breakdown,
            "concepts": [s.concept_id for s in scheduled],
        }

    # Re-synthesize the chosen concepts via the existing bulk
    # synthesizer, scoped to the stale concepts' current_cluster_id
    # set.  ``only_cluster_ids`` is the surgical filter that lets us
    # bound the LLM calls to exactly the stale set without re-running
    # over every Louvain cluster.  ``skip_existing=False`` so a stale
    # concept with an active crystal still gets a fresh version
    # (which then supersedes the prior via the BL-114 ``_versioning``
    # pipeline).
    from ..llm_client import get_litellm_client
    from ..synthesis.community_crystal import synthesize_community_crystals

    cluster_ids = {s.current_cluster_id for s in scheduled if s.current_cluster_id}
    if not cluster_ids:
        return {
            "evaluated": len(stale), "scheduled": len(scheduled),
            "synthesized": 0, "skipped": len(scheduled),
            "signal_breakdown": signal_breakdown,
            "concepts": [s.concept_id for s in scheduled],
        }
    llm = get_litellm_client(vault_dir=vault_dir)
    crystals = synthesize_community_crystals(
        vault_dir=vault_dir,
        llm_client=llm,
        db_path=layout.knowledge_db,
        pack_name=pack,
        only_cluster_ids=cluster_ids,
        skip_existing=False,
    )
    synthesized = len(crystals)
    skipped = len(scheduled) - synthesized

    for s in scheduled:
        _emit(vault_dir, "crystal_resynthesized", {
            "pack": pack,
            "concept_id": s.concept_id,
            "cluster_id": s.current_cluster_id,
            "primary_signal": s.primary_signal,
            "signals": list(s.signals),
            "jaccard": s.jaccard,
        }, pack=pack)

    return {
        "evaluated": len(stale),
        "scheduled": len(scheduled),
        "synthesized": synthesized,
        "skipped": skipped,
        "signal_breakdown": signal_breakdown,
        "concepts": [s.concept_id for s in scheduled],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Re-synthesize stale community crystals — bounded by "
            "--max so a nightly run never exceeds a known LLM budget."
        ),
    )
    parser.add_argument("--vault-dir", type=Path, required=True)
    parser.add_argument("--pack", default="research-tech")
    parser.add_argument(
        "--max", type=int, default=DEFAULT_BUDGET,
        help=(
            f"Maximum concepts to re-synthesize this run "
            f"(default {DEFAULT_BUDGET} ≈ $1/day at current pricing)."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report the stale set without calling the LLM.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Print the summary as a single JSON object on stdout.",
    )
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    summary = resynth_stale_crystals(
        vault_dir=vault_dir, pack=args.pack,
        budget=args.max, dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(summary, default=str), flush=True)
    else:
        print(
            f"evaluated={summary['evaluated']} "
            f"scheduled={summary['scheduled']} "
            f"synthesized={summary['synthesized']} "
            f"skipped={summary['skipped']} "
            f"signals={summary['signal_breakdown']}"
        )

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
