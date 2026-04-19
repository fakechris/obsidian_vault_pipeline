#!/usr/bin/env python3
"""Thin CLI wrapper for the canonical migrate_broken_links implementation."""

from __future__ import annotations

from ..migrate_broken_links import (
    BrokenLinkOccurrence,
    BrokenLinkResolver,
    BrokenLinkScanner,
    LinkPatcher,
    UniqueBrokenMention,
    WikilinkExtractor,
    apply_resolution_results,
    main,
    resolve_broken_mentions,
    scan_broken_mentions,
)

__all__ = [
    "BrokenLinkOccurrence",
    "BrokenLinkResolver",
    "BrokenLinkScanner",
    "LinkPatcher",
    "UniqueBrokenMention",
    "WikilinkExtractor",
    "apply_resolution_results",
    "main",
    "resolve_broken_mentions",
    "scan_broken_mentions",
]


if __name__ == "__main__":
    raise SystemExit(main())
