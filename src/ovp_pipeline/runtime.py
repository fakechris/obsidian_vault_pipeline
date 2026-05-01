from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager
import fcntl
import os
import time
from typing import Any, Iterable, Iterator

import yaml


def resolve_vault_dir(vault_dir: Path | str | None = None) -> Path:
    """Resolve the vault directory once and use the absolute path everywhere."""
    if vault_dir is None:
        vault_dir = os.environ.get("OVP_VAULT_DIR") or os.environ.get("VAULT_DIR")
    base = Path.cwd() if vault_dir is None else Path(vault_dir)
    return base.expanduser().resolve()


def looks_like_vault_dir(vault_dir: Path | str) -> bool:
    """Return True when the path has the core OVP/Obsidian vault layout."""
    base = resolve_vault_dir(vault_dir)
    has_core_dirs = (
        (base / "10-Knowledge").is_dir()
        and (base / "20-Areas").is_dir()
        and (base / "50-Inbox").is_dir()
    )
    has_vault_marker = (
        (base / ".obsidian").is_dir()
        or ((base / "Index.md").is_file() and (base / "Log.md").is_file())
    )
    is_package_checkout = (
        (base / "pyproject.toml").is_file()
        and (base / "src" / "ovp_pipeline").is_dir()
    )
    return has_core_dirs and has_vault_marker and not is_package_checkout


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
    metadata, _body = split_markdown_frontmatter(content)
    return metadata


def split_markdown_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Return YAML frontmatter and body for standard or fenced markdown notes."""
    raw_frontmatter = ""
    body = text
    if text.startswith("```yaml\n---\n") or text.startswith("```yml\n---\n"):
        first_newline = text.find("\n")
        closing = "\n---\n```"
        end = text.find(closing, first_newline + 1)
        if end < 0:
            return {}, text
        raw_frontmatter = text[first_newline + len("\n---\n") : end]
        body = text[end + len(closing) :].lstrip("\n")
    elif text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end < 0:
            return {}, text
        raw_frontmatter = text[4:end]
        body = text[end + len("\n---") :].lstrip("\n")
    else:
        return {}, text
    parsed = yaml.safe_load(raw_frontmatter) or {}
    if not isinstance(parsed, dict):
        return {}, body
    return parsed, body


def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


def format_utc_timestamp(value: datetime) -> str:
    """Return stable second-precision UTC timestamps for projections and logs."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_utc_iso(value: datetime | None) -> str:
    """Return ISO 8601 UTC timestamp, or empty string for None."""
    if value is None:
        return ""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc_timestamp(value: object) -> datetime | None:
    """Parse an ISO 8601 or Z-suffixed UTC timestamp, returning None on failure."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


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
    def signals_log(self) -> Path:
        return self.logs_dir / "signals.jsonl"

    @property
    def signals_log_lock(self) -> Path:
        return self.logs_dir / "signals.jsonl.lock"

    @property
    def actions_log(self) -> Path:
        return self.logs_dir / "actions.jsonl"

    @property
    def actions_log_lock(self) -> Path:
        return self.logs_dir / "actions.jsonl.lock"

    @property
    def action_worker_lock(self) -> Path:
        return self.logs_dir / "action-worker.lock"

    @property
    def action_worker_state(self) -> Path:
        return self.logs_dir / "action-worker.json"

    @property
    def workflow_lock(self) -> Path:
        return self.logs_dir / "workflow.lock"

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
    def stage_artifacts_dir(self) -> Path:
        return self.logs_dir / "stage-artifacts"

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


@contextmanager
def signal_ledger_write_lock(
    vault_dir: Path | str | None = None,
    *,
    timeout_seconds: float | None = 300.0,
) -> Iterator[None]:
    layout = VaultLayout.from_vault(vault_dir)
    with advisory_file_lock(layout.signals_log_lock, timeout_seconds=timeout_seconds):
        yield


@contextmanager
def action_queue_write_lock(
    vault_dir: Path | str | None = None,
    *,
    timeout_seconds: float | None = 300.0,
) -> Iterator[None]:
    layout = VaultLayout.from_vault(vault_dir)
    with advisory_file_lock(layout.actions_log_lock, timeout_seconds=timeout_seconds):
        yield


@contextmanager
def vault_workflow_lock(
    vault_dir: Path | str | None = None,
    *,
    timeout_seconds: float | None = 300.0,
) -> Iterator[None]:
    layout = VaultLayout.from_vault(vault_dir)
    with advisory_file_lock(layout.workflow_lock, timeout_seconds=timeout_seconds):
        yield


# ---------------------------------------------------------------------------
# JSONL rotation & bounded-read utilities
# ---------------------------------------------------------------------------

JSONL_DEFAULT_MAX_LINES = 10_000


def _count_lines_fast(path: Path) -> int:
    """Count non-empty lines without parsing JSON."""
    if not path.exists():
        return 0
    count = 0
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.strip():
                    count += 1
    except OSError:
        return 0
    return count


def _write_sidecar_stats(
    archived_path: Path,
    *,
    original_name: str,
    line_count: int,
    event_types: Counter[str],
    first_ts: str,
    last_ts: str,
) -> Path:
    sidecar_path = archived_path.with_suffix(".stats.json")
    stats = {
        "archived_from": original_name,
        "archived_at": format_utc_iso(utc_now()),
        "line_count": line_count,
        "event_types": dict(event_types.most_common()),
        "first_ts": first_ts,
        "last_ts": last_ts,
    }
    sidecar_path.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return sidecar_path


def rotate_jsonl_if_needed(
    path: Path,
    *,
    max_lines: int = JSONL_DEFAULT_MAX_LINES,
) -> Path | None:
    """Rotate *path* when it exceeds *max_lines*.

    Returns the archived path on rotation, or ``None`` if no rotation occurred.
    Writes a ``{stem}.{timestamp}.stats.json`` sidecar with aggregate metadata.
    """
    if not path.exists():
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size < max_lines * 20:
        return None
    line_count = _count_lines_fast(path)
    if line_count < max_lines:
        return None

    original_name = path.name
    event_types: Counter[str] = Counter()
    first_ts = ""
    last_ts = ""
    for obj in iter_jsonl(path):
        et = str(obj.get("event_type") or obj.get("type") or "")
        if et:
            event_types[et] += 1
        ts = str(obj.get("ts") or obj.get("timestamp") or "")
        if ts:
            if not first_ts:
                first_ts = ts
            last_ts = ts

    ts_suffix = utc_now().strftime("%Y%m%d-%H%M%S")
    archived = path.with_name(f"{path.stem}.{ts_suffix}.jsonl")
    seq = 0
    while archived.exists():
        seq += 1
        archived = path.with_name(f"{path.stem}.{ts_suffix}-{seq}.jsonl")
    path.rename(archived)
    _write_sidecar_stats(
        archived,
        original_name=original_name,
        line_count=line_count,
        event_types=event_types,
        first_ts=first_ts,
        last_ts=last_ts,
    )
    return archived


def append_jsonl(
    path: Path,
    payload: dict[str, Any],
    *,
    max_lines: int = JSONL_DEFAULT_MAX_LINES,
) -> None:
    """Append a JSON object to *path*, rotating beforehand if the file is large.

    Holds an advisory lock around the rotate-then-append sequence so
    concurrent writers cannot race on path.rename().
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / (path.name + ".append.lock")
    with advisory_file_lock(lock_path, timeout_seconds=30.0):
        rotate_jsonl_if_needed(path, max_lines=max_lines)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def iter_jsonl(
    path: Path,
    *,
    tail_lines: int | None = None,
) -> Iterable[dict[str, Any]]:
    """Yield parsed dicts from a JSONL file.

    When *tail_lines* is set, only the last N non-empty lines are parsed.
    This avoids reading multi-megabyte historic logs when only recent data
    is needed (e.g. for runtime-state summaries).
    """
    if not path.exists():
        return

    if tail_lines is not None and tail_lines > 0:
        yield from _iter_jsonl_tail(path, tail_lines)
        return

    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def _iter_jsonl_tail(path: Path, n: int) -> Iterable[dict[str, Any]]:
    """Read approximately the last *n* non-empty lines from *path*.

    Uses a seek-from-end strategy: reads blocks backwards until enough
    lines are collected, then parses only those lines.
    """
    block_size = 8192
    lines: list[str] = []
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size == 0:
        return

    with path.open("rb") as fh:
        offset = size
        leftover = b""
        while len(lines) < n + 1 and offset > 0:
            read_size = min(block_size, offset)
            offset -= read_size
            fh.seek(offset)
            chunk = fh.read(read_size) + leftover
            parts = chunk.split(b"\n")
            leftover = parts[0]
            for part in reversed(parts[1:]):
                stripped = part.strip()
                if stripped:
                    lines.append(stripped.decode("utf-8", errors="ignore"))
                    if len(lines) >= n + 1:
                        break
        if leftover.strip():
            lines.append(leftover.strip().decode("utf-8", errors="ignore"))

    for raw in reversed(lines[:n]):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj


def _rotated_segments(path: Path) -> list[Path]:
    """Return rotated archive files for *path* sorted chronologically (oldest first)."""
    stem = path.stem
    parent = path.parent
    if not parent.is_dir():
        return []
    segments = sorted(
        [
            p
            for p in parent.iterdir()
            if p.name.startswith(f"{stem}.") and p.suffix == ".jsonl" and p.name != path.name
        ],
        key=lambda p: p.name,
    )
    return segments


def read_sidecar_stats(sidecar: Path) -> dict[str, Any]:
    """Parse a ``.stats.json`` sidecar written during rotation."""
    try:
        return json.loads(sidecar.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def sidecar_aggregate(path: Path) -> dict[str, int]:
    """Sum ``line_count`` from all sidecar stats files for *path*.

    Returns ``{"total_archived_lines": <int>}`` which callers can add to
    a live-file count for an accurate grand total.
    """
    total = 0
    parent = path.parent
    stem = path.stem
    if not parent.is_dir():
        return {"total_archived_lines": 0}
    for sidecar in parent.iterdir():
        if sidecar.name.startswith(f"{stem}.") and sidecar.name.endswith(".stats.json"):
            stats = read_sidecar_stats(sidecar)
            total += int(stats.get("line_count") or 0)
    return {"total_archived_lines": total}


def iter_jsonl_with_rotated(path: Path) -> Iterable[dict[str, Any]]:
    """Yield events from all rotated segments + live file in chronological order."""
    for segment in _rotated_segments(path):
        yield from iter_jsonl(segment)
    yield from iter_jsonl(path)
