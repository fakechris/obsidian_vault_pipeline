"""ovp-score-sources — batch-score every source in the vault.

Walks ``50-Inbox/03-Processed/**/*.md`` (the canonical source layer),
extracts each file's frontmatter, runs the source_authority
orchestrator, and persists results to:

  * ``60-Logs/knowledge.db`` (table ``source_authority``)
  * ``60-Logs/source_authority.jsonl`` (append-only audit)

Usage::

    ovp-score-sources --vault-dir ~/Documents/ovp-vault          # full scan
    ovp-score-sources --vault-dir ... --since 2026-04-01         # incremental
    ovp-score-sources --vault-dir ... --domains-only             # T1 only
                                                                 # (no GitHub
                                                                 # API hits)
    ovp-score-sources --vault-dir ... --json                     # machine-
                                                                 # readable

By default scores ALL sources (deterministic providers don't make
network calls so this is cheap); ``--domains-only`` skips GitHub /
arXiv API providers if you want fully offline runs.

Soft signal: scores are stored but never gate any pipeline step.
Downstream tools (``ovp-query``, UI) read them for filter/sort.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from dataclasses import asdict

from ..layer_schemas import parse_frontmatter
from ..source_authority import (
    AuthorityScore,
    append_audit,
    default_providers,
    ensure_schema,
    score_source,
    upsert_score,
)


def _audit_line(score: AuthorityScore) -> str:
    """Serialize one ``AuthorityScore`` to a JSONL line (with trailing \\n).

    Lifted out of ``source_authority.append_audit`` so the batch CLI
    can hold the file handle open for the whole run instead of paying
    open/close per record.
    """
    record = {
        "source_id": score.source_id,
        "authority": score.authority,
        "signals": [asdict(s) for s in score.signals],
        "scored_at": score.scored_at,
        "scorer_version": score.scorer_version,
    }
    return json.dumps(record, ensure_ascii=False) + "\n"
from ..source_signals import (
    ArxivSignalProvider,
    AuthorRulesProvider,
    DomainRulesProvider,
    GitHubSignalProvider,
    SignalProvider,
    SubstackSignalProvider,
    TwitterSignalProvider,
)


def _build_providers(vault_dir: Path, *, domains_only: bool) -> list[SignalProvider]:
    if domains_only:
        return [
            DomainRulesProvider(),
            AuthorRulesProvider(authors_path=vault_dir / "60-Logs" / "authors.jsonl"),
        ]
    return default_providers(vault_dir)


def _iter_sources(vault_dir: Path, *, since: datetime | None):
    """Stream sources lazily — caller may break out as soon as it has
    enough (e.g. ``--limit``) without paying for a full FS walk.

    Note: ``rglob`` itself returns paths in arbitrary order; that's
    fine for the limit case (any N sources is a valid sample).
    """
    for f in (vault_dir / "50-Inbox" / "03-Processed").rglob("*.md"):
        if not f.is_file():
            continue
        if since is not None:
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
            except OSError:
                continue
            if mtime < since:
                continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = parse_frontmatter(text) or {}
        url = fm.get("source") or fm.get("source_url") or ""
        if not isinstance(url, str):
            url = ""
        yield (url, fm, f)


def _non_negative_int(raw: str) -> int:
    """Argparse type for flags where negative values would be silently
    surprising (e.g. ``--limit -1`` would currently terminate after
    zero records, contradicting the documented ``0 = all``).
    """
    value = int(raw)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {value!r}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score every source's authority and persist to knowledge.db",
    )
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument(
        "--since",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d"),
        help="Only score files modified after YYYY-MM-DD (incremental)",
    )
    parser.add_argument(
        "--domains-only", action="store_true",
        help="Skip network providers (GitHub / arXiv); offline-safe",
    )
    parser.add_argument(
        "--limit", type=_non_negative_int, default=0,
        help="Stop after N sources (0 = all). Useful for testing.",
    )
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON summary instead of human report")
    args = parser.parse_args(argv)

    vault = args.vault_dir.resolve()
    if not vault.is_dir():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 2

    providers = _build_providers(vault, domains_only=args.domains_only)
    db_path = vault / "60-Logs" / "knowledge.db"
    audit_path = vault / "60-Logs" / "source_authority.jsonl"

    db_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    # Hold the audit log open for the duration of the batch — opening
    # in append mode per record is ~2-3× slower at scale (444 sources
    # measured at 0.4s vs 1.1s).
    audit_fh = audit_path.open("a", encoding="utf-8")
    try:
        ensure_schema(conn)
        scored_count = 0
        skipped_count = 0
        per_bucket = {"high": 0, "mid": 0, "low": 0}
        for url, fm, _path in _iter_sources(vault, since=args.since):
            score = score_source(url, fm, providers=providers)
            if not score.source_id:
                skipped_count += 1
                continue
            upsert_score(conn, score)
            audit_fh.write(_audit_line(score))
            scored_count += 1
            if score.authority >= 0.75:
                per_bucket["high"] += 1
            elif score.authority >= 0.55:
                per_bucket["mid"] += 1
            else:
                per_bucket["low"] += 1
            if args.limit and scored_count >= args.limit:
                break
        conn.commit()
        audit_fh.flush()
    finally:
        audit_fh.close()
        conn.close()

    if args.json:
        print(json.dumps({
            "scored": scored_count,
            "skipped_no_url": skipped_count,
            "by_authority": per_bucket,
            "providers": [p.name for p in providers],
        }, ensure_ascii=False, indent=2))
    else:
        print(f"Sources scored: {scored_count} (skipped {skipped_count} without URL)")
        print(f"  high (≥0.75): {per_bucket['high']}")
        print(f"  mid  (≥0.55): {per_bucket['mid']}")
        print(f"  low  (< 0.55): {per_bucket['low']}")
        print(f"\nProviders: {', '.join(p.name for p in providers)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
