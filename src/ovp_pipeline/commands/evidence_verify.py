"""
ovp-evidence — Phase 33 verifier for ``claim_evidence`` (and ``relations``).

Subcommands:

* ``verify``   Re-hash recently-touched rows and update ``status`` /
               ``verified_at`` in ``60-Logs/knowledge.db``. Emits a
               ``evidence_reverified`` audit event so reuse-event trustedness
               can recompute on the next index rebuild.
* ``backfill`` Idempotently fill ``content_hash`` / ``locator`` /
               ``retrieval_context`` for any row missing them, then verify.

Both subcommands write into the canonical knowledge.db. Counts are reported
both per-pack and per-status so the doctor's Evidence Health panel and the
CI lint can read the same numbers.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from ..evidence import (
    compute_content_hash,
    compute_locator,
    compute_retrieval_context,
    verify_evidence_row,
)
from ..event_emitter import emit
from ..evidence_replay import emit_evidence_verified
from ..knowledge_index import ensure_knowledge_db_current
from ..packs.loader import DEFAULT_WORKFLOW_PACK_NAME
from ..runtime import resolve_vault_dir
from ..truth_store import (
    EVIDENCE_STATUS_BROKEN,
    EVIDENCE_STATUS_STALE,
    EVIDENCE_STATUS_UNVERIFIED,
    EVIDENCE_STATUS_VERIFIED,
)


_TARGET_TABLES = ("claim_evidence", "relations")
# The ``relations`` table has no unique constraint on the (pack, source, target,
# type) tuple — multiple rows can carry the same triple but different
# ``evidence_source_slug`` values when the relation is independently attested
# in two deep dives. Including ``evidence_source_slug`` in the WHERE keeps each
# UPDATE scoped to the row that was actually verified; otherwise one slug's
# verification would silently overwrite another's metadata.
_TABLE_KEY_COLUMNS: dict[str, tuple[str, ...]] = {
    "claim_evidence": ("pack", "claim_id", "source_slug", "evidence_kind"),
    "relations": (
        "pack",
        "source_object_id",
        "target_object_id",
        "relation_type",
        "evidence_source_slug",
    ),
}


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_dict(table: str, row: tuple[Any, ...]) -> dict[str, Any]:
    if table == "claim_evidence":
        columns = (
            "pack",
            "claim_id",
            "source_slug",
            "evidence_kind",
            "quote_text",
            "locator",
            "content_hash",
            "retrieval_context",
            "status",
            "verified_at",
        )
    else:
        columns = (
            "pack",
            "source_object_id",
            "target_object_id",
            "relation_type",
            "evidence_source_slug",
            "quote_text",
            "locator",
            "content_hash",
            "retrieval_context",
            "status",
            "verified_at",
        )
    return dict(zip(columns, row))


def _select_rows(
    conn: sqlite3.Connection,
    table: str,
    *,
    pack: str | None,
    cutoff_text: str | None,
) -> list[tuple[Any, ...]]:
    where: list[str] = []
    params: list[Any] = []
    if pack:
        where.append("pack = ?")
        params.append(pack)
    if cutoff_text:
        where.append("(verified_at = '' OR verified_at < ?)")
        params.append(cutoff_text)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    columns = (
        "pack, claim_id, source_slug, evidence_kind, quote_text, "
        "locator, content_hash, retrieval_context, status, verified_at"
        if table == "claim_evidence"
        else "pack, source_object_id, target_object_id, relation_type, evidence_source_slug, "
        "quote_text, locator, content_hash, retrieval_context, status, verified_at"
    )
    return list(conn.execute(f"SELECT {columns} FROM {table} {where_sql}", params).fetchall())


def _key_clause(table: str) -> str:
    return " AND ".join(f"{column} = ?" for column in _TABLE_KEY_COLUMNS[table])


def _key_values(table: str, row_dict: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row_dict[col] for col in _TABLE_KEY_COLUMNS[table])


def _quote_text_for(table: str, row_dict: dict[str, Any]) -> str:
    return str(row_dict.get("quote_text") or "")


def _source_path_for(table: str, row_dict: dict[str, Any]) -> str:
    if table == "claim_evidence":
        return str(row_dict.get("source_slug") or "")
    return str(row_dict.get("evidence_source_slug") or "")


def _empty_summary() -> dict[str, int]:
    return {
        EVIDENCE_STATUS_VERIFIED: 0,
        EVIDENCE_STATUS_STALE: 0,
        EVIDENCE_STATUS_BROKEN: 0,
        EVIDENCE_STATUS_UNVERIFIED: 0,
    }


def _load_slug_to_path(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """Map ``(pack, object_id) → canonical_path`` for slug→path resolution."""
    try:
        rows = conn.execute(
            "SELECT pack, object_id, canonical_path FROM objects"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {
        (str(pack or ""), str(object_id or "")): str(canonical_path or "")
        for pack, object_id, canonical_path in rows
        if canonical_path
    }


def _resolve_evidence_path(
    table: str,
    row_dict: dict[str, Any],
    raw_source: str,
    slug_to_path: dict[tuple[str, str], str],
) -> str:
    """Resolve ``source_slug`` (claim_evidence) or ``evidence_source_slug``
    (relations) to the owning object's ``canonical_path`` when known."""
    if not raw_source:
        return ""
    pack = str(row_dict.get("pack") or "")
    canonical = slug_to_path.get((pack, raw_source))
    return canonical or raw_source


def _process_table(
    conn: sqlite3.Connection,
    table: str,
    *,
    vault_dir: Path,
    pack: str | None,
    cutoff_text: str | None,
    backfill: bool,
    slug_to_path: dict[tuple[str, str], str],
) -> dict[str, Any]:
    rows = _select_rows(conn, table, pack=pack, cutoff_text=cutoff_text)
    summary: dict[str, int] = _empty_summary()
    examined = 0
    backfilled = 0

    for row in rows:
        row_dict = _row_dict(table, row)
        quote = _quote_text_for(table, row_dict)
        raw_source = _source_path_for(table, row_dict)
        source_path = _resolve_evidence_path(table, row_dict, raw_source, slug_to_path)

        new_locator = row_dict.get("locator") or ""
        new_content_hash = row_dict.get("content_hash") or ""
        new_context = row_dict.get("retrieval_context") or ""
        if backfill:
            if not new_content_hash and source_path:
                new_content_hash = compute_content_hash(source_path, vault_dir=vault_dir)
            if not new_locator and quote and source_path:
                new_locator = compute_locator(source_path, quote, vault_dir=vault_dir)
            if not new_context and quote and source_path:
                new_context = compute_retrieval_context(source_path, quote, vault_dir=vault_dir)
            if (
                new_locator != (row_dict.get("locator") or "")
                or new_content_hash != (row_dict.get("content_hash") or "")
                or new_context != (row_dict.get("retrieval_context") or "")
            ):
                backfilled += 1

        verify_input = {
            **row_dict,
            "content_hash": new_content_hash,
            "source_slug": source_path,
            "quote_text": quote,
        }
        status, verified_at = verify_evidence_row(verify_input, vault_dir)

        examined += 1
        summary[status] = summary.get(status, 0) + 1

        conn.execute(
            f"""
            UPDATE {table}
               SET locator = ?,
                   content_hash = ?,
                   retrieval_context = ?,
                   status = ?,
                   verified_at = ?
             WHERE {_key_clause(table)}
            """,
            (
                new_locator,
                new_content_hash,
                new_context,
                status,
                verified_at,
                *_key_values(table, row_dict),
            ),
        )

        # Phase 33 durability: also emit a JSONL event so the next
        # rebuild_knowledge_index can re-apply this verification after the
        # projection re-inserts the row in its 'unverified' default state.
        emit_evidence_verified(
            vault_dir,
            table=table,
            key={col: row_dict.get(col) for col in _TABLE_KEY_COLUMNS[table]},
            locator=new_locator,
            content_hash=new_content_hash,
            retrieval_context=new_context,
            status=status,
            verified_at=verified_at,
            pack=str(row_dict.get("pack") or ""),
        )

    return {
        "table": table,
        "examined": examined,
        "backfilled": backfilled,
        "by_status": summary,
    }


def _emit_audit(
    vault_dir: Path,
    *,
    pack: str,
    summaries: Iterable[dict[str, Any]],
) -> None:
    """Record a single ``evidence_reverified`` event so reuse_event trustedness
    can recompute on the next ``ovp-knowledge-index`` rebuild."""
    payload = {
        "tables": list(summaries),
    }
    emit(
        vault_dir,
        "pipeline.jsonl",
        "evidence_reverified",
        payload,
        pack=pack,
    )


def _run_verify(args: argparse.Namespace, *, backfill: bool = False) -> int:
    vault_dir = resolve_vault_dir(args.vault_dir)
    db_path = ensure_knowledge_db_current(vault_dir)
    cutoff_text: str | None = None
    if args.recent and args.recent > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.recent)
        cutoff_text = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    summaries: list[dict[str, Any]] = []
    with sqlite3.connect(db_path) as conn:
        slug_to_path = _load_slug_to_path(conn)
        for table in _TARGET_TABLES:
            summary = _process_table(
                conn,
                table,
                vault_dir=vault_dir,
                pack=args.pack,
                cutoff_text=cutoff_text,
                backfill=backfill,
                slug_to_path=slug_to_path,
            )
            summaries.append(summary)
        conn.commit()

    _emit_audit(vault_dir, pack=args.pack or DEFAULT_WORKFLOW_PACK_NAME, summaries=summaries)

    payload = {
        "vault_dir": str(vault_dir),
        "pack": args.pack,
        "ts": _utc_now_text(),
        "recent_days": args.recent,
        "summaries": summaries,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for summary in summaries:
            by_status = summary["by_status"]
            print(
                f"{summary['table']}: examined={summary['examined']} "
                f"backfilled={summary['backfilled']} "
                f"verified={by_status[EVIDENCE_STATUS_VERIFIED]} "
                f"stale={by_status[EVIDENCE_STATUS_STALE]} "
                f"broken={by_status[EVIDENCE_STATUS_BROKEN]} "
                f"unverified={by_status[EVIDENCE_STATUS_UNVERIFIED]}"
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 33 evidence verifier")
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify", help="Re-hash existing evidence rows")
    verify.add_argument("--vault-dir", type=Path, default=None)
    verify.add_argument(
        "--recent",
        type=int,
        default=30,
        help="Only re-verify rows whose verified_at is empty or older than N days (0 = all)",
    )
    verify.add_argument("--pack", default=None, help="Restrict to a single pack")
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(func=lambda args: _run_verify(args, backfill=False))

    backfill = sub.add_parser(
        "backfill",
        help="Fill content_hash/locator/retrieval_context, then verify",
    )
    backfill.add_argument("--vault-dir", type=Path, default=None)
    backfill.add_argument(
        "--recent",
        type=int,
        default=0,
        help="Only touch rows whose verified_at is empty or older than N days (0 = all)",
    )
    backfill.add_argument("--pack", default=None)
    backfill.add_argument("--json", action="store_true")
    backfill.set_defaults(func=lambda args: _run_verify(args, backfill=True))

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
