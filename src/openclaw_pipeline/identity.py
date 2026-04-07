from __future__ import annotations

import re


def canonicalize_note_id(value: str) -> str:
    """
    Normalize a note identity into the canonical slug form used across the system.

    Rules:
    - strip heading/query suffixes from wikilinks
    - if the value is path-like, use the final path segment as the note identity
    - normalize whitespace/underscores to hyphens
    - remove non-word characters except hyphens
    - lowercase and collapse repeated hyphens
    """
    note_id = re.sub(r"[#?].*$", "", value).strip()
    if "/" in note_id or "\\" in note_id:
        note_id = re.split(r"[/\\]+", note_id)[-1]
    note_id = re.sub(r"[\s_]+", "-", note_id)
    note_id = re.sub(r"[^\w\-]", "", note_id, flags=re.UNICODE)
    note_id = re.sub(r"-+", "-", note_id)
    return note_id.strip("-").lower()
