#!/usr/bin/env python3
"""auto_github_processor — GitHub project intake (BL-066).

Replaces the previous "13-section deep-dive" LLM rewrite with a
deterministic enrichment chain that fetches richer source material
and writes it to ``50-Inbox/03-Processed/{YYYY-MM}/`` so absorb can
read it directly.

Pipeline integration:

    pinboard_process step calls
        ovp_pipeline.auto_github_processor --process-single <stub.md>

    For each GitHub URL:

        1. ``enrich_github_source(owner, repo)``
           - Tier 1: DeepWiki (pre-rendered structured wiki)
           - Tier 2: GitIngest (clone + concatenated docs)
           - Tier 3: README from raw.githubusercontent.com
        2. Write to ``50-Inbox/03-Processed/{YYYY-MM}/<date>_<owner>_<repo>.md``
           with frontmatter:
               source: https://github.com/<owner>/<repo>
               source_type: github-project
               source_tier: deepwiki | gitingest | readme
               github_owner / github_repo / github_stars
               source_fetched_at: <iso>
               source_indexed_at: <DeepWiki last-indexed, if applicable>

This step does NOT call any LLM — it is now pure intake.  Knowledge
extraction happens later in absorb, which reads from the same
``03-Processed/`` directory regardless of source type.

Why we removed the 13-section deep-dive:
    The 13-section template was an LLM-driven rewrite that forced
    every repo into the same wiki shape (项目定位 / 技术架构 / 核心
    能力 / etc.).  absorb would then re-flatten that wiki shape back
    into atomic units — two LLM passes, both lossy and both
    abstraction-inflating.  The fidelity audit on 2026-05-05 showed
    most absorbed evergreens from this path were ``faithful_generic``
    (the source had specifics, the deep-dive abstracted them away,
    absorb amplified the loss).  Skipping the deep-dive entirely and
    feeding richer raw material directly to absorb is strictly better.

Usage:
    python -m ovp_pipeline.auto_github_processor --single https://github.com/owner/repo
    python -m ovp_pipeline.auto_github_processor --process-single <pinboard-stub.md>
    python -m ovp_pipeline.auto_github_processor --input github_urls.txt
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .runtime import VaultLayout, resolve_vault_dir
except ImportError:
    from runtime import VaultLayout, resolve_vault_dir  # type: ignore

try:
    from .github_enrichment import (
        EnrichedSource,
        enrich_github_source,
        parse_github_url,
    )
except ImportError:
    from github_enrichment import (  # type: ignore
        EnrichedSource,
        enrich_github_source,
        parse_github_url,
    )

try:
    from .source_lifecycle import maybe_archive_pinboard_process_single
except ImportError:
    from source_lifecycle import maybe_archive_pinboard_process_single  # type: ignore


VAULT_DIR = resolve_vault_dir()


def load_env_file(vault_dir: Path) -> None:
    """Load environment variables from the vault root if present.

    Kept for backward compatibility with callers that expect this
    function — enrichment itself doesn't need any API keys, but
    callers may set vault-scoped vars here.
    """
    env_file = vault_dir / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=env_file, override=True)
    except ImportError:
        pass


def build_default_output_dir(vault_dir: Path | str | None = None) -> Path:
    """Default output directory for processed GitHub source markdowns.

    Was ``20-Areas/Tools/Topics/YYYY-MM`` (deep-dive layer); now
    ``50-Inbox/03-Processed/YYYY-MM`` (raw intake layer, alongside
    article-processor output).  The change is BL-066: github intake
    no longer produces a "deep-dive" — the enriched body IS the
    processed source.
    """
    layout = VaultLayout.from_vault(vault_dir)
    month = datetime.now().strftime("%Y-%m")
    return layout.processed_dir / month


# ---------------------------------------------------------------------------
# Logging (kept minimal — pipeline_log gets the structured event)
# ---------------------------------------------------------------------------


class PipelineLogger:
    """Append-only JSONL logger for github intake events."""

    def __init__(self, log_file: Path):
        self.log_file = log_file
        self.session_id = (
            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()}"
        )

    def log(self, event_type: str, data: dict[str, Any]) -> None:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
            "event_type": event_type,
            **data,
        }
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Filename / frontmatter helpers
# ---------------------------------------------------------------------------


_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9_\-]+")


def _safe_segment(value: str) -> str:
    """Make ``owner`` or ``repo`` safe for a filename without losing the
    canonical github name.  Keep alphanumerics/underscore/hyphen, drop
    everything else."""
    out = _FILENAME_SAFE.sub("-", value).strip("-")
    return out or "unknown"


def _build_output_filename(date: str, owner: str, repo: str) -> str:
    """Match the article-processor convention: ``YYYY-MM-DD_<slug>.md``.

    No ``_深度解读`` suffix — this is a raw processed source, not a
    deep-dive.  Frontmatter ``source_type: github-project`` is what
    distinguishes it for absorb / index.
    """
    return f"{date}_{_safe_segment(owner)}_{_safe_segment(repo)}.md"


def _yaml_escape(value: Any) -> str:
    """Minimal YAML scalar escape — wrap in double-quotes when the
    value contains characters that would confuse a YAML parser."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    s = str(value)
    if not s:
        return '""'
    if any(c in s for c in ':#"\'\\\n[]{}'):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _build_frontmatter(
    *,
    title: str,
    url: str,
    owner: str,
    repo: str,
    date: str,
    tags: list[str],
    enriched: EnrichedSource,
    fetched_at: str,
) -> str:
    """Render frontmatter for the processed github markdown."""
    meta = enriched.metadata
    fields: list[tuple[str, Any]] = [
        ("title", title or f"{owner}/{repo}"),
        ("source", url),
        ("source_type", "github-project"),
        ("source_tier", enriched.tier),
        ("github_owner", owner),
        ("github_repo", repo),
    ]
    if "github_stars" in meta:
        fields.append(("github_stars", meta["github_stars"]))
    fields.append(("source_fetched_at", fetched_at))

    # Tier-specific metadata — flat, prefixed so they don't collide.
    if enriched.tier == "deepwiki":
        if meta.get("deepwiki_last_indexed"):
            fields.append(("source_indexed_at", meta["deepwiki_last_indexed"]))
        if meta.get("deepwiki_section_count") is not None:
            fields.append(("deepwiki_section_count", meta["deepwiki_section_count"]))
    elif enriched.tier == "gitingest":
        if meta.get("gitingest_commit"):
            fields.append(("gitingest_commit", meta["gitingest_commit"]))
        if meta.get("gitingest_file_count") is not None:
            fields.append(("gitingest_file_count", meta["gitingest_file_count"]))

    fields.append(("date", date))
    fields.append(("type", "raw"))
    if tags:
        fields.append(("tags", "[" + ", ".join(_yaml_escape(t) for t in tags) + "]"))

    lines = ["---"]
    for key, value in fields:
        if key == "tags":
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {_yaml_escape(value)}")
    lines.append("---")
    return "\n".join(lines)


def _build_body_header(enriched: EnrichedSource, url: str) -> str:
    """Short provenance preamble at the top of the body so a human
    reader can see at a glance which tier this came from."""
    lines = [
        f"# {enriched.owner}/{enriched.repo}",
        "",
        f"_Source: [{url}]({url})_",
        f"_Enrichment tier: **{enriched.tier}**_",
    ]
    meta = enriched.metadata
    if enriched.tier == "deepwiki":
        if meta.get("deepwiki_last_indexed"):
            lines.append(f"_DeepWiki last indexed: {meta['deepwiki_last_indexed']}_")
    elif enriched.tier == "gitingest":
        if meta.get("gitingest_commit"):
            lines.append(f"_GitIngest commit: `{meta['gitingest_commit'][:12]}`_")
        if meta.get("gitingest_file_count"):
            lines.append(f"_GitIngest files analyzed: {meta['gitingest_file_count']}_")
    if "github_stars" in meta:
        lines.append(f"_Stars: {meta['github_stars']}_")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core: process one URL
# ---------------------------------------------------------------------------


def process_single_repo(
    *,
    url: str,
    date: str,
    tags: list[str],
    description: str,
    output_dir: Path,
    logger: PipelineLogger | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Enrich a single GitHub URL and write the processed markdown.

    Returns a result dict with ``status`` ∈ {completed, skipped, error}.

    Behavioral changes from the pre-BL-066 implementation:
    - No LLM call (no ``llm_client`` parameter).
    - Output goes to ``50-Inbox/03-Processed/<YYYY-MM>/`` not
      ``20-Areas/Tools/Topics/<YYYY-MM>/``.
    - Filename has no ``_深度解读`` suffix.
    - On total enrichment failure (all 3 tiers empty), we still write
      a frontmatter-only stub so the source is tracked, but flag
      ``status=skipped`` and ``warning="empty_body"``.
    """
    result: dict[str, Any] = {
        "url": url,
        "status": "pending",
        "output_file": None,
        "tier": None,
        "error": None,
    }

    parsed = parse_github_url(url)
    if not parsed:
        result["status"] = "error"
        result["error"] = "Invalid GitHub URL"
        if logger:
            logger.log("github_intake_error", {"url": url, "error": result["error"]})
        return result
    owner, repo = parsed

    print(f"  Enriching {owner}/{repo} (tier 1 → 2 → 3) …")
    try:
        enriched = enrich_github_source(owner, repo)
    except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
        result["status"] = "error"
        result["error"] = f"enrich_github_source failed: {exc}"
        if logger:
            logger.log("github_intake_error", {"url": url, "error": result["error"]})
        return result

    result["tier"] = enriched.tier
    print(f"  → tier: {enriched.tier}, body: {len(enriched.body)} chars")

    if dry_run:
        result["status"] = "dry_run"
        result["output_file"] = "(dry run)"
        return result

    fetched_at = datetime.now(timezone.utc).isoformat()
    title = description.strip() if description else f"{owner}/{repo}"

    frontmatter = _build_frontmatter(
        title=title,
        url=url,
        owner=owner,
        repo=repo,
        date=date,
        tags=tags,
        enriched=enriched,
        fetched_at=fetched_at,
    )
    body_header = _build_body_header(enriched, url)

    if enriched.body.strip():
        full_content = f"{frontmatter}\n\n{body_header}\n{enriched.body}\n"
        result_status = "completed"
    else:
        # All tiers returned empty body — still record the source, but
        # mark as skipped so absorb won't try to extract from it.
        full_content = (
            f"{frontmatter}\n\n{body_header}\n"
            "_All enrichment tiers returned empty content. "
            "This source has been recorded but contains no extractable body._\n"
        )
        result_status = "skipped"
        result["error"] = "empty_body"

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _build_output_filename(date, owner, repo)
    output_path = output_dir / safe_name
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(full_content)

    result["status"] = result_status
    result["output_file"] = str(output_path)
    print(f"  ✓ Wrote: {output_path}")

    if logger:
        logger.log("github_intake_completed", {
            "url": url,
            "owner": owner,
            "repo": repo,
            "tier": enriched.tier,
            "body_chars": len(enriched.body),
            "output_file": str(output_path),
            "status": result_status,
        })
    return result


# ---------------------------------------------------------------------------
# Pinboard stub parsing (used by --process-single)
# ---------------------------------------------------------------------------


def _parse_pinboard_stub(path: Path) -> dict[str, Any] | None:
    """Read a pinboard-style stub and extract URL/title/date/tags.

    The pinboard intake writes frontmatter like::

        ---
        title: "owner/repo"
        source: https://github.com/owner/repo
        date: 2026-04-28
        tags: [github, agent]
        ---

    We do a light regex parse — the pinboard step writes deterministic
    frontmatter so we don't need a full YAML parser here.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    source_match = re.search(r"^source\s*:\s*(.+)$", content, re.MULTILINE)
    if not source_match:
        return None
    title_match = re.search(r'^title\s*:\s*"?([^"\n]+?)"?\s*$', content, re.MULTILINE)
    date_match = re.search(r"^date\s*:\s*(.+)$", content, re.MULTILINE)
    tags_match = re.search(r"^tags\s*:\s*\[(.+?)\]", content, re.MULTILINE)

    return {
        "url": source_match.group(1).strip().strip('"'),
        "title": title_match.group(1).strip() if title_match else "",
        "date": (
            date_match.group(1).strip() if date_match
            else datetime.now().strftime("%Y-%m-%d")
        ),
        "tags": (
            [t.strip().strip('"') for t in tags_match.group(1).split(",")]
            if tags_match else []
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="ovp-github",
        description=(
            "GitHub source enrichment (DeepWiki → GitIngest → README). "
            "Outputs to 50-Inbox/03-Processed/, NOT 20-Areas/Tools/Topics/."
        ),
    )
    parser.add_argument("--input", "-i", help="文件路径，每行一个 GitHub URL")
    parser.add_argument("--single", "-s", help="单个 GitHub URL")
    parser.add_argument(
        "--process-single", type=Path,
        help="处理单个 pinboard 书签文件，提取 URL 并跑 enrichment",
    )
    parser.add_argument("--vault-dir", type=Path, default=None)
    parser.add_argument(
        "--output-dir", "-o", type=Path, default=None,
        help="输出目录（默认：<vault>/50-Inbox/03-Processed/YYYY-MM/）",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", "-d", type=float, default=1.0)
    args = parser.parse_args()

    if not any([args.input, args.single, args.process_single]):
        parser.print_help()
        return 1

    layout = VaultLayout.from_vault(args.vault_dir or VAULT_DIR)
    load_env_file(layout.vault_dir)
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir else build_default_output_dir(layout.vault_dir)
    )
    logger = PipelineLogger(layout.pipeline_log)

    # Build URL list
    urls_to_process: list[dict[str, Any]] = []
    if args.single:
        urls_to_process.append({
            "url": args.single,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "tags": [],
            "description": "",
        })
    elif args.input:
        with open(args.input) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls_to_process.append({
                        "url": line,
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "tags": [],
                        "description": "",
                    })
    elif args.process_single:
        stub = _parse_pinboard_stub(args.process_single)
        if not stub:
            print(f"❌ 无法从文件提取 source URL: {args.process_single}")
            return 1
        urls_to_process.append({
            "url": stub["url"],
            "date": stub["date"],
            "tags": stub["tags"],
            "description": stub["title"],
        })

    print(f"\nProcessing {len(urls_to_process)} GitHub repositories…")
    print(f"Output: {output_dir}")
    print("=" * 60)

    results: list[dict[str, Any]] = []
    for i, item in enumerate(urls_to_process, 1):
        print(f"\n[{i}/{len(urls_to_process)}] {item['url']}")
        result = process_single_repo(
            url=item["url"],
            date=item["date"],
            tags=item["tags"],
            description=item["description"],
            output_dir=output_dir,
            logger=logger,
            dry_run=args.dry_run,
        )
        maybe_archive_pinboard_process_single(
            layout,
            (
                args.process_single
                if args.process_single and len(urls_to_process) == 1 else None
            ),
            result,
            dry_run=args.dry_run,
        )
        results.append(result)
        if i < len(urls_to_process):
            time.sleep(args.delay)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    by_status = {"completed": 0, "skipped": 0, "error": 0, "dry_run": 0}
    by_tier = {"deepwiki": 0, "gitingest": 0, "readme": 0, None: 0}
    for r in results:
        by_status[r.get("status", "error")] = by_status.get(r.get("status", "error"), 0) + 1
        by_tier[r.get("tier")] = by_tier.get(r.get("tier"), 0) + 1
    print(f"Total: {len(results)}")
    for status, n in by_status.items():
        if n:
            print(f"  {status:10s}: {n}")
    print("Tier breakdown:")
    for tier in ("deepwiki", "gitingest", "readme"):
        print(f"  {tier:10s}: {by_tier.get(tier, 0)}")

    return 0 if by_status["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
