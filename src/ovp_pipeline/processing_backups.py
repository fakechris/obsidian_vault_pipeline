"""Cleanup helpers for image-downloader backups left in 02-Processing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .runtime import VaultLayout


IMAGE_MARKDOWN_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
HTML_IMAGE_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)


@dataclass(frozen=True)
class BackupCleanupCheck:
    backup_path: Path
    processed_path: Path | None
    ok: bool
    reason: str


def _normalize_source_text(content: str) -> str:
    """Normalize source text while ignoring image-link rewrites."""

    content = IMAGE_MARKDOWN_RE.sub("", content)
    content = HTML_IMAGE_RE.sub("", content)
    content = re.sub(r"\s+", " ", content)
    return content.strip()


def _body_without_frontmatter(content: str) -> str:
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
    return content


def processing_backup_for_source(source_path: Path) -> Path:
    return source_path.with_suffix(".md.backup")


def build_processed_source_lookup(layout: VaultLayout) -> dict[str, list[Path]]:
    lookup: dict[str, list[Path]] = {}
    for path in layout.processed_dir.rglob("*.md"):
        lookup.setdefault(path.name, []).append(path)
    return {name: sorted(paths) for name, paths in lookup.items()}


def find_processed_source_for_backup(
    layout: VaultLayout,
    backup_path: Path,
    *,
    processed_lookup: dict[str, list[Path]] | None = None,
) -> Path | None:
    source_name = backup_path.name.removesuffix(".backup")
    lookup = processed_lookup if processed_lookup is not None else build_processed_source_lookup(layout)
    exact_matches = lookup.get(source_name, [])
    if len(exact_matches) == 1:
        return exact_matches[0]
    return None


def verify_processing_backup_covered(
    backup_path: Path,
    processed_path: Path | None,
) -> BackupCleanupCheck:
    if not backup_path.exists():
        return BackupCleanupCheck(backup_path, processed_path, False, "backup_missing")
    if backup_path.suffix != ".backup" or not backup_path.name.endswith(".md.backup"):
        return BackupCleanupCheck(backup_path, processed_path, False, "not_markdown_backup")
    if processed_path is None:
        return BackupCleanupCheck(backup_path, processed_path, False, "processed_match_missing")
    if not processed_path.exists():
        return BackupCleanupCheck(backup_path, processed_path, False, "processed_missing")
    if processed_path.suffix != ".md":
        return BackupCleanupCheck(backup_path, processed_path, False, "processed_not_markdown")

    try:
        backup_text = backup_path.read_text(encoding="utf-8")
        processed_text = processed_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return BackupCleanupCheck(backup_path, processed_path, False, "decode_error")

    if not backup_text.strip():
        return BackupCleanupCheck(backup_path, processed_path, False, "backup_empty")
    if not processed_text.strip():
        return BackupCleanupCheck(backup_path, processed_path, False, "processed_empty")

    normalized_backup = _normalize_source_text(_body_without_frontmatter(backup_text))
    normalized_processed = _normalize_source_text(_body_without_frontmatter(processed_text))
    if not normalized_backup:
        return BackupCleanupCheck(backup_path, processed_path, False, "backup_no_text_after_images")
    if normalized_backup != normalized_processed:
        return BackupCleanupCheck(backup_path, processed_path, False, "content_mismatch")

    return BackupCleanupCheck(backup_path, processed_path, True, "covered_by_processed")


def cleanup_processing_backup_for_archived_source(source_path: Path, archived_path: Path) -> BackupCleanupCheck:
    backup_path = processing_backup_for_source(source_path)
    check = verify_processing_backup_covered(backup_path, archived_path)
    if check.ok:
        backup_path.unlink()
    return check


def iter_orphan_processing_backups(layout: VaultLayout) -> list[Path]:
    backups = sorted(layout.processing_dir.glob("*.md.backup"))
    return [backup for backup in backups if not backup.with_suffix("").exists()]


def cleanup_orphan_processing_backups(
    layout: VaultLayout,
    *,
    apply: bool = False,
) -> list[BackupCleanupCheck]:
    checks: list[BackupCleanupCheck] = []
    processed_lookup = build_processed_source_lookup(layout)
    for backup_path in iter_orphan_processing_backups(layout):
        processed_path = find_processed_source_for_backup(
            layout,
            backup_path,
            processed_lookup=processed_lookup,
        )
        check = verify_processing_backup_covered(backup_path, processed_path)
        if apply and check.ok:
            backup_path.unlink()
        checks.append(check)
    return checks
