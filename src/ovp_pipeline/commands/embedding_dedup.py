"""ovp-embedding-dedup — Detect paraphrastic clones via embedding cosine similarity.

Uses the ``page_embeddings`` table in ``knowledge.db`` to find semantically
similar Evergreen notes that slug-level trigram dedup cannot catch (e.g.
``pattern-composability`` vs ``agent-skill-patterns-are-composable``).

Workflow:
  1. Read all page embeddings from knowledge.db
  2. Aggregate per-slug to a single page-level vector (mean of chunk vectors)
  3. Compute pairwise cosine similarity for all slug pairs
  4. Report clusters above threshold
  5. Optionally generate a dedup proposal compatible with concept_dedup.apply_proposal
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from array import array
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EmbeddingMatch:
    slug_a: str
    slug_b: str
    cosine_sim: float
    title_a: str
    title_b: str


def _decode_embedding(blob: bytes) -> list[float]:
    decoded = array("f")
    decoded.frombytes(blob)
    return list(decoded)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _mean_vector(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    dim = len(vectors[0])
    result = [0.0] * dim
    for v in vectors:
        for i in range(dim):
            result[i] += v[i]
    n = len(vectors)
    return [x / n for x in result]


def _load_page_vectors(db_path: Path) -> dict[str, list[float]]:
    """Load embeddings from knowledge.db, aggregate chunks per slug."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT slug, embedding_blob FROM page_embeddings"
    ).fetchall()
    conn.close()

    slug_chunks: dict[str, list[list[float]]] = defaultdict(list)
    for slug, blob in rows:
        slug_chunks[slug].append(_decode_embedding(blob))

    return {slug: _mean_vector(chunks) for slug, chunks in slug_chunks.items()}


def _load_titles(db_path: Path) -> dict[str, str]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT slug, title FROM pages_index").fetchall()
    conn.close()
    return {slug: title for slug, title in rows}


def _is_archived(slug: str, vault_dir: Path) -> bool:
    """Check if slug is already archived (not in Evergreen dir)."""
    eg = vault_dir / "10-Knowledge" / "Evergreen" / f"{slug}.md"
    return not eg.exists()


def find_embedding_duplicates(
    db_path: Path,
    *,
    threshold: float = 0.92,
    vault_dir: Path | None = None,
    exclude_archived: bool = True,
) -> list[EmbeddingMatch]:
    """Find slug pairs with cosine similarity >= threshold."""
    vectors = _load_page_vectors(db_path)
    titles = _load_titles(db_path)

    if exclude_archived and vault_dir:
        vectors = {s: v for s, v in vectors.items() if not _is_archived(s, vault_dir)}

    slugs = sorted(vectors.keys())
    n = len(slugs)
    matches: list[EmbeddingMatch] = []

    for i in range(n):
        for j in range(i + 1, n):
            sim = _cosine_similarity(vectors[slugs[i]], vectors[slugs[j]])
            if sim >= threshold:
                matches.append(
                    EmbeddingMatch(
                        slug_a=slugs[i],
                        slug_b=slugs[j],
                        cosine_sim=round(sim, 4),
                        title_a=titles.get(slugs[i], slugs[i]),
                        title_b=titles.get(slugs[j], slugs[j]),
                    )
                )

    matches.sort(key=lambda m: -m.cosine_sim)
    return matches


def _cluster_matches(
    matches: list[EmbeddingMatch],
    vault_dir: Path | None = None,
) -> list[list[str]]:
    """Star clustering: each canonical absorbs only its direct neighbours.

    Unlike Union-Find, this avoids transitive chaining where A↔B and B↔C would
    incorrectly merge A and C even when they are dissimilar.  Slugs are greedily
    claimed by the largest canonical that is directly similar to them.
    """
    adj: dict[str, set[str]] = defaultdict(set)
    for m in matches:
        adj[m.slug_a].add(m.slug_b)
        adj[m.slug_b].add(m.slug_a)

    eg_dir = vault_dir / "10-Knowledge" / "Evergreen" if vault_dir else None

    def _file_size(slug: str) -> int:
        if eg_dir:
            fp = eg_dir / f"{slug}.md"
            if fp.exists():
                return fp.stat().st_size
        return 0

    all_slugs = sorted(adj.keys(), key=lambda s: -_file_size(s))
    claimed: set[str] = set()
    clusters: list[list[str]] = []

    for slug in all_slugs:
        if slug in claimed:
            continue
        neighbours = [n for n in adj[slug] if n not in claimed]
        if not neighbours:
            continue
        group = [slug] + sorted(neighbours)
        clusters.append(group)
        claimed.add(slug)
        claimed.update(neighbours)

    return clusters


def generate_proposal(
    vault_dir: Path,
    matches: list[EmbeddingMatch],
    *,
    output_dir: Path | None = None,
) -> Path | None:
    """Generate a dedup proposal JSON from embedding matches."""
    clusters = _cluster_matches(matches, vault_dir=vault_dir)
    if not clusters:
        return None

    from ..concept_dedup import DedupCandidate, DedupCluster, write_proposal

    eg_dir = vault_dir / "10-Knowledge" / "Evergreen"
    dedup_clusters: list[DedupCluster] = []

    sim_map: dict[tuple[str, str], float] = {}
    for m in matches:
        sim_map[(m.slug_a, m.slug_b)] = m.cosine_sim
        sim_map[(m.slug_b, m.slug_a)] = m.cosine_sim

    for group in clusters:
        candidates: list[DedupCandidate] = []
        for slug in group:
            fp = eg_dir / f"{slug}.md"
            if fp.exists():
                candidates.append(
                    DedupCandidate(
                        slug=slug,
                        title=slug.replace("-", " "),
                        path=fp,
                        size_bytes=fp.stat().st_size,
                    )
                )

        if len(candidates) < 2:
            continue

        canonical = max(candidates, key=lambda c: c.size_bytes)
        dups = tuple(c for c in candidates if c.slug != canonical.slug)

        min_sim = min(
            sim_map.get((canonical.slug, d.slug), 0.0) for d in dups
        )
        dedup_clusters.append(
            DedupCluster(canonical=canonical, duplicates=dups, min_similarity=min_sim)
        )

    if not dedup_clusters:
        return None

    path, proposal = write_proposal(vault_dir, dedup_clusters, threshold=0.92)
    return path


def run(
    vault_dir: Path,
    *,
    threshold: float = 0.92,
    limit: int = 0,
    generate: bool = False,
    json_output: bool = False,
) -> dict[str, Any]:
    from ..knowledge_index import _ensure_knowledge_db

    _, layout = _ensure_knowledge_db(vault_dir)
    db_path = layout.knowledge_db

    if not db_path.exists():
        print(f"knowledge.db not found at {db_path}", file=sys.stderr)
        return {"error": "db_not_found"}

    t0 = time.time()
    matches = find_embedding_duplicates(
        db_path,
        threshold=threshold,
        vault_dir=vault_dir,
        exclude_archived=True,
    )
    elapsed = time.time() - t0

    if limit > 0:
        matches = matches[:limit]

    result: dict[str, Any] = {
        "threshold": threshold,
        "matches_found": len(matches),
        "elapsed_seconds": round(elapsed, 2),
    }

    if json_output:
        result["matches"] = [
            {
                "slug_a": m.slug_a,
                "slug_b": m.slug_b,
                "cosine_sim": m.cosine_sim,
                "title_a": m.title_a,
                "title_b": m.title_b,
            }
            for m in matches
        ]
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Embedding dedup scan: threshold={threshold}, elapsed={elapsed:.1f}s")
        print(f"Found {len(matches)} duplicate pairs:\n")
        for i, m in enumerate(matches):
            print(f"  {i+1}. [{m.cosine_sim:.4f}] {m.slug_a}")
            print(f"     ↔ {m.slug_b}")
            print(f"     titles: '{m.title_a}' / '{m.title_b}'")
            print()

    if generate and matches:
        proposal_path = generate_proposal(vault_dir, matches)
        if proposal_path:
            print(f"\nProposal written to: {proposal_path}")
            result["proposal_path"] = str(proposal_path)
        else:
            print("\nNo proposal generated (no valid clusters)")

    clusters = _cluster_matches(matches, vault_dir=vault_dir)
    result["clusters"] = len(clusters)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ovp-embedding-dedup",
        description="Detect paraphrastic clones via embedding cosine similarity",
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=Path.cwd(),
        help="Vault root directory (default: cwd)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.92,
        help="Cosine similarity threshold (default: 0.92)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit output to top N matches (0=all)",
    )
    parser.add_argument(
        "--generate-proposal",
        action="store_true",
        help="Generate a dedup proposal JSON file",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    args = parser.parse_args()
    result = run(
        args.vault_dir,
        threshold=args.threshold,
        limit=args.limit,
        generate=args.generate_proposal,
        json_output=args.json,
    )
    if result.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
