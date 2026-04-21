from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any


PIPELINE_PROCESS_MARKERS = (
    "ovp_pipeline.unified_pipeline_enhanced",
    "ovp_pipeline.auto_article_processor",
    "ovp_pipeline.batch_quality_checker",
    "ovp_pipeline.commands.absorb",
    "ovp_pipeline.auto_moc_updater",
    "ovp_pipeline.commands.knowledge_index",
    "ovp_pipeline.clippings_processor",
    "ovp_pipeline.auto_github_processor",
    "ovp_pipeline.auto_paper_processor",
    "pinboard-processor.py",
    "/bin/ovp ",
    "/bin/ovp-",
)
DAEMON_PROCESS_MARKERS = (
    "ovp_pipeline.autopilot.daemon",
    "/bin/ovp-autopilot ",
)
ACTION_WORKER_PROCESS_MARKERS = ("ovp_pipeline.commands.run_actions",)
OBSERVER_PROCESS_MARKERS = (
    "/bin/ovp-ui ",
    "/bin/ovp-watch-progress ",
)


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, _ = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, remaining_minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h" if remaining_minutes == 0 else f"{hours}h {remaining_minutes}m"
    days, remaining_hours = divmod(hours, 24)
    return f"{days}d" if remaining_hours == 0 else f"{days}d {remaining_hours}h"


def _parse_elapsed_seconds(raw_elapsed: str) -> int | None:
    value = raw_elapsed.strip()
    if not value:
        return None
    days = 0
    if "-" in value:
        day_text, value = value.split("-", 1)
        try:
            days = int(day_text)
        except ValueError:
            return None
    parts = value.split(":")
    try:
        if len(parts) == 2:
            hours = 0
            minutes, seconds = [int(part) for part in parts]
        elif len(parts) == 3:
            hours, minutes, seconds = [int(part) for part in parts]
        else:
            return None
    except ValueError:
        return None
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _classify_process(command: str) -> str | None:
    if any(marker in command for marker in OBSERVER_PROCESS_MARKERS):
        return "observer"
    if any(marker in command for marker in ACTION_WORKER_PROCESS_MARKERS):
        return "action_worker"
    if any(marker in command for marker in DAEMON_PROCESS_MARKERS):
        return "daemon"
    if any(marker in command for marker in PIPELINE_PROCESS_MARKERS):
        return "one_shot"
    return None


def _args_summary(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if not parts:
        return ""
    try:
        ovp_index = next(
            _index
            for _index, part in enumerate(parts)
            if Path(part).name == "ovp" or Path(part).name.startswith("ovp-")
        )
        args = parts[ovp_index + 1 :]
    except StopIteration:
        args = parts[1:] if len(parts) > 1 else []
    filtered: list[str] = []
    skip_next = False
    for _index, part in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if part == "--vault-dir":
            skip_next = True
            continue
        if part.startswith("--vault-dir="):
            continue
        filtered.append(part)
        if len(" ".join(filtered)) > 140:
            break
    return " ".join(filtered)


def detect_runtime_processes(
    vault_dir: Path | str, *, include_observers: bool = False
) -> list[dict[str, Any]]:
    resolved_vault = Path(vault_dir).resolve()
    ps_executable = shutil.which("ps")
    if ps_executable is None:
        return []
    try:
        result = subprocess.run(
            [ps_executable, "-eo", "pid=,etime=,stat=,command="],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []

    vault_token = str(resolved_vault)
    processes: list[dict[str, Any]] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if vault_token not in line:
            continue
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid_text, elapsed_raw, state, command = parts
        process_kind = _classify_process(command)
        if process_kind is None:
            continue
        if process_kind == "observer" and not include_observers:
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        elapsed_seconds = _parse_elapsed_seconds(elapsed_raw)
        processes.append(
            {
                "pid": pid,
                "process_kind": process_kind,
                "state": state,
                "elapsed_raw": elapsed_raw,
                "elapsed_seconds": elapsed_seconds,
                "elapsed_summary": _format_duration(elapsed_seconds)
                if elapsed_seconds is not None
                else elapsed_raw,
                "args_summary": _args_summary(command),
                "command": command,
            }
        )
    return processes


def detect_runtime_process_lines(vault_dir: Path | str) -> list[str]:
    return [str(item["command"]) for item in detect_runtime_processes(vault_dir)]
