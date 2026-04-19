#!/usr/bin/env python3
"""Thin CLI wrapper for the canonical rebuild_registry implementation."""

from __future__ import annotations

from ..rebuild_registry import main, print_report, reconcile_registry, rebuild_registry

__all__ = ["main", "print_report", "reconcile_registry", "rebuild_registry"]


if __name__ == "__main__":
    raise SystemExit(main())
