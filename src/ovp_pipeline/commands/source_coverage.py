"""ovp-source-coverage — discovery dashboard for source authority.

Surfaces the long tail of unrecognized hosts + authors so the user
can decide which to add to ``domain_overrides.yaml`` /
``author_overrides.yaml`` / ``authors.jsonl`` (or score them via
``ovp-score-domain``).

Three sections in the report:

  1. **Authority distribution** — how many sources fall into each
     bucket (high / mid / low / default).  Tells you whether the
     scorer is calibrated.

  2. **Unrecognized hosts ranked by impact** — hosts that all scored
     at the 0.45 default, ranked by ``# sources × # atomic units``.
     The top entries are where review time pays off most.

  3. **Unrecognized X.com handles** — Twitter handles that appeared
     in source URLs but aren't in the authors list.  Quick path to
     extending the author whitelist.

Output formats:
  * Default — human-readable with three sections + suggested actions
  * ``--json`` — machine-readable for piping to other tools
  * ``--triage`` — emit a YAML stub the user can paste into
    ``domain_overrides.yaml`` after editing ``authority`` values
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..source_signals.url_utils import extract_x_handle, normalize_host


# Number of sample URLs we keep per host for the ``ovp-score-domain``
# follow-up workflow.  Three is the LLM-prompt sweet spot — enough
# for the model to reason about content variance, few enough to
# avoid token bloat.
_MAX_SAMPLE_URLS_PER_HOST = 3


@dataclass
class HostStats:
    host: str
    source_count: int = 0
    authoritative_count: int = 0   # sources with non-default score
    avg_authority: float = 0.45
    sample_urls: list[str] = field(default_factory=list)
    sample_titles: list[str] = field(default_factory=list)


def _query_source_authorities(db_path: Path) -> list[tuple[str, float]]:
    """Pull (source_id, authority) for every scored source.

    Returns ``[]`` if the table doesn't exist yet (PR-D1 not run).
    """
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        try:
            rows = conn.execute(
                "SELECT source_id, authority FROM source_authority"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return rows
    finally:
        conn.close()


def _query_unit_count_per_source(db_path: Path) -> dict[str, int]:
    """Count atomic units (evergreens + entity_mentions) per source slug.

    Used to weight host impact: a host that produced 100 evergreens
    matters more than one that produced 2.  Best-effort — returns ``{}``
    if KG hasn't been built.
    """
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    try:
        try:
            # SQLite doesn't allow column aliases in WHERE/GROUP BY —
            # repeat the json_extract expression where needed.
            rows = conn.execute(
                "SELECT json_extract(frontmatter_json, '$.source_url') AS source, "
                "COUNT(*) FROM pages_index "
                "WHERE note_type = 'evergreen' "
                "AND json_extract(frontmatter_json, '$.source_url') IS NOT NULL "
                "GROUP BY json_extract(frontmatter_json, '$.source_url')"
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
        return {url: count for url, count in rows if url}
    finally:
        conn.close()


def collect_host_stats(
    vault_dir: Path,
) -> tuple[list[HostStats], dict[str, int]]:
    """Aggregate per-host statistics from knowledge.db + source_authority.

    Returns
    -------
    (host_stats_sorted_by_impact, bucket_counts)
        ``host_stats`` is descending by ``source_count * (1 - avg_authority)``
        (the impact score — lots of sources at default authority = top
        priority for triage).  ``bucket_counts`` summarizes the
        authority distribution.
    """
    db_path = vault_dir / "60-Logs" / "knowledge.db"
    authorities = _query_source_authorities(db_path)
    units_per_source = _query_unit_count_per_source(db_path)

    by_host: dict[str, HostStats] = {}
    units_by_host: dict[str, int] = defaultdict(int)
    bucket_counts = {"high": 0, "mid": 0, "low": 0, "default": 0}

    for source_id, authority in authorities:
        host = normalize_host(source_id)
        if not host:
            continue
        stats = by_host.setdefault(host, HostStats(host=host))
        stats.source_count += 1
        if abs(authority - 0.45) > 0.001:
            stats.authoritative_count += 1
        # accumulate avg
        stats.avg_authority = (
            (stats.avg_authority * (stats.source_count - 1) + authority)
            / stats.source_count
        )
        if len(stats.sample_urls) < _MAX_SAMPLE_URLS_PER_HOST:
            stats.sample_urls.append(source_id)
        # accumulate atomic-unit weight per host (best-effort; 0 if KG missing)
        units_by_host[host] += units_per_source.get(source_id, 0)
        # bucket
        if authority >= 0.75:
            bucket_counts["high"] += 1
        elif authority >= 0.55:
            bucket_counts["mid"] += 1
        elif authority < 0.45:
            bucket_counts["low"] += 1
        else:
            bucket_counts["default"] += 1

    # Impact score = source_count × (units || 1) × (1 - avg_authority).
    # The unit-count factor weights hosts that produced lots of evergreens
    # over hosts that produced 1-2 — when the KG isn't built it falls back
    # to source_count alone.
    def _impact(s: HostStats) -> float:
        weight = max(units_by_host.get(s.host, 0), 1)
        return s.source_count * weight * (1.0 - s.avg_authority)

    sorted_stats = sorted(by_host.values(), key=_impact, reverse=True)
    return sorted_stats, bucket_counts


def collect_unrecognized_x_handles(
    vault_dir: Path,
    *,
    known_authors: set[str],
    entity_resolved: set[str] | None = None,
) -> list[tuple[str, int]]:
    """List X handles in source URLs that aren't already curated.

    Curation has two layers (PR-E3+):
      * ``known_authors`` — explicit whitelist (``authors.jsonl`` +
        ``author_overrides.yaml``).  Wins by definition.
      * ``entity_resolved`` — handles that the entity table can map
        to a person/organization/twitter_author with non-None
        derived_authority.  When passed, those handles are treated
        as "already known" too, so the dashboard surfaces only
        truly-unresolved long-tail handles.

    Pass ``entity_resolved=None`` to keep the pre-PR-F1 behavior
    (only the curated whitelist counts as known).

    Returns ``[(handle, count_of_sources)]`` descending by count.
    """
    db_path = vault_dir / "60-Logs" / "knowledge.db"
    authorities = _query_source_authorities(db_path)
    handle_counts: dict[str, int] = defaultdict(int)
    resolved = entity_resolved or set()
    for source_id, _ in authorities:
        handle = extract_x_handle(source_id)
        if handle is None:
            continue
        if handle in known_authors or handle in resolved:
            continue
        handle_counts[handle] += 1
    return sorted(handle_counts.items(), key=lambda kv: -kv[1])


def _load_entity_resolved_handles(vault_dir: Path) -> set[str]:
    """Return the set of X handles that the entity table can resolve
    to a non-None ``derived_authority`` (twitter_author, person, or
    organization).  These handles aren't on the curated whitelist
    but ARE recognized by the runtime resolver — so flagging them as
    "unknown" in the discovery dashboard is misleading.
    """
    db_path = vault_dir / "60-Logs" / "knowledge.db"
    if not db_path.exists():
        return set()
    # Lazy import — avoids pulling entities/* into source_coverage's
    # import graph for users who never wired the entity layer.
    from ..entities.store import EntityStore

    store = EntityStore(db_path=db_path)
    out: set[str] = set()
    for entity_type in ("twitter_author", "person", "organization"):
        for e in store.list_by_type(entity_type):
            if e.derived_authority is None:
                continue
            out.add(e.identity_key.lower())
    return out


def _load_known_authors(vault_dir: Path) -> set[str]:
    """Union handles + aliases from authors.jsonl AND author_overrides.yaml.

    Both surfaces feed AuthorRulesProvider at runtime (yaml wins on
    handle collision), so the discovery dashboard must consider both
    when filtering "unrecognized" handles — otherwise a handle the user
    already curated via yaml shows up as unknown noise.
    """
    out: set[str] = set()

    authors_jsonl = vault_dir / "60-Logs" / "authors.jsonl"
    if authors_jsonl.exists():
        for line in authors_jsonl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            handle = (rec.get("handle") or "").lower().lstrip("@")
            if handle:
                out.add(handle)
            for alias in (rec.get("aliases") or []):
                if isinstance(alias, str):
                    out.add(alias.lower().lstrip("@"))

    # Merge in YAML-curated handles via the canonical loader, which
    # already does normalization + clamping + warning on bad rows.
    yaml_path = vault_dir / "60-Logs" / "author_overrides.yaml"
    if yaml_path.exists():
        from ..source_signals.overrides import AuthorOverrides
        for rec in AuthorOverrides.load(yaml_path).authors:
            handle = rec.get("handle", "")
            if handle:
                out.add(handle)
            for alias in rec.get("aliases", []):
                if isinstance(alias, str) and alias:
                    out.add(alias)

    return out


def emit_triage_yaml(stats: list[HostStats], top_n: int) -> str:
    """Render top-N unrecognized hosts as a YAML stub for paste-into-overrides."""
    lines = [
        "# Triage stub — review each entry's `authority` and `bucket`,",
        "# delete the ones you don't want, then paste into",
        "# 60-Logs/domain_overrides.yaml under `domains:`.",
        "domains:",
    ]
    for s in stats[:top_n]:
        sample = s.sample_urls[0] if s.sample_urls else ""
        lines.append(f"  {s.host}:")
        lines.append(f"    authority: 0.55  # TODO: review (sample: {sample})")
        lines.append("    bucket: mixed    # TODO: canonical | mixed | low")
        lines.append("    rationale: \"\"     # TODO: 1-line reason")
        lines.append("    source: triage")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Discovery dashboard for unrecognized sources + authors",
    )
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument("--top", type=int, default=20,
                        help="How many top-impact hosts to surface (default 20)")
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON")
    parser.add_argument("--triage", action="store_true",
                        help="Emit a YAML triage stub for the top-N hosts")
    parser.add_argument("--default-only", action="store_true",
                        help="Only show hosts whose sources all scored at default")
    args = parser.parse_args(argv)

    vault = args.vault_dir.resolve()
    if not vault.is_dir():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 2

    host_stats, bucket_counts = collect_host_stats(vault)

    if args.default_only:
        host_stats = [s for s in host_stats if s.authoritative_count == 0]

    known_authors = _load_known_authors(vault)
    # Treat entity-resolved handles as "already curated" — they're
    # picked up at runtime by the AuthorRulesProvider entity-fallback
    # (PR-E3) and therefore aren't actually unrecognized.
    entity_resolved = _load_entity_resolved_handles(vault)
    unknown_handles = collect_unrecognized_x_handles(
        vault,
        known_authors=known_authors,
        entity_resolved=entity_resolved,
    )

    if args.triage:
        print(emit_triage_yaml(host_stats, args.top))
        return 0

    if args.json:
        print(json.dumps({
            "authority_distribution": bucket_counts,
            "top_hosts": [
                {
                    "host": s.host,
                    "source_count": s.source_count,
                    "avg_authority": round(s.avg_authority, 3),
                    "sample_urls": s.sample_urls,
                }
                for s in host_stats[:args.top]
            ],
            "unknown_x_handles": [
                {"handle": h, "count": c}
                for h, c in unknown_handles[:args.top]
            ],
        }, ensure_ascii=False, indent=2))
        return 0

    total = sum(bucket_counts.values()) or 1
    print(f"=== Authority distribution ({total} sources) ===")
    print(f"  high     (≥0.75): {bucket_counts['high']:>5}  "
          f"({bucket_counts['high']*100//total}%)")
    print(f"  mid      (≥0.55): {bucket_counts['mid']:>5}  "
          f"({bucket_counts['mid']*100//total}%)")
    print(f"  default  (=0.45): {bucket_counts['default']:>5}  "
          f"({bucket_counts['default']*100//total}%)  ← triage candidates")
    print(f"  low      (<0.45): {bucket_counts['low']:>5}  "
          f"({bucket_counts['low']*100//total}%)")

    print(f"\n=== Top {min(args.top, len(host_stats))} unrecognized hosts (by impact) ===")
    for s in host_stats[:args.top]:
        sample = s.sample_urls[0] if s.sample_urls else ""
        print(f"  {s.host:<35} {s.source_count:>3} sources  "
              f"avg={s.avg_authority:.2f}  e.g. {sample[:60]}")

    if unknown_handles:
        print(f"\n=== Top {min(args.top, len(unknown_handles))} unrecognized X handles ===")
        for handle, count in unknown_handles[:args.top]:
            print(f"  @{handle:<30} {count:>3} sources")

    print("\n=== Suggested actions ===")
    print("  ovp-score-domain <host>       # one-shot LLM-assisted scoring")
    print("  edit 60-Logs/domain_overrides.yaml  # manual entry per host")
    print("  edit 60-Logs/authors.jsonl    # add unrecognized X handle")
    print("  ovp-source-coverage --triage --top 20 > /tmp/triage.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
