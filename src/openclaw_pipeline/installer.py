from __future__ import annotations

import argparse
import os
import shlex
import stat
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

PACKAGE_NAME = "obsidian-vault-pipeline"


def load_project_scripts(project_root: Path) -> dict[str, str]:
    toml_module = _load_toml_module()
    pyproject_path = project_root / "pyproject.toml"
    with pyproject_path.open("rb") as fh:
        data = toml_module.load(fh)
    scripts = data.get("project", {}).get("scripts", {})
    return {
        name: entry_point
        for name, entry_point in scripts.items()
        if name.startswith("ovp")
    }


def load_distribution_scripts(distribution_name: str = PACKAGE_NAME) -> dict[str, str]:
    dist = distribution(distribution_name)
    scripts: dict[str, str] = {}
    for entry_point in dist.entry_points:
        if entry_point.group == "console_scripts" and entry_point.name.startswith("ovp"):
            scripts[entry_point.name] = entry_point.value
    return scripts


def choose_install_bin_dir(path_env: str | None = None, home_dir: Path | None = None) -> Path:
    home = home_dir or Path.home()
    path_value = path_env if path_env is not None else os.environ.get("PATH", "")
    path_entries = _parse_path_entries(path_value)

    preferred_bins = [
        home / ".local" / "bin",
        home / "bin",
    ]
    preferred_lookup = {path.resolve(strict=False) for path in preferred_bins}
    path_lookup = {path.resolve(strict=False) for path in path_entries}

    for preferred in preferred_bins:
        if preferred.resolve(strict=False) in path_lookup and _is_writable_dir(preferred):
            return preferred

    for candidate in _curated_path_candidates(path_entries, home):
        if _is_writable_dir(candidate):
            return candidate

    return preferred_bins[0]


def write_shims(
    target_dir: Path,
    scripts: dict[str, str],
    python_executable: str,
) -> list[Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for script_name, entry_point in sorted(scripts.items()):
        module_name, func_name = _split_entry_point(entry_point)
        wrapper_path = target_dir / script_name
        wrapper_body = _build_wrapper(
            python_executable=python_executable,
            module_name=module_name,
            func_name=func_name,
        )
        wrapper_path.write_text(wrapper_body, encoding="utf-8")
        current_mode = wrapper_path.stat().st_mode
        wrapper_path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        created.append(wrapper_path)
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install OVP command shims into a user-visible bin directory.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        help="Repository root containing pyproject.toml. Used as a fallback when distribution metadata is unavailable.",
    )
    parser.add_argument(
        "--distribution",
        default=PACKAGE_NAME,
        help=f"Installed distribution name to inspect. Defaults to {PACKAGE_NAME}.",
    )
    parser.add_argument(
        "--bin-dir",
        type=Path,
        help="Explicit directory to place command shims into.",
    )
    parser.add_argument(
        "--python-executable",
        default=sys.executable,
        help="Python executable used inside generated shims.",
    )
    args = parser.parse_args(argv)

    try:
        scripts = load_distribution_scripts(args.distribution)
    except PackageNotFoundError:
        if args.project_root is None:
            parser.error(
                f"Distribution {args.distribution!r} is not installed and --project-root was not provided."
            )
        scripts = load_project_scripts(args.project_root)

    target_dir = args.bin_dir or choose_install_bin_dir()
    created = write_shims(
        target_dir=target_dir,
        scripts=scripts,
        python_executable=args.python_executable,
    )
    print(f"Installed {len(created)} OVP command shims into {target_dir}")
    if str(target_dir) not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"Note: {target_dir} is not currently on PATH.")
    return 0


def _parse_path_entries(path_env: str) -> list[Path]:
    entries: list[Path] = []
    seen: set[Path] = set()
    for raw_entry in path_env.split(os.pathsep):
        if not raw_entry:
            continue
        path = Path(raw_entry).expanduser()
        resolved = path.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        entries.append(path)
    return entries


def _curated_path_candidates(path_entries: list[Path], home_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    preferred_system_bins = [
        Path("/usr/local/bin"),
        Path("/opt/homebrew/bin"),
    ]
    for candidate in preferred_system_bins:
        if candidate in path_entries:
            candidates.append(candidate)

    for candidate in path_entries:
        if candidate in candidates:
            continue
        resolved = candidate.resolve(strict=False)
        if _looks_like_virtualenv_bin(candidate):
            continue
        if home_dir in resolved.parents and candidate.name == "bin":
            candidates.append(candidate)
            continue
        if candidate in preferred_system_bins:
            candidates.append(candidate)
    return candidates


def _looks_like_virtualenv_bin(path: Path) -> bool:
    text = str(path)
    markers = (".venv/", "virtualenv", "conda", "miniconda", "/envs/")
    return any(marker in text for marker in markers)


def _is_writable_dir(path: Path) -> bool:
    return path.is_dir() and os.access(path, os.W_OK | os.X_OK)


def _split_entry_point(entry_point: str) -> tuple[str, str]:
    module_name, _, func_name = entry_point.partition(":")
    if not module_name or not func_name:
        raise ValueError(f"Unsupported entry point: {entry_point!r}")
    return module_name, func_name


def _build_wrapper(python_executable: str, module_name: str, func_name: str) -> str:
    python_code = (
        f"from {module_name} import {func_name} as _entry; "
        "raise SystemExit(_entry())"
    )
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'exec "{python_executable}" -c {shlex.quote(python_code)} "$@"\n'
    )


def _load_toml_module():
    try:
        import tomllib

        return tomllib
    except ModuleNotFoundError:
        try:
            import tomli

            return tomli
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Reading pyproject.toml requires tomllib (Python 3.11+) or tomli."
            ) from exc


if __name__ == "__main__":
    raise SystemExit(main())
