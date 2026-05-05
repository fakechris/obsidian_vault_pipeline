"""BL-054 — Backfill source provenance into legacy evergreen frontmatter.

The historical extractor never wrote ``source_url`` /
``source_fingerprint`` / ``source_title`` into evergreen frontmatter,
so 6,584 evergreens have empty source-provenance fields.  Without
provenance, ``crystal_scoring`` cannot:

* dedupe ``credibility_norm`` by source URL
* compute the BL-054 ``source_diversity_norm`` signal

The clean attribution source is the ``audit_events`` table, which
records ``evergreen_auto_promoted`` events for every promoted
evergreen with both the concept slug and the source-article filename.
This is far more reliable than walking ``link-resolution/*.json``
(which only sees ~3% of current evergreens because slugs are
canonicalised during promotion).

For each evergreen with a promotion event, the source article is
located on disk under ``50-Inbox/03-Processed/<YYYY-MM>/<file>.md``;
its frontmatter ``source: <URL>`` becomes ``source_url`` on the
evergreen.  ``source_fingerprint`` is a 12-char SHA-256 of the URL.

Idempotent: writes only when fields are currently empty.  Re-running
is safe.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sqlite3
import sys
from pathlib import Path

from ..runtime import VaultLayout, resolve_vault_dir

logger = logging.getLogger(__name__)


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _read_frontmatter_dict(text: str) -> dict[str, str]:
    """Lightweight YAML-ish frontmatter parser; returns flat
    ``key → raw value`` strings.  Good enough for the few scalar
    fields we care about (source_url, source_fingerprint)."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    out: dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, raw_value = line.partition(":")
        out[key.strip()] = raw_value.strip()
    return out


_URL_FIELD_PRIORITY = (
    "source", "source_url", "url", "github", "twitter", "arxiv",
)


def _build_evergreen_to_source_from_audit(
    db_path: Path,
    vault_root: Path,
) -> dict[str, dict[str, str]]:
    """Read ``evergreen_auto_promoted`` audit rows; for each, look up
    the source article on disk and read its frontmatter.  Returns
    ``slug → {source_url, source_title, source_authors, source_published_at}``
    with the latest promotion per slug.

    Returns empty dict on missing DB / missing audit_events table.
    """
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.DatabaseError:
        return {}
    try:
        rows = conn.execute(
            "SELECT payload_json FROM audit_events "
            "WHERE event_type='evergreen_auto_promoted' "
            "ORDER BY timestamp"
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return {}
    conn.close()

    # First pass: build slug → source_filename
    slug_to_source_filename: dict[str, str] = {}
    for (payload_text,) in rows:
        try:
            payload = json.loads(payload_text)
        except (json.JSONDecodeError, TypeError):
            continue
        slug = str(payload.get("concept") or "")
        source = str(payload.get("source") or "")
        if not slug or not source:
            continue
        # Latest wins (rows ordered by timestamp asc).
        slug_to_source_filename[slug] = source
    logger.info(
        "audit_events scan: %d unique evergreen → source mappings",
        len(slug_to_source_filename),
    )

    # Second pass: resolve each source filename to a real file
    # somewhere in the vault (sources have moved across folders over
    # the project lifetime — 50-Inbox / 20-Areas / 70-Archive).
    # Build a one-shot index by stem to avoid repeated rglob scans.
    logger.info("indexing vault source files by stem (one-shot)…")
    # Reviewer PR #152 (gemini + coderabbit MAJOR): stem-only matching
    # silently picks the first occurrence when the same filename
    # exists in multiple folders (e.g. ``Inbox/Plan.md`` vs
    # ``Archive/Plan.md``).  Use a ``str → list[Path]`` index, then
    # disambiguate at lookup time.  Reader-shell + crystal directories
    # are excluded by full-path containment with leading ``/`` so a
    # vault root that happens to contain the substring "Evergreen"
    # doesn't accidentally exclude everything.
    SKIP_PREFIXES = ("/10-Knowledge/Evergreen/", "/10-Knowledge/Crystals/")
    source_index: dict[str, list[Path]] = {}
    for md in vault_root.rglob("*.md"):
        path_str = str(md)
        if any(prefix in path_str for prefix in SKIP_PREFIXES):
            continue
        source_index.setdefault(md.stem, []).append(md)
    n_unique = sum(1 for v in source_index.values() if len(v) == 1)
    n_collisions = sum(1 for v in source_index.values() if len(v) > 1)
    logger.info(
        "vault source-file index: %d unique stems, %d collisions",
        n_unique, n_collisions,
    )

    fm_cache: dict[str, dict[str, str]] = {}

    def _read_source_fm(filename: str) -> dict[str, str]:
        if filename in fm_cache:
            return fm_cache[filename]
        stem = Path(filename).stem
        candidates = source_index.get(stem) or []
        # Stem-collision disambiguation: prefer a candidate whose
        # parent dir name appears in the audit-log filename's date
        # prefix (``2026-04`` etc.), then fall back to the first.
        path: Path | None
        if not candidates:
            path = None
        elif len(candidates) == 1:
            path = candidates[0]
        else:
            target_date_prefix = filename[:7]  # "2026-04" from "2026-04-..."
            scored = sorted(
                candidates,
                key=lambda p: (
                    target_date_prefix not in str(p),  # False (=match) sorts first
                    len(str(p)),  # shorter path wins ties
                ),
            )
            path = scored[0]
        if path is None:
            fm_cache[filename] = {}
            return fm_cache[filename]
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            fm_cache[filename] = {}
            return fm_cache[filename]
        fm = _read_frontmatter_dict(text)
        for key in (*_URL_FIELD_PRIORITY, "title", "author", "date"):
            v = fm.get(key, "")
            if v.startswith('"') and v.endswith('"'):
                fm[key] = v[1:-1]
            elif v.startswith("'") and v.endswith("'"):
                fm[key] = v[1:-1]
        fm_cache[filename] = fm
        return fm

    out: dict[str, dict[str, str]] = {}
    for slug, source_filename in slug_to_source_filename.items():
        fm = _read_source_fm(source_filename)
        # Pick the first non-empty URL-shaped field.
        source_url = ""
        for key in _URL_FIELD_PRIORITY:
            candidate = fm.get(key, "").strip()
            if candidate:
                source_url = candidate
                break
        if not source_url:
            # Fall back to a synthetic identifier so the source-diversity
            # signal still has something to dedupe on, even if we cannot
            # resolve a real URL.  Distinct from real URL space.
            source_url = f"vault://source/{Path(source_filename).stem}"
        out[slug] = {
            "source_url": source_url,
            "source_title": fm.get("title", "") or Path(source_filename).stem,
            "source_authors": fm.get("author", ""),
            "source_published_at": fm.get("date", ""),
            "source_filename": source_filename,
        }
    return out


def _make_fingerprint(source_url: str) -> str:
    return hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:12]


def _yaml_inline(value: str) -> str:
    """Quote a value for inline YAML scalar use; minimal escapes."""
    escaped = value.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


def _patch_frontmatter(
    text: str, source_url: str, fingerprint: str,
) -> tuple[str, bool]:
    """Insert or replace ``source_url`` / ``source_fingerprint`` lines
    in the frontmatter block of ``text``.  Only sets fields that are
    currently empty so re-runs leave fully-populated rows untouched.
    Returns ``(new_text, changed)``."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return text, False
    fm_block = match.group(1)
    existing = _read_frontmatter_dict(text)
    cur_url = existing.get("source_url", "")
    cur_fp = existing.get("source_fingerprint", "")

    def _is_blank_quoted(value: str) -> bool:
        v = value.strip()
        return v in ('""', "''", "")

    needs_url = _is_blank_quoted(cur_url)
    needs_fp = _is_blank_quoted(cur_fp)
    if not needs_url and not needs_fp:
        return text, False

    new_lines: list[str] = []
    saw_url = False
    saw_fp = False
    for line in fm_block.splitlines():
        if needs_url and line.lstrip().startswith("source_url:") and not saw_url:
            new_lines.append(f"source_url: {_yaml_inline(source_url)}")
            saw_url = True
            continue
        if needs_fp and line.lstrip().startswith("source_fingerprint:") and not saw_fp:
            new_lines.append(f"source_fingerprint: {_yaml_inline(fingerprint)}")
            saw_fp = True
            continue
        new_lines.append(line)
    # If frontmatter didn't have the keys at all, append them so future
    # rebuilds can read from frontmatter unconditionally.
    if needs_url and not saw_url:
        new_lines.append(f"source_url: {_yaml_inline(source_url)}")
    if needs_fp and not saw_fp:
        new_lines.append(f"source_fingerprint: {_yaml_inline(fingerprint)}")
    new_block = "\n".join(new_lines) + "\n"
    body_after = text[match.end():]
    return f"---\n{new_block}---\n{body_after}", True


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        description=(
            "Backfill ``source_url`` / ``source_fingerprint`` into "
            "legacy evergreen frontmatter using the link-resolution "
            "JSON logs as the attribution source."
        ),
    )
    parser.add_argument("--vault-dir", type=Path, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be written but make no changes.",
    )
    args = parser.parse_args(argv)
    vault = resolve_vault_dir(args.vault_dir)
    layout = VaultLayout.from_vault(vault)

    evergreen_to_source = _build_evergreen_to_source_from_audit(
        layout.knowledge_db, vault,
    )

    evergreen_dir = vault / "10-Knowledge" / "Evergreen"
    if not evergreen_dir.is_dir():
        print(f"no evergreen dir at {evergreen_dir}", file=sys.stderr)
        return 1

    total_evergreens = 0
    matched = 0
    written = 0
    skipped_already_set = 0
    unmatched = 0
    real_url_count = 0
    synthetic_count = 0

    for md in sorted(evergreen_dir.glob("*.md")):
        total_evergreens += 1
        slug = md.stem
        info = evergreen_to_source.get(slug)
        if info is None:
            unmatched += 1
            continue
        matched += 1
        source_url = info["source_url"]
        if source_url.startswith("vault://"):
            synthetic_count += 1
        else:
            real_url_count += 1
        fingerprint = _make_fingerprint(source_url)
        text = md.read_text(encoding="utf-8")
        new_text, changed = _patch_frontmatter(text, source_url, fingerprint)
        if not changed:
            skipped_already_set += 1
            continue
        if not args.dry_run:
            md.write_text(new_text, encoding="utf-8")
        written += 1

    print(f"=== Backfill summary ({'dry-run' if args.dry_run else 'applied'}) ===")
    print(f"  evergreens scanned:          {total_evergreens}")
    print(f"  matched to a source article: {matched} "
          f"({100*matched/max(total_evergreens,1):.1f}%)")
    print(f"  ↳ with real URL:             {real_url_count}")
    print(f"  ↳ with vault:// fallback:    {synthetic_count}")
    print(f"  unmatched (left empty):      {unmatched}")
    print(f"  frontmatter rewritten:       {written}")
    print(f"  already populated, skipped:  {skipped_already_set}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
