# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.

from __future__ import annotations

import json
import mimetypes
import re
import sqlite3
import threading
from html import escape
from pathlib import Path
from urllib.parse import quote, urlparse

import yaml
from markdown_it import MarkdownIt

from ovp_pipeline.identity import canonicalize_note_id
from ovp_pipeline.ops_honest_zero import honest_zero_html
from ovp_pipeline.pack_resolution import iter_compatible_packs
from ovp_pipeline.packs.loader import PRIMARY_PACK_NAME
from ovp_pipeline.runtime import VaultLayout
from ovp_pipeline.ui.view_models import (
    DEFAULT_CANDIDATE_BROWSER_LIMIT,
    build_runtime_home_payload,
)


"""HTML rendering functions for the OVP UI server.

All page, card, and fragment renderers live here.  They are pure
functions: each receives a ``payload`` dict (or a few scalar args)
and returns an HTML string.  The companion ``ui_server.py`` module
owns routing and the HTTP handler.
"""


_MARKDOWN_RENDERER = MarkdownIt("commonmark", {"breaks": True, "html": False}).enable("table")

_FENCED_FRONTMATTER_RE = re.compile(r"^```ya?ml\s*\n---\n(.*?)\n---\n```\s*\n?", re.DOTALL)

_GITHUB_REPO_RE = re.compile(r"https://github\.com/([^/\s]+)/([^/\s#]+)")

_EVOLUTION_LINK_TYPES = ["challenges", "replaces", "enriches", "confirms"]


_request_ctx = threading.local()



_CANDIDATE_MERGE_AUTOFILL_THRESHOLD = 0.7

_INLINE_MEMBER_LINK_LIMIT = 8



_THIN_NOTE_TYPES: frozenset[str] = frozenset(
    {
        # Generated / autonomous-action outputs and user-declared
        # interpretation surfaces don't have provenance, production
        # chains, source notes, or inbound captures.  Rendering the
        # full evergreen scaffold around them produces a page of empty
        # cards with the actual content buried at the bottom.
        "digest",
        "live-concept",
        "user-profile",
    }
)


_THIN_NOTE_PATH_PREFIXES: tuple[str, ...] = (
    # Everything under GENERATED is by definition agent-produced
    # content, not a Canonical-State object — apply the thin shell
    # even if the frontmatter type is missing or unrecognised.
    "40-Resources/Generated/",
)



# Lineage-card CSS pulled to a module-level constant so the
# render function doesn't ship a multi-line literal in the middle
# of its body.  Scope is intentionally local — promoting these
# styles to ``_layout`` would make them load on every page even
# when the lineage card isn't rendered.  Visual rules now live in
# /static/ovp-pages.css (.lineage-flow / .lineage-row / .lineage-arrow);
# this constant stays as an empty string so callers that interpolate
# it stay shape-stable and we don't have to chase every f-string.
_LINEAGE_CARD_STYLE = ""



_OBJECTS_INDEX_PAGE_SIZES = (10, 50, 100, 200)



# Type-facet chip-rail rules now live in /static/ovp-pages.css.
_TYPE_FACET_STYLE = ""


# Default chip-rail size for the type facet.  12 covers the common
# CORE_OBJECT_KINDS set with one or two long-tail entries; the long
# tail of rare kinds stays accessible via the search box / API.
_TYPE_FACET_DEFAULT_LIMIT = 12



_SHELL_BODY_OPEN = '<div class="shell-body">'



# Bridge script appended to every fragment so iframe clicks on `/object?id=...`
# anchors become `postMessage({type:'select_object', id})` calls — the
# Workbench parent listens for this and re-points the object pane without a
# full page reload.
_FRAGMENT_BRIDGE_SCRIPT = (
    "<script>(function(){"
    "if(window.parent===window)return;"
    "document.addEventListener('click',function(ev){"
    "var a=ev.target&&ev.target.closest&&ev.target.closest('a[href]');"
    "if(!a)return;"
    "var href=a.getAttribute('href')||'';"
    "var m=href.match(/\\/object(?:\\/fragment)?\\?(?:[^#]*&)?id=([^&#]+)/);"
    "if(!m)return;"
    "ev.preventDefault();"
    "window.parent.postMessage({type:'select_object',id:decodeURIComponent(m[1])},'*');"
    "},true);"
    "})();</script>"
)



# Timeline / Lineage UI strings — pulled out for translation /
# constant-vs-magic-number hygiene.  Body copy is intentionally
# Chinese to match the rest of the maintainer surface.
_TIMELINE_NEW_EVERGREENS_LABEL = "新增 evergreens"

_TIMELINE_ERROR_SAMPLE_HEADING = "Errors / skips"

# Cap the per-error row's ``subject`` rendering so a 2KB JSON dump
# doesn't blow out the day card.  140 covers most "absorb_parse_error
# on /Users/chris/.../<long-path>.md" cases without truncation.
_TIMELINE_ERROR_SUBJECT_MAX_CHARS = 140


# Day-card CSS pulled to a module-level constant so
# ``_render_timeline_page`` doesn't ship a multi-line literal in the
# middle of its body.  Inline rather than promoted to ``_layout`` —
# the styles are scoped to one route, lifting them globally would
# Timeline day-card rules now live in /static/ovp-pages.css.
_TIMELINE_DAY_CARD_STYLE = ""



# Number of by-type pills shown in the trail of each /ops/today
# card.  3 fits one line on most screens; the long tail lives in
# /ops/timeline.
_TODAY_CARD_TOP_TYPES_LIMIT = 3

# Truncate sample event_type / subject so a card stays scannable.
_TODAY_SAMPLE_EVENT_TYPE_MAX_CHARS = 30

_TODAY_SAMPLE_SUBJECT_MAX_CHARS = 80

# /ops/runs* renderers — clip txn_id and the per-row subject so the
# table doesn't word-wrap on long pipeline.jsonl strings.
_RUN_TXN_ID_DISPLAY_MAX_CHARS = 30

_RUN_DETAIL_SUBJECT_MAX_CHARS = 120

# ISO-8601 timestamps are sliced to YYYY-MM-DDTHH:MM:SS for display.
_TS_DISPLAY_LEN = 19



# Today digest cards now live in /static/ovp-pages.css.
_TODAY_DIGEST_STYLE = ""



# Runs index table rules now live in /static/ovp-pages.css.
_RUNS_INDEX_STYLE = ""



# Run detail rules now live in /static/ovp-pages.css.
_RUN_DETAIL_STYLE = ""


_BRACKET_EVENT_TYPES = frozenset(
    {
        "transaction_started",
        "transaction_completed",
    }
)


_ERROR_EVENT_TYPE_PREFIXES = (
    "absorb_parse_error",
    "absorb_schema_drift",
    "broken_link",
    "github_intake_error",
    "article_error",
    "image_download_error",
)


__all__ = [
    'annotations',
    'json',
    'mimetypes',
    're',
    'sqlite3',
    'threading',
    'escape',
    'Path',
    'quote',
    'urlparse',
    'yaml',
    'MarkdownIt',
    'canonicalize_note_id',
    'honest_zero_html',
    'iter_compatible_packs',
    'PRIMARY_PACK_NAME',
    'VaultLayout',
    'DEFAULT_CANDIDATE_BROWSER_LIMIT',
    'build_runtime_home_payload',
    '_MARKDOWN_RENDERER',
    '_FENCED_FRONTMATTER_RE',
    '_GITHUB_REPO_RE',
    '_EVOLUTION_LINK_TYPES',
    '_request_ctx',
    '_CANDIDATE_MERGE_AUTOFILL_THRESHOLD',
    '_INLINE_MEMBER_LINK_LIMIT',
    '_THIN_NOTE_TYPES',
    '_THIN_NOTE_PATH_PREFIXES',
    '_LINEAGE_CARD_STYLE',
    '_OBJECTS_INDEX_PAGE_SIZES',
    '_TYPE_FACET_STYLE',
    '_TYPE_FACET_DEFAULT_LIMIT',
    '_SHELL_BODY_OPEN',
    '_FRAGMENT_BRIDGE_SCRIPT',
    '_TIMELINE_NEW_EVERGREENS_LABEL',
    '_TIMELINE_ERROR_SAMPLE_HEADING',
    '_TIMELINE_ERROR_SUBJECT_MAX_CHARS',
    '_TIMELINE_DAY_CARD_STYLE',
    '_TODAY_CARD_TOP_TYPES_LIMIT',
    '_TODAY_SAMPLE_EVENT_TYPE_MAX_CHARS',
    '_TODAY_SAMPLE_SUBJECT_MAX_CHARS',
    '_RUN_TXN_ID_DISPLAY_MAX_CHARS',
    '_RUN_DETAIL_SUBJECT_MAX_CHARS',
    '_TS_DISPLAY_LEN',
    '_TODAY_DIGEST_STYLE',
    '_RUNS_INDEX_STYLE',
    '_RUN_DETAIL_STYLE',
    '_BRACKET_EVENT_TYPES',
    '_ERROR_EVENT_TYPE_PREFIXES'
]
