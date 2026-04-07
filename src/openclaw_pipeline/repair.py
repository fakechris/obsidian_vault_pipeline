"""Compatibility wrapper for the canonical repair command implementation."""

from __future__ import annotations

from .commands.repair import (
    get_vault_dir,
    main,
    print_report,
    repair_autopilot,
    repair_registry,
    repair_transactions,
)

__all__ = [
    "get_vault_dir",
    "main",
    "print_report",
    "repair_autopilot",
    "repair_registry",
    "repair_transactions",
]


if __name__ == "__main__":
    raise SystemExit(main())
