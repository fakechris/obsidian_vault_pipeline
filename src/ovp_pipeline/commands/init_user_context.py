"""``ovp-init-user-context`` — scaffold USER.md + OVP_RULES.md into a vault.

Both files are M20 / BL-075 prerequisites: they let every LLM call
site that uses ``context_loader.load_llm_context`` adapt to the
operator's voice + autonomous-action policy.  Templates ship under
``src/ovp_pipeline/data/`` and are copied into the vault on demand.

Idempotent: skips files that already exist unless ``--force`` is
passed.  Never overwrites edited content silently.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from importlib.resources import files
from pathlib import Path

from ..context_loader import RULES_REL, USER_PROFILE_REL


def _template_path(name: str) -> Path:
    return Path(str(files("ovp_pipeline") / "data" / name))


def _copy_template(template_name: str, target: Path, force: bool) -> str:
    """Copy a template into ``target``.  Returns a status string for
    the CLI output."""
    if target.exists() and not force:
        return f"skip   {target} (already exists; pass --force to overwrite)"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_template_path(template_name), target)
    return f"write  {target}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Scaffold 00-Polaris/USER.md and OVP_RULES.md into a "
            "vault so M20 LLM call sites can read user context."
        ),
    )
    parser.add_argument(
        "--vault-dir", required=True, type=Path,
        help="Vault root directory.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite existing files (default: skip if present).",
    )
    args = parser.parse_args(argv)

    vault = args.vault_dir.expanduser().resolve()
    if not vault.exists():
        print(f"error: vault dir does not exist: {vault}", file=sys.stderr)
        return 2

    results = [
        _copy_template(
            "user_profile_template.md",
            vault / USER_PROFILE_REL,
            args.force,
        ),
        _copy_template(
            "rules_template.md",
            vault / RULES_REL,
            args.force,
        ),
    ]
    for line in results:
        print(line)
    print()
    print(
        "Next: edit 00-Polaris/USER.md to fill in identity + current "
        "focus.  OVP_RULES.md is sane out of the box — edit only when "
        "your tolerance for autonomous behaviour changes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
