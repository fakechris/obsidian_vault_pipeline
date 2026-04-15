from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
import fcntl
import time
from typing import Iterator

import yaml


def resolve_vault_dir(vault_dir: Path | str | None = None) -> Path:
    """Resolve the vault directory once and use the absolute path everywhere."""
    base = Path.cwd() if vault_dir is None else Path(vault_dir)
    return base.expanduser().resolve()


def is_hidden_path(path: Path) -> bool:
    """Return True only for actual hidden path parts, not '.' or '..'."""
    return any(part.startswith(".") and part not in {".", ".."} for part in path.parts)


def iter_markdown_files(directory: Path, recursive: bool = True) -> Iterator[Path]:
    """Yield markdown files while skipping actual hidden files and directories."""
    pattern = "**/*.md" if recursive else "*.md"
    for md_file in directory.glob(pattern):
        if is_hidden_path(md_file):
            continue
        yield md_file


def read_markdown_frontmatter(path: Path) -> dict[str, object]:
    """Return parsed YAML frontmatter for a markdown file, if present."""
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    return yaml.safe_load(parts[1]) or {}


def markdown_title(path: Path) -> str:
    """Return a markdown file title from frontmatter, falling back to the stem."""
    metadata = read_markdown_frontmatter(path)
    title = metadata.get("title")
    return str(title) if title else path.stem


@dataclass(frozen=True)
class VaultLayout:
    vault_dir: Path

    @classmethod
    def from_vault(cls, vault_dir: Path | str | None = None) -> "VaultLayout":
        return cls(resolve_vault_dir(vault_dir))

    @property
    def logs_dir(self) -> Path:
        return self.vault_dir / "60-Logs"

    @property
    def pipeline_log(self) -> Path:
        return self.logs_dir / "pipeline.jsonl"

    @property
    def knowledge_db(self) -> Path:
        return self.logs_dir / "knowledge.db"

    @property
    def knowledge_db_lock(self) -> Path:
        return self.logs_dir / "knowledge.db.lock"

    @property
    def action_worker_lock(self) -> Path:
        return self.logs_dir / "action-worker.lock"

    @property
    def transactions_dir(self) -> Path:
        return self.logs_dir / "transactions"

    @property
    def derived_dir(self) -> Path:
        return self.logs_dir / "derived"

    @property
    def extraction_runs_dir(self) -> Path:
        return self.derived_dir / "extraction-runs"

    @property
    def review_queue_dir(self) -> Path:
        return self.derived_dir / "review-queue"

    @property
    def compiled_views_dir(self) -> Path:
        return self.derived_dir / "compiled-views"

    @property
    def pipeline_reports_dir(self) -> Path:
        return self.logs_dir / "pipeline-reports"

    @property
    def link_resolution_dir(self) -> Path:
        return self.logs_dir / "link-resolution"

    @property
    def daily_delta_dir(self) -> Path:
        return self.logs_dir / "daily-deltas"

    @property
    def quality_reports_dir(self) -> Path:
        return self.logs_dir / "quality-reports"

    @property
    def raw_dir(self) -> Path:
        return self.vault_dir / "50-Inbox" / "01-Raw"

    @property
    def clippings_dir(self) -> Path:
        return self.vault_dir / "Clippings"

    @property
    def pinboard_dir(self) -> Path:
        return self.vault_dir / "50-Inbox" / "02-Pinboard"

    @property
    def processing_dir(self) -> Path:
        return self.vault_dir / "50-Inbox" / "02-Processing"

    @property
    def processed_dir(self) -> Path:
        return self.vault_dir / "50-Inbox" / "03-Processed"

    def processed_month_dir(self, when: datetime | None = None) -> Path:
        month = (when or datetime.now()).strftime("%Y-%m")
        return self.processed_dir / month

    @property
    def evergreen_dir(self) -> Path:
        return self.vault_dir / "10-Knowledge" / "Evergreen"

    @property
    def atlas_dir(self) -> Path:
        return self.vault_dir / "10-Knowledge" / "Atlas"

    @property
    def papers_dir(self) -> Path:
        return self.vault_dir / "20-Areas" / "AI-Research" / "Papers"

    @property
    def queries_dir(self) -> Path:
        return self.vault_dir / "20-Areas" / "Queries"

    @property
    def pinboard_archive_dir(self) -> Path:
        return self.vault_dir / "70-Archive" / "Pinboard"

    def month_topics_dir(self, area: str, when: datetime | None = None) -> Path:
        month = (when or datetime.now()).strftime("%Y-%m")
        return self.vault_dir / "20-Areas" / area / "Topics" / month

    def classification_output_dir(self, classification: str, when: datetime | None = None) -> Path:
        mapping = {
            "ai": "AI-Research",
            "tools": "Tools",
            "investing": "Investing",
            "programming": "Programming",
        }
        area = mapping.get(classification, "AI-Research")
        return self.month_topics_dir(area, when=when)


@contextmanager
def advisory_file_lock(
    path: Path,
    *,
    timeout_seconds: float | None = 300.0,
    poll_interval_seconds: float = 0.1,
) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    with path.open("a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for lock: {path}")
                time.sleep(poll_interval_seconds)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def knowledge_db_write_lock(
    vault_dir: Path | str | None = None,
    *,
    timeout_seconds: float | None = 300.0,
) -> Iterator[None]:
    layout = VaultLayout.from_vault(vault_dir)
    with advisory_file_lock(layout.knowledge_db_lock, timeout_seconds=timeout_seconds):
        yield
