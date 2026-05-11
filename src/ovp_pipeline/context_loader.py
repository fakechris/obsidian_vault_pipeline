"""User-facing LLM context loader (M20 / BL-075).

Reads two vault-root files and concatenates them as a system-prompt
prefix that LLM call sites can prepend to their existing prompts:

* ``00-Polaris/USER.md`` — user identity, current focus, voice, what
  to ignore.  Hand-authored, edited weekly.
* ``OVP_RULES.md`` — autonomous-action contract (never delete without
  confirmation, always date-stamp, log every write, etc.).  Machine-
  facing constitution that every autonomous LLM call must respect.

Design rules
============

1. **Graceful degradation.**  Either file missing → return ``""``.
   Every existing call site continues to work unchanged.
2. **No side effects.**  This module never writes.  It only reads
   the two files.  All caching is keyed to mtime so weekly edits
   propagate without restarts.
3. **Bounded size.**  USER.md and OVP_RULES.md should each stay
   under ~4 KB; the loader truncates anything beyond ``MAX_BYTES``
   so a runaway file can't blow LLM token budgets.

Wired into (initial scope, expanded by BL-076 and BL-077):

* ``auto_evergreen_extractor`` — extraction adapts to user voice
* ``synthesis/community_crystals`` — crystals reflect user focus
* ``synthesis/contradiction_crystals`` — open questions framed in
  the user's voice
* BL-076 task handlers (RESEARCH, SYNTHESIZE, DIGEST, CONTRADICT)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

USER_PROFILE_REL: Final[str] = "00-Polaris/USER.md"
RULES_REL: Final[str] = "OVP_RULES.md"

# Per-file cap.  Keep the prefix bounded so a forgetful edit can't
# 10x every LLM call.  Both files together stay under ~8 KB.
MAX_BYTES: Final[int] = 4_096

_CACHE: dict[Path, tuple[float, str]] = {}


def _read_capped(path: Path) -> str:
    """Read ``path``; return empty string when missing or unreadable.

    Caches the value keyed to mtime so the file can be edited live
    without restarting any long-running OVP process.
    """
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""

    cached = _CACHE.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug("context_loader: failed to read %s: %s", path, exc)
        return ""

    if len(text.encode("utf-8")) > MAX_BYTES:
        # Trim on a line boundary so we don't cut mid-sentence.
        truncated_bytes = text.encode("utf-8")[:MAX_BYTES]
        text = truncated_bytes.decode("utf-8", errors="ignore")
        last_newline = text.rfind("\n")
        if last_newline > 0:
            text = text[:last_newline]
        text += "\n\n[truncated — see source file for full text]\n"
        logger.warning(
            "context_loader: %s exceeded %d bytes; truncated",
            path.name, MAX_BYTES,
        )

    _CACHE[path] = (mtime, text)
    return text


def load_user_profile(vault_dir: Path | str) -> str:
    """Return the user-profile text or ``""`` when the file is absent."""
    return _read_capped(Path(vault_dir) / USER_PROFILE_REL)


def load_rules(vault_dir: Path | str) -> str:
    """Return the autonomous-action rules text or ``""``."""
    return _read_capped(Path(vault_dir) / RULES_REL)


def load_llm_context(vault_dir: Path | str) -> str:
    """Return a system-prompt prefix combining USER + RULES.

    Empty string when neither file exists — callers can safely
    concatenate without conditionals.  When both exist, returns:

        # User Profile
        <USER.md body>

        # Autonomous Action Rules
        <OVP_RULES.md body>

    The headings let downstream prompt templates inline this block
    without re-explaining what it is.
    """
    profile = load_user_profile(vault_dir).strip()
    rules = load_rules(vault_dir).strip()

    parts: list[str] = []
    if profile:
        parts.append("# User Profile\n" + profile)
    if rules:
        parts.append("# Autonomous Action Rules\n" + rules)
    if not parts:
        return ""
    return "\n\n".join(parts) + "\n"


def clear_cache() -> None:
    """Drop the mtime cache.  Used by tests; production code can
    just edit the file — mtime change invalidates automatically."""
    _CACHE.clear()
