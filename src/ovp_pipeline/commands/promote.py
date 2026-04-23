"""ovp-promote — Phase 34 promotion CLI.

Subcommands:

* ``run``       Re-run concept policy across all current candidates and report
                lane assignments. Auto-lane candidates are promoted; escalate
                lane lands in the review queue; reject lane is archived.
* ``workspace`` Promote a single agent-owned draft to an accepted-state file.
                Wraps :func:`workspace_promotion.promote` and emits the
                matching ``promotion`` audit event so the lint mtime check
                stays silent.

Both surfaces share :class:`promotion_policy.PolicyDecision` so the audit
records the same shape downstream tooling will consume.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..concept_registry import ConceptRegistry
from ..packs.loader import DEFAULT_WORKFLOW_PACK_NAME, load_pack
from ..promote_candidates import review_candidates
from ..promotion_audit import emit_promotion
from ..promotion_policy import (
    LANE_AUTO,
    LANE_ESCALATE,
    LANE_HOLD,
    LANE_REJECT,
    collect_pack_signals,
    evaluate_concept,
    evaluate_workspace,
)
from ..relation_promotion import promote_review_queue
from ..runtime import VaultLayout, resolve_vault_dir
from ..state_lifecycle import State
from ..workspace_promotion import promote as workspace_promote


def _run_concept(args: argparse.Namespace) -> int:
    vault_dir = resolve_vault_dir(args.vault_dir)
    pack = load_pack(args.pack or DEFAULT_WORKFLOW_PACK_NAME)
    registry = ConceptRegistry(vault_dir).load()
    layout = VaultLayout.from_vault(vault_dir)
    kinds_by_id, disputed_ids = collect_pack_signals(
        layout.knowledge_db, pack_name=pack.name
    )

    by_lane: dict[str, list[dict[str, Any]]] = {
        LANE_AUTO: [],
        LANE_ESCALATE: [],
        LANE_HOLD: [],
        LANE_REJECT: [],
    }
    for entry in registry.candidates:
        decision = evaluate_concept(
            entry,
            pack=pack,
            registry=registry,
            evidence_kinds=kinds_by_id.get(entry.slug, frozenset()),
            has_open_contradiction=entry.slug in disputed_ids,
        )
        by_lane.setdefault(decision.lane, []).append(
            {
                "slug": entry.slug,
                "title": entry.title,
                "reason_code": decision.reason_code,
                "blocking_facts": list(decision.blocking_facts),
            }
        )

    payload = {
        "vault_dir": str(vault_dir),
        "pack": pack.name,
        "lanes": {lane: rows for lane, rows in by_lane.items()},
        "totals": {lane: len(rows) for lane, rows in by_lane.items()},
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for lane, rows in by_lane.items():
            print(f"{lane}: {len(rows)}")
            for row in rows[:5]:
                print(f"  - {row['slug']} ({row['reason_code']})")
    return 0


def _run_workspace(args: argparse.Namespace) -> int:
    vault_dir = resolve_vault_dir(args.vault_dir)
    pack = load_pack(args.pack or DEFAULT_WORKFLOW_PACK_NAME)
    draft = Path(args.from_path).expanduser()
    target = Path(args.to_path).expanduser()
    if not draft.is_absolute():
        draft = (vault_dir / draft).resolve()
    if not target.is_absolute():
        target = (vault_dir / target).resolve()

    decision = evaluate_workspace(draft, target, pack=pack, target_state=State.ACCEPTED)
    if decision.lane != LANE_AUTO:
        print(json.dumps({
            "ok": False,
            "decision": {
                "lane": decision.lane,
                "reason_code": decision.reason_code,
                "blocking_facts": list(decision.blocking_facts),
            },
        }, ensure_ascii=False, indent=2))
        return 1

    if args.diff:
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        proposed = draft.read_text(encoding="utf-8")
        print("--- existing\n+++ proposed")
        print(f"-- {len(existing)} bytes\n++ {len(proposed)} bytes")

    if args.dry_run:
        print(json.dumps({
            "ok": True,
            "dry_run": True,
            "draft": str(draft),
            "target": str(target),
        }, ensure_ascii=False, indent=2))
        return 0

    if not args.auto:
        print(
            "Refusing to write without --auto (run with --diff first to inspect, "
            "then --auto to commit)."
        )
        return 1

    record = workspace_promote(
        draft,
        target,
        approver=args.approver or "cli",
        pack=pack,
        vault_dir=vault_dir,
    )
    emit_promotion(
        vault_dir,
        pack=pack.name,
        from_state=State.DRAFT,
        to_state=State.ACCEPTED,
        target_path=target,
        actor=f"ovp-promote workspace ({args.approver or 'cli'})",
        payload={"draft": str(draft), "bytes_written": record.bytes_written},
    )
    print(json.dumps({
        "ok": True,
        "target": str(target),
        "bytes_written": record.bytes_written,
    }, ensure_ascii=False, indent=2))
    return 0


def _run_relations(args: argparse.Namespace) -> int:
    vault_dir = resolve_vault_dir(args.vault_dir)
    pack = load_pack(args.pack or DEFAULT_WORKFLOW_PACK_NAME)
    layout = VaultLayout.from_vault(vault_dir)
    report = promote_review_queue(layout, pack=pack)
    payload = {
        "vault_dir": str(vault_dir),
        "pack": pack.name,
        "lane_counts": report.lane_counts(),
        "promoted": [
            {
                "relation_type": c.relation_type,
                "source_object_id": c.source_object_id,
                "target_object_id": c.target_object_id,
                "source_slug": c.source_slug,
            }
            for c in report.promoted
        ],
        "escalated": [
            {
                "relation_type": c.relation_type,
                "source_object_id": c.source_object_id,
                "target_object_id": c.target_object_id,
                "facts": list(facts),
            }
            for c, facts in report.escalated
        ],
        "rejected": [
            {
                "relation_type": c.relation_type,
                "source_object_id": c.source_object_id,
                "target_object_id": c.target_object_id,
                "facts": list(facts),
            }
            for c, facts in report.rejected
        ],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for lane, count in report.lane_counts().items():
            print(f"{lane}: {count}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 34 promotion CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Re-evaluate concept candidates against pack policy")
    run.add_argument("--vault-dir", type=Path, default=None)
    run.add_argument("--pack", default=None)
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=_run_concept)

    workspace = sub.add_parser(
        "workspace",
        help="Promote a single agent-owned draft to an accepted-state file",
    )
    workspace.add_argument("--vault-dir", type=Path, default=None)
    workspace.add_argument("--pack", default=None)
    workspace.add_argument("--from", dest="from_path", required=True)
    workspace.add_argument("--to", dest="to_path", required=True)
    workspace.add_argument("--diff", action="store_true")
    workspace.add_argument("--auto", action="store_true", help="Commit the write (off = preview only)")
    workspace.add_argument("--dry-run", action="store_true")
    workspace.add_argument("--approver", default="cli")
    workspace.set_defaults(func=_run_workspace)

    relations = sub.add_parser(
        "relations",
        help="Promote semantic_relation_candidate JSON files into the relations table",
    )
    relations.add_argument("--vault-dir", type=Path, default=None)
    relations.add_argument("--pack", default=None)
    relations.add_argument("--json", action="store_true")
    relations.set_defaults(func=_run_relations)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
