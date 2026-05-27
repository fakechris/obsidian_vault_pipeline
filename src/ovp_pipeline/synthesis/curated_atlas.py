"""Curated Atlas (BL-046, M14).

Ranks the crystal corpus and renders the top-N crystals as a single
Projection markdown at ``40-Resources/CuratedAtlas.md``.  This is
the M14 product surface that turns "329 crystals on disk" into a
"top 30 entry-points the user can scan".

Architecture role: **Access Surface** in the six-term contract.
Reads ``crystal_scores`` (Projection from BL-045) joined with
``community_crystals`` / ``contradiction_crystals``.  Never writes
Canonical State.  The on-disk markdown is itself a Projection
(deletable + rebuildable from the DB).

The user-visible markdown carries:

* Frontmatter with the standard ``projection_*`` lineage + atlas-
  specific metadata (``top_n``, ``selection_basis``, total chain
  counts).
* Header explaining what this page is and how to refresh it.
* One numbered entry per crystal: rank, label, kind, score, a
  one-line teaser pulled from the body, and a wikilink to the
  crystal's source markdown.
* A score-component breakdown so the reader knows *why* a crystal
  is ranked here (size vs credibility vs contradiction vs recency).

Re-running is idempotent: the same crystal_scores produces the
same atlas markdown.  Refresh by invoking
``ovp-build-curated-atlas``; it can also be re-rendered from
existing DB state any time without LLM cost.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..projection_labels import frontmatter_projection_fields
from ._shared import crystal_safe_id as _crystal_safe_id

logger = logging.getLogger(__name__)


# Where the curated atlas markdown lands.  Sibling to the synthesis
# substrate at ``40-Resources/Crystals/`` so reader navigation is
# one filesystem hop away.
CURATED_ATLAS_REL: Path = Path("40-Resources") / "CuratedAtlas.md"

# Default top-N.  Per the M14 plan: 30 is a per-vault sensible
# default; 20 forces hard prioritization, 50 is the upper bound
# for one scrollable list.
DEFAULT_TOP_N: int = 30

# Prompt-version-style marker so future format changes are
# distinguishable on disk without a schema bump.
CURATED_ATLAS_FORMAT_VERSION: str = "v1"


@dataclass(frozen=True, slots=True)
class CuratedEntry:
    rank: int
    crystal_kind: str           # 'community' | 'contradiction'
    crystal_id: str
    label: str
    score: float
    size_norm: float
    credibility_norm: float
    contradiction_norm: float
    reuse_recency_norm: float
    evergreen_recency_norm: float
    # BL-054: source-diversity is a 0.20-weighted signal in the
    # default scoring formula, but it was missing from the entry
    # plumbing — the markdown breakdown / atlas HTML / /topics
    # JSON all rendered "size + credibility + contradiction +
    # reuse + recency" leaving a 20% gap in the displayed
    # explanation that didn't add up to the final ``score``.
    source_diversity_norm: float
    teaser: str
    source_slugs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CuratedAtlas:
    pack: str
    top_n: int
    total_chains: int
    entries: tuple[CuratedEntry, ...]
    generated_at: str


# ----- Teaser extraction --------------------------------------------------


# Strip the standard sampling-disclosure blockquote from
# PR #136 so the teaser uses the actual body content, not the
# operator-facing under-coverage note.
_DISCLOSURE_RE = re.compile(r"^>\s*\*\*采样说明\*\*[^\n]*\n+", re.MULTILINE)

# A markdown header line: ``# foo`` or ``## foo`` etc.
_HEADER_RE = re.compile(r"^#+\s+.*$", re.MULTILINE)

# Pull the first sentence-ish span from a paragraph.  We accept
# Chinese ``。！？`` and Western ``.!?`` as terminators.
_SENTENCE_END_RE = re.compile(r"[。！？!?](?=\s|$|[^\d])")


def _extract_teaser(body_md: str, *, max_chars: int = 180) -> str:
    """Pull a one-line teaser from a crystal body.  Skips the
    sampling-disclosure blockquote and the section headers, then
    takes the first sentence (Chinese or Western punctuation) of
    the first non-blank paragraph.  Truncates at ``max_chars``."""
    text = _DISCLOSURE_RE.sub("", body_md)
    text = _HEADER_RE.sub("", text)
    # Find the first non-blank, non-list-item paragraph.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    for para in paragraphs:
        # Skip lines that are purely list items / wikilink dumps.
        if all(line.lstrip().startswith(("-", "*", "[[")) for line in para.splitlines()):
            continue
        # Join the paragraph's lines into one logical span — a
        # source markdown paragraph can wrap across multiple lines
        # but represent a single sentence; pre-fix
        # ``split("\n", 1)[0]`` truncated those mid-sentence.
        para_text = " ".join(line.strip() for line in para.splitlines() if line.strip())
        if not para_text:
            continue
        match = _SENTENCE_END_RE.search(para_text)
        if match:
            sentence = para_text[:match.end()].strip()
        else:
            sentence = para_text
        if len(sentence) > max_chars:
            sentence = sentence[:max_chars - 1].rstrip() + "…"
        return sentence
    return ""


# ----- DB helpers ---------------------------------------------------------


def _load_top_n(
    conn: sqlite3.Connection, pack: str, top_n: int,
) -> list[dict]:
    """Return the top-N rows from ``crystal_scores`` joined with
    body + label.  One round-trip per kind because the body table
    differs (community vs contradiction); each kind already comes
    back sorted + capped at ``top_n`` so Python only merges and
    truncates the final union — no full-corpus load.

    Fetching ``top_n`` per kind (rather than ``top_n // 2`` each)
    is intentional: in a vault dominated by community crystals,
    contradictions might be empty; conversely a vault with mostly
    contradictions shouldn't lose communities.  Worst case, we
    over-fetch by ``top_n`` rows — still a fixed ceiling, never
    proportional to the corpus.
    """
    rows: list[dict] = []

    # Community crystals join ``crystal_scores`` ↔ ``community_crystals``
    # (current row only) ↔ ``graph_clusters`` (label).
    #
    # BL-114: ``crystal_scores.crystal_id`` is now the stable
    # ``concept_id`` (BL-114 made ``_load_community_index`` return
    # concept_ids).  The label comes from the CURRENT Louvain cluster
    # via the ledger so a concept whose ``current_cluster_id`` shifted
    # across a re-cluster still picks up a fresh label.
    cur = conn.execute(
        """
        SELECT cs.crystal_id, 'community' AS kind, gc.label,
               cs.score, cs.size_norm, cs.credibility_norm,
               cs.contradiction_norm, cs.reuse_recency_norm,
               cs.evergreen_recency_norm, cs.source_diversity_norm,
               cc.body_md, cc.source_evergreen_slugs_json
          FROM crystal_scores cs
          JOIN community_crystals cc
            ON cc.pack = cs.pack AND cc.concept_id = cs.crystal_id
           AND cc.superseded_by_synthesized_at = ''
          JOIN concept_identity_ledger cil
            ON cil.pack = cc.pack AND cil.concept_id = cc.concept_id
          JOIN graph_clusters gc
            ON gc.pack = cil.pack AND gc.cluster_id = cil.current_cluster_id
         WHERE cs.pack = ?
           AND cs.crystal_kind = 'community'
         ORDER BY cs.score DESC
         LIMIT ?
        """,
        (pack, top_n),
    )
    for r in cur:
        rows.append({
            "crystal_id": r[0], "kind": r[1], "label": r[2],
            "score": r[3], "size_norm": r[4], "credibility_norm": r[5],
            "contradiction_norm": r[6], "reuse_recency_norm": r[7],
            "evergreen_recency_norm": r[8], "source_diversity_norm": r[9],
            "body_md": r[10], "source_slugs_json": r[11],
        })

    # Contradiction crystals — different body table; ``label`` is
    # the subject_key.
    cur = conn.execute(
        """
        SELECT cs.crystal_id, 'contradiction' AS kind, cc.subject_key,
               cs.score, cs.size_norm, cs.credibility_norm,
               cs.contradiction_norm, cs.reuse_recency_norm,
               cs.evergreen_recency_norm, cs.source_diversity_norm,
               cc.body_md, cc.source_object_ids_json
          FROM crystal_scores cs
          JOIN contradiction_crystals cc
            ON cc.pack = cs.pack AND cc.contradiction_id = cs.crystal_id
           AND cc.superseded_by_synthesized_at = ''
         WHERE cs.pack = ?
           AND cs.crystal_kind = 'contradiction'
         ORDER BY cs.score DESC
         LIMIT ?
        """,
        (pack, top_n),
    )
    for r in cur:
        rows.append({
            "crystal_id": r[0], "kind": r[1], "label": r[2],
            "score": r[3], "size_norm": r[4], "credibility_norm": r[5],
            "contradiction_norm": r[6], "reuse_recency_norm": r[7],
            "evergreen_recency_norm": r[8], "source_diversity_norm": r[9],
            "body_md": r[10], "source_slugs_json": r[11],
        })

    rows.sort(key=lambda r: -r["score"])
    return rows[:top_n]


def _count_total_chains(conn: sqlite3.Connection, pack: str) -> int:
    """Count of distinct chains across both kinds — the denominator
    for the ``X of Y`` framing in the atlas header."""
    n = conn.execute(
        "SELECT COUNT(*) FROM crystal_scores WHERE pack = ?",
        (pack,),
    ).fetchone()[0]
    return int(n)


# ----- Atlas builder ------------------------------------------------------


def build_curated_atlas(
    conn: sqlite3.Connection,
    *,
    pack: str,
    top_n: int = DEFAULT_TOP_N,
) -> CuratedAtlas:
    """Compose the top-N atlas in memory.  Pure read against the DB;
    safe to call without acquiring the write lock.
    """
    raw_rows = _load_top_n(conn, pack, top_n)
    total = _count_total_chains(conn, pack)
    entries: list[CuratedEntry] = []
    for rank, row in enumerate(raw_rows, start=1):
        try:
            slugs = tuple(json.loads(row["source_slugs_json"]))
        except (TypeError, json.JSONDecodeError):
            slugs = ()
        entries.append(CuratedEntry(
            rank=rank, crystal_kind=row["kind"],
            crystal_id=row["crystal_id"], label=row["label"] or "(untitled)",
            score=float(row["score"]),
            size_norm=float(row["size_norm"]),
            credibility_norm=float(row["credibility_norm"]),
            contradiction_norm=float(row["contradiction_norm"]),
            reuse_recency_norm=float(row["reuse_recency_norm"]),
            evergreen_recency_norm=float(row["evergreen_recency_norm"]),
            source_diversity_norm=float(row.get("source_diversity_norm", 0.0)),
            teaser=_extract_teaser(row["body_md"] or ""),
            source_slugs=slugs,
        ))
    return CuratedAtlas(
        pack=pack,
        top_n=top_n,
        total_chains=total,
        entries=tuple(entries),
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


# ----- Markdown rendering -------------------------------------------------


# ``_crystal_safe_id`` is re-exported from ``_shared`` (pre-fix this
# file held a copy of the same prefix-stripping logic — see
# ``_shared.crystal_safe_id`` for the canonical home).


def _crystal_wikilink(kind: str, crystal_id: str, *, label: str) -> str:
    safe_id = _crystal_safe_id(kind, crystal_id)
    return f"[[{safe_id}|{label}]]"


def render_curated_atlas_markdown(atlas: CuratedAtlas) -> str:
    lines: list[str] = ["---", "type: curated_atlas"]
    lines.append(f"format_version: {CURATED_ATLAS_FORMAT_VERSION}")
    lines.append(f"top_n: {atlas.top_n}")
    lines.append(f"total_chains: {atlas.total_chains}")
    lines.append("selection_basis: crystal_scores")
    lines.append(f"generated_at: {atlas.generated_at}")
    lines.append(f"pack: {atlas.pack}")
    lines.append("tags: [atlas, curated, projection]")
    lines.extend(frontmatter_projection_fields(
        surface="curated_atlas",
        projection_kind="compiled_wiki_projection",
        owner_pack=atlas.pack,
        generated_by="ovp-build-curated-atlas",
        derived_from=(
            "knowledge.db.crystal_scores",
            "knowledge.db.community_crystals",
            "knowledge.db.contradiction_crystals",
            "knowledge.db.graph_clusters",
        ),
        rebuild_policy="on_demand_or_refresh",
    ))
    lines.extend(["---", "", "# Curated Atlas", ""])

    if atlas.total_chains == 0:
        lines.append(
            "_No crystals scored yet.  Run `ovp-knowledge-index` after a "
            "successful `ovp-synthesize-community-crystals` to populate "
            "the underlying Projections._"
        )
        return "\n".join(lines) + "\n"

    if atlas.entries:
        lines.append(
            f"Top {len(atlas.entries)} of {atlas.total_chains} crystal chains "
            f"in pack `{atlas.pack}`, ranked by `crystal_scores`.  Generated "
            f"{atlas.generated_at}."
        )
    else:
        lines.append(
            f"No entries in the top-{atlas.top_n} for pack `{atlas.pack}` "
            "even though the corpus is non-empty — this usually means the "
            "scores haven't been rebuilt; run `ovp-rescore-crystals`."
        )
    lines.extend([
        "",
        "> This page is a **Projection** over the synthesis substrate.  "
        "Refresh by re-running `ovp-knowledge-index` (which auto-rebuilds "
        "scores) or `ovp-build-curated-atlas` directly.  Deleting this "
        "file does not affect the underlying crystal corpus.",
        "",
    ])

    for entry in atlas.entries:
        kind_marker = "🌐" if entry.crystal_kind == "community" else "⚖️"
        teaser = entry.teaser or "_(no teaser available)_"
        link = _crystal_wikilink(
            entry.crystal_kind, entry.crystal_id, label=entry.label,
        )
        lines.extend([
            f"## {entry.rank}. {kind_marker} {entry.label}  "
            f"_(score {entry.score:.3f})_",
            "",
            teaser,
            "",
            f"- {link}",
            f"- crystal_kind: {entry.crystal_kind}",
            f"- crystal_id: `{entry.crystal_id}`",
            f"- score breakdown: "
            f"size {entry.size_norm:.2f} · "
            f"credibility {entry.credibility_norm:.2f} · "
            f"source-diversity {entry.source_diversity_norm:.2f} · "
            f"contradiction {entry.contradiction_norm:.2f} · "
            f"reuse-recency {entry.reuse_recency_norm:.2f} · "
            f"evergreen-recency {entry.evergreen_recency_norm:.2f}",
            "",
        ])

    return "\n".join(lines) + "\n"


# ----- Public entry point -------------------------------------------------


def write_curated_atlas(
    vault_dir: Path,
    *,
    db_path: Path,
    pack: str,
    top_n: int = DEFAULT_TOP_N,
    dry_run: bool = False,
) -> tuple[CuratedAtlas, Path]:
    """Build the atlas + write to ``40-Resources/CuratedAtlas.md``
    via tempfile + os.replace for atomic-on-same-filesystem update.
    Returns ``(atlas, target_path)``.  In dry-run, the target_path
    is reported but not touched.
    """
    conn = sqlite3.connect(db_path)
    try:
        atlas = build_curated_atlas(conn, pack=pack, top_n=top_n)
    finally:
        conn.close()

    target = (vault_dir / CURATED_ATLAS_REL).resolve()
    vault_root = vault_dir.resolve()
    try:
        target.relative_to(vault_root)
    except ValueError:
        # Defense in depth: refuse to write outside the vault root.
        raise RuntimeError(
            f"refusing to write curated atlas outside vault: {target}"
        )

    if dry_run:
        return atlas, target

    target.parent.mkdir(parents=True, exist_ok=True)
    markdown = render_curated_atlas_markdown(atlas)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(markdown, encoding="utf-8")
    # ``Path.replace`` is the idiomatic atomic-rename — same
    # underlying ``os.replace`` semantics, no inline import.
    tmp.replace(target)
    return atlas, target
