"""ovp-init project — Phase 34 project skeleton scaffolder.

Copies ``90-Templates/Project-Skeleton/`` into ``30-Projects/<name>/``,
substituting ``{project_name}`` in template bodies. The four accepted-state
files (README/Plan/Roadmap/Decisions) come pre-stamped with
``state: accepted`` frontmatter so the lint zone-boundary check has a baseline
mtime/audit pair.
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

from ..promotion_audit import emit_promotion
from ..runtime import resolve_vault_dir
from ..state_lifecycle import State


_SKELETON_REL = Path("90-Templates/Project-Skeleton")
_VALID_PROJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SUBSTITUTE_FILES = {
    "README.md",
    "Plan.md",
    "Roadmap.md",
    "Decisions.md",
    "OVP-Suggestions.md",
}
_ACCEPTED_FILES = {
    "README.md",
    "Plan.md",
    "Roadmap.md",
    "Decisions.md",
}


def _copy_skeleton(src: Path, dst: Path, *, project_name: str) -> list[Path]:
    if not src.exists():
        raise FileNotFoundError(
            f"Project skeleton not found at {src}. "
            "Reinstall the package or restore 90-Templates/Project-Skeleton/."
        )
    if dst.exists():
        raise FileExistsError(f"Project already exists at {dst}")

    shutil.copytree(src, dst)
    touched: list[Path] = []
    for path in dst.rglob("*"):
        if path.is_file() and path.name in _SUBSTITUTE_FILES:
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace("{project_name}", project_name), encoding="utf-8")
            touched.append(path)
    return touched


def _emit_baseline_audit(vault_dir: Path, *, project_name: str, files: list[Path]) -> None:
    """Drop a single ``promotion`` event covering the freshly-created accepted
    files so the Phase 34 lint mtime check doesn't fire on an empty project."""
    for path in files:
        emit_promotion(
            vault_dir,
            pack="default-knowledge",
            from_state=State.DRAFT,
            to_state=State.ACCEPTED,
            target_path=path,
            actor=f"ovp-init project ({project_name})",
            reason="initial_scaffold",
            payload={"project_name": project_name},
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 34 project scaffold")
    sub = parser.add_subparsers(dest="command", required=True)

    project = sub.add_parser("project", help="Create a new project under 30-Projects/")
    project.add_argument("name", help="Project directory name (e.g. 'demo')")
    project.add_argument("--vault-dir", type=Path, default=None)
    project.add_argument(
        "--no-audit",
        action="store_true",
        help="Skip the baseline promotion audit emission (test/CI use only)",
    )
    project.set_defaults(func=_cmd_project)

    args = parser.parse_args(argv)
    return int(args.func(args))


def _cmd_project(args: argparse.Namespace) -> int:
    name = args.name
    if not _VALID_PROJECT_NAME.fullmatch(name):
        raise SystemExit(
            f"Invalid project name {name!r}. "
            "Use only letters, digits, '.', '_', '-' and start with an alphanumeric."
        )
    vault_dir = resolve_vault_dir(args.vault_dir)
    src = vault_dir / _SKELETON_REL
    projects_root = (vault_dir / "30-Projects").resolve()
    dst = (projects_root / name).resolve()
    try:
        dst.relative_to(projects_root)
    except ValueError as exc:
        raise SystemExit(f"Project name {name!r} escapes 30-Projects/") from exc
    if dst == projects_root:
        raise SystemExit("Project name must not be empty or '.'")
    touched = _copy_skeleton(src, dst, project_name=name)
    if not args.no_audit:
        accepted = [path for path in touched if path.name in _ACCEPTED_FILES]
        _emit_baseline_audit(vault_dir, project_name=name, files=accepted)
    print(f"Created project: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
