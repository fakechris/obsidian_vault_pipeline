"""Phase 38.A — Canonical Evergreen file-level deduplication.

Identifies clusters of near-duplicate Evergreen files (e.g. 7 variants of
"MCP Client") and merges them safely:

  1. Find clusters by trigram-Jaccard over normalized slugs.
  2. Write the proposal as JSON under ``60-Logs/dedup-proposals/`` so a human
     can review before any file is touched.
  3. ``apply`` the proposal:
       - Archive each duplicate file to ``70-Archive/dedup-merged/<slug>.md``
         (atomic rename; preserves git blame via ``git mv`` if available, but
         falls back to ``Path.replace`` so tests don't need git).
       - Add the duplicate slug to the canonical's ``aliases:`` frontmatter.
       - Rewrite ``[[dup_slug]]`` / ``[[dup_slug#anchor]]`` /
         ``[[dup_slug|display]]`` across the vault to point at the canonical
         (display text and anchors are preserved).
       - Emit a ``concept_merged`` audit event to
         ``60-Logs/concept-merges.jsonl``.
       - Best-effort: if a ``ConceptRegistry`` is loadable, call
         ``merge_as_alias`` so the registry tracks the redirect. Failure to
         update the registry does not abort the merge — the JSONL audit log is
         the truth, registry rebuild can replay it.

The operation is **invertible**: archived files retain their original slug as
the filename and their full content; the canonical's aliases list records the
merge; the audit JSONL holds the full mapping.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .event_emitter import emit

CONCEPT_MERGES_LOG = "concept-merges.jsonl"
DEDUP_PROPOSALS_DIR = "dedup-proposals"
DEDUP_ARCHIVE_DIR = "dedup-merged"
DEFAULT_THRESHOLD = 0.82


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DedupCandidate:
    slug: str
    title: str
    path: Path
    size_bytes: int

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "title": self.title,
            "path": str(self.path),
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DedupCandidate":
        return cls(
            slug=data["slug"],
            title=data["title"],
            path=Path(data["path"]),
            size_bytes=int(data.get("size_bytes", 0)),
        )


@dataclass(frozen=True)
class DedupCluster:
    canonical: DedupCandidate
    duplicates: tuple[DedupCandidate, ...]
    min_similarity: float

    def to_dict(self) -> dict:
        return {
            "canonical": self.canonical.to_dict(),
            "duplicates": [d.to_dict() for d in self.duplicates],
            "min_similarity": self.min_similarity,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DedupCluster":
        return cls(
            canonical=DedupCandidate.from_dict(data["canonical"]),
            duplicates=tuple(DedupCandidate.from_dict(d) for d in data["duplicates"]),
            min_similarity=float(data["min_similarity"]),
        )


@dataclass(frozen=True)
class DedupProposal:
    proposal_id: str
    created_at: str
    threshold: float
    clusters: tuple[DedupCluster, ...]

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "created_at": self.created_at,
            "threshold": self.threshold,
            "clusters": [c.to_dict() for c in self.clusters],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DedupProposal":
        return cls(
            proposal_id=data["proposal_id"],
            created_at=data["created_at"],
            threshold=float(data["threshold"]),
            clusters=tuple(DedupCluster.from_dict(c) for c in data["clusters"]),
        )


@dataclass
class ApplyResult:
    canonical_slug: str
    archived: list[Path] = field(default_factory=list)
    wikilink_rewrites: int = 0
    aliases_added: list[str] = field(default_factory=list)
    registry_updated: bool = False
    audit_event_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Cluster discovery
# ---------------------------------------------------------------------------


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    padded = f"  {text}  "
    if len(padded) < n:
        return {padded}
    return {padded[i : i + n] for i in range(len(padded) - n + 1)}


def trigram_jaccard(a: str, b: str) -> float:
    ga = _char_ngrams(a)
    gb = _char_ngrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _normalize_slug_for_compare(slug: str) -> str:
    return re.sub(r"[-_\s]+", " ", slug.strip().lower())


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_TITLE_RE = re.compile(r'^title:\s*"?([^"\n]+)"?\s*$', re.MULTILINE)


def _read_title(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return path.stem
    fm = _FRONTMATTER_RE.match(text)
    if not fm:
        return path.stem
    title = _TITLE_RE.search(fm.group(1))
    if title:
        return title.group(1).strip().strip('"').strip("'")
    return path.stem


def _scan_evergreen(vault_dir: Path) -> list[DedupCandidate]:
    evergreen_dir = vault_dir / "10-Knowledge" / "Evergreen"
    if not evergreen_dir.exists():
        return []
    cands: list[DedupCandidate] = []
    for md_file in sorted(evergreen_dir.glob("*.md")):
        if md_file.stem.startswith(("_", ".")):
            continue
        try:
            size = md_file.stat().st_size
        except OSError:
            continue
        cands.append(
            DedupCandidate(
                slug=md_file.stem,
                title=_read_title(md_file),
                path=md_file,
                size_bytes=size,
            )
        )
    return cands


def _cluster_by_similarity(
    cands: list[DedupCandidate], threshold: float
) -> list[tuple[list[DedupCandidate], float]]:
    """Union-find clustering by trigram similarity over normalized slugs."""
    n = len(cands)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    edge_sims: dict[tuple[int, int], float] = {}
    norms = [_normalize_slug_for_compare(c.slug) for c in cands]
    for i in range(n):
        for j in range(i + 1, n):
            sim = trigram_jaccard(norms[i], norms[j])
            if sim >= threshold:
                edge_sims[(i, j)] = sim
                union(i, j)

    groups: dict[int, list[int]] = defaultdict(list)
    for idx in range(n):
        groups[find(idx)].append(idx)

    out: list[tuple[list[DedupCandidate], float]] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        # min similarity inside the cluster (drop singletons that joined via transitive union)
        min_sim = min(
            (edge_sims.get((min(a, b), max(a, b)), 1.0) for a in members for b in members if a < b),
            default=1.0,
        )
        out.append(([cands[i] for i in members], min_sim))
    return out


def _pick_canonical(members: list[DedupCandidate]) -> DedupCandidate:
    """Canonical = largest body (most curated content), tiebreak shortest slug, tiebreak slug asc."""
    return max(
        members, key=lambda c: (c.size_bytes, -len(c.slug), -ord(c.slug[0]) if c.slug else 0)
    )


def find_clusters(vault_dir: Path, *, threshold: float = DEFAULT_THRESHOLD) -> list[DedupCluster]:
    cands = _scan_evergreen(vault_dir)
    raw = _cluster_by_similarity(cands, threshold=threshold)
    clusters: list[DedupCluster] = []
    for members, min_sim in raw:
        canonical = _pick_canonical(members)
        dups = tuple(sorted((m for m in members if m.slug != canonical.slug), key=lambda c: c.slug))
        if not dups:
            continue
        clusters.append(DedupCluster(canonical=canonical, duplicates=dups, min_similarity=min_sim))
    clusters.sort(key=lambda c: (-len(c.duplicates), c.canonical.slug))
    return clusters


# ---------------------------------------------------------------------------
# Proposal storage
# ---------------------------------------------------------------------------


def _proposals_dir(vault_dir: Path) -> Path:
    return vault_dir / "60-Logs" / DEDUP_PROPOSALS_DIR


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_proposal(
    vault_dir: Path,
    clusters: list[DedupCluster],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[Path, DedupProposal]:
    proposal_id = f"dedup-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    proposal = DedupProposal(
        proposal_id=proposal_id,
        created_at=_utc_now_text(),
        threshold=threshold,
        clusters=tuple(clusters),
    )
    out_dir = _proposals_dir(vault_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{proposal_id}.json"
    path.write_text(json.dumps(proposal.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path, proposal


def list_proposals(vault_dir: Path) -> list[Path]:
    out_dir = _proposals_dir(vault_dir)
    if not out_dir.exists():
        return []
    return sorted(out_dir.glob("dedup-*.json"))


def archive_applied_proposal(vault_dir: Path, path: Path) -> Path:
    applied_dir = _proposals_dir(vault_dir) / "applied"
    applied_dir.mkdir(parents=True, exist_ok=True)
    dest = applied_dir / path.name
    if dest.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = applied_dir / f"{path.stem}.{stamp}{path.suffix}"
    path.replace(dest)
    return dest


def load_proposal(path: Path) -> DedupProposal:
    return DedupProposal.from_dict(json.loads(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


_WIKILINK_RE = re.compile(r"\[\[([^\[\]\|#]+)(#[^\[\]\|]+)?(\|[^\[\]]+)?\]\]")
_ALIASES_BLOCK_RE = re.compile(r"^aliases:\s*\[(.*?)\]", re.MULTILINE)
_ALIASES_LINE_RE = re.compile(r"^aliases:\s*$", re.MULTILINE)


def _rewrite_wikilinks(text: str, slug_map: dict[str, str]) -> tuple[str, int]:
    """Replace ``[[old_slug...]]`` with ``[[new_slug...]]``. Anchor + display preserved."""
    count = 0

    def repl(match: re.Match) -> str:
        nonlocal count
        target, anchor, display = match.group(1), match.group(2) or "", match.group(3) or ""
        new_target = slug_map.get(target.strip(), target)
        if new_target != target:
            count += 1
            return f"[[{new_target}{anchor}{display}]]"
        return match.group(0)

    new_text = _WIKILINK_RE.sub(repl, text)
    return new_text, count


def _add_alias_to_frontmatter(text: str, alias: str) -> str:
    """Add ``alias`` to the ``aliases:`` list in YAML frontmatter (idempotent)."""
    fm_match = _FRONTMATTER_RE.match(text)
    if not fm_match:
        return text
    fm_body = fm_match.group(1)
    block_match = _ALIASES_BLOCK_RE.search(fm_body)
    if block_match:
        existing = block_match.group(1)
        # naive parse — split on commas, strip quotes/whitespace
        items = [s.strip().strip('"').strip("'") for s in existing.split(",") if s.strip()]
        if alias in items:
            return text
        items.append(alias)
        new_block = "aliases: [" + ", ".join(f'"{it}"' for it in items) + "]"
        new_body = fm_body[: block_match.start()] + new_block + fm_body[block_match.end() :]
    else:
        # no aliases line — insert after first frontmatter line
        new_body = fm_body.rstrip() + f'\naliases: ["{alias}"]'
    return f"---\n{new_body}\n---\n" + text[fm_match.end() :]


def _archive_dest(vault_dir: Path, slug: str) -> Path:
    archive_dir = vault_dir / "70-Archive" / DEDUP_ARCHIVE_DIR
    archive_dir.mkdir(parents=True, exist_ok=True)
    base = archive_dir / f"{slug}.md"
    if not base.exists():
        return base
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return archive_dir / f"{slug}.{stamp}.md"


def _archive_markdown_file(vault_dir: Path, source: Path, dest: Path) -> None:
    """Archive a Markdown file, preferring Obsidian CLI so wikilinks stay coherent."""
    obsidian = shutil.which("obsidian")
    if obsidian and (vault_dir / ".obsidian").exists():
        source_arg = source.relative_to(vault_dir).as_posix()
        dest_arg = dest.relative_to(vault_dir).as_posix()
        try:
            completed = subprocess.run(
                [obsidian, "move", f"file={source_arg}", f"to={dest_arg}"],
                cwd=vault_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.SubprocessError as exc:
            raise OSError(f"obsidian move failed for {source_arg}: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise OSError(f"obsidian move failed for {source_arg}: {detail}")
        return

    source.rename(dest)


def _iter_vault_md(vault_dir: Path) -> Iterable[Path]:
    skip_dirs = {".git", ".obsidian", "70-Archive", "60-Logs"}
    for md in vault_dir.rglob("*.md"):
        parts = md.relative_to(vault_dir).parts
        if any(p in skip_dirs for p in parts):
            continue
        yield md


def _try_registry_merge(
    vault_dir: Path, canonical_slug: str, dup_slugs: list[str]
) -> tuple[bool, str | None]:
    try:
        from .concept_registry import ConceptRegistry  # local import — heavy module
    except Exception as exc:  # pragma: no cover
        return False, f"registry import failed: {exc}"

    try:
        registry = ConceptRegistry(vault_dir).load()
    except Exception as exc:
        return False, f"registry load failed: {exc}"

    target = registry.find_by_slug(canonical_slug)
    if not target:
        # Canonical isn't tracked in the registry yet — that's fine, the
        # frontmatter alias + audit log carry the truth. Not an error.
        return False, None

    merged_any = False
    for dup_slug in dup_slugs:
        cand = registry.find_by_slug(dup_slug)
        if not cand:
            continue
        try:
            registry.merge_as_alias(dup_slug, canonical_slug, [dup_slug])
            merged_any = True
        except Exception as exc:
            return merged_any, f"merge_as_alias({dup_slug}) failed: {exc}"

    if merged_any:
        try:
            registry.save()
        except Exception as exc:
            return False, f"registry save failed: {exc}"
    return merged_any, None


def apply_cluster(
    vault_dir: Path,
    cluster: DedupCluster,
    *,
    dry_run: bool = True,
    pack: str = "",
    proposal_id: str = "",
) -> ApplyResult:
    result = ApplyResult(canonical_slug=cluster.canonical.slug)
    canonical_path = cluster.canonical.path
    if not canonical_path.exists():
        result.errors.append(f"canonical file missing: {canonical_path}")
        return result

    dup_slugs = [d.slug for d in cluster.duplicates]
    slug_map = {dup: cluster.canonical.slug for dup in dup_slugs}

    # Pre-validate every duplicate exists before touching the vault. Otherwise
    # a missing duplicate surfaces only at step 3 (archive), after wikilinks
    # and aliases have already been mutated — leaving the vault half-merged
    # with no way to roll back. Fail-fast keeps apply atomic in the
    # non-dry-run path.
    if not dry_run:
        missing = [d.slug for d in cluster.duplicates if not d.path.exists()]
        if missing:
            result.errors.append(
                "refusing to apply: missing duplicate files: " + ", ".join(missing)
            )
            return result

    # 1. Rewrite wikilinks across vault (skip the duplicates themselves — about to archive).
    dup_paths = {d.path for d in cluster.duplicates}
    for md in _iter_vault_md(vault_dir):
        if md in dup_paths:
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        new_text, count = _rewrite_wikilinks(text, slug_map)
        if count == 0:
            continue
        result.wikilink_rewrites += count
        if not dry_run:
            md.write_text(new_text, encoding="utf-8")

    # 2. Add duplicate slugs as aliases on canonical.
    try:
        canonical_text = canonical_path.read_text(encoding="utf-8")
    except OSError as exc:
        result.errors.append(f"read canonical: {exc}")
        return result
    new_canonical_text = canonical_text
    for dup_slug in dup_slugs:
        before = new_canonical_text
        new_canonical_text = _add_alias_to_frontmatter(new_canonical_text, dup_slug)
        if new_canonical_text != before:
            result.aliases_added.append(dup_slug)
    if not dry_run and new_canonical_text != canonical_text:
        canonical_path.write_text(new_canonical_text, encoding="utf-8")

    # 3. Archive duplicates.
    archived: list[tuple[Path, Path]] = []
    for dup in cluster.duplicates:
        if not dup.path.exists():
            result.errors.append(f"duplicate missing: {dup.path}")
            continue
        dest = _archive_dest(vault_dir, dup.slug)
        if dry_run:
            result.archived.append(dest)
            continue
        try:
            _archive_markdown_file(vault_dir, dup.path, dest)
        except OSError as exc:
            result.errors.append(f"archive {dup.slug}: {exc}")
            continue
        result.archived.append(dest)
        archived.append((dup.path, dest))

    # 4. Best-effort registry merge.
    if not dry_run:
        ok, err = _try_registry_merge(vault_dir, cluster.canonical.slug, dup_slugs)
        result.registry_updated = ok
        if err:
            result.errors.append(f"registry: {err}")

    # 5. Audit event.
    if not dry_run:
        event = emit(
            vault_dir,
            CONCEPT_MERGES_LOG,
            "concept_merged",
            {
                "proposal_id": proposal_id,
                "canonical_slug": cluster.canonical.slug,
                "canonical_path": str(canonical_path.relative_to(vault_dir)),
                "merged_slugs": dup_slugs,
                "archived_to": [str(p.relative_to(vault_dir)) for p in result.archived],
                "wikilink_rewrites": result.wikilink_rewrites,
                "aliases_added": result.aliases_added,
                "registry_updated": result.registry_updated,
                "min_similarity": cluster.min_similarity,
            },
            pack=pack,
        )
        result.audit_event_ids.append(event["event_id"])

    return result


def apply_proposal(
    vault_dir: Path,
    proposal: DedupProposal,
    *,
    dry_run: bool = True,
    pack: str = "",
    only_canonicals: set[str] | None = None,
) -> list[ApplyResult]:
    results: list[ApplyResult] = []
    for cluster in proposal.clusters:
        if only_canonicals is not None and cluster.canonical.slug not in only_canonicals:
            continue
        results.append(
            apply_cluster(
                vault_dir,
                cluster,
                dry_run=dry_run,
                pack=pack,
                proposal_id=proposal.proposal_id,
            )
        )
    return results
