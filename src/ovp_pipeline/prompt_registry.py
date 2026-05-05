"""Prompt registry — central place where every LLM prompt lives.

Phase 1 of the prompt-evolution architecture.  Pre-2026-05 the absorb,
article-rewriter, paper-rewriter, entity-extract, and quality-check
prompts each lived hardcoded inside their owning module as a string
literal.  That made:

* Iterating on a prompt require editing Python (git diffs are buried
  in indentation noise).
* Recording which prompt version produced which artifact impossible
  beyond a single hardcoded "v2" marker.
* Side-by-side comparison of two prompt versions on the same source
  require manually checking out two git commits.

This module is the smallest thing that fixes (1) — moves the prompt
text out of code into versioned ``.md`` files with frontmatter
metadata.  It is **not** an A/B routing system; that's deferred (see
``docs/plans/2026-05-05-prompt-ab-test-backlog.md``).  But it lays the
foundation: once every prompt is in the registry, A/B routing becomes
a config knob, not a refactor.

Layout::

    src/ovp_pipeline/prompts/
    ├── README.md
    └── <prompt_name>/
        ├── v1.md
        ├── v2.md
        └── v3-experimental.md     (optional)

Each ``.md`` carries YAML frontmatter declaring at minimum
``prompt_name``, ``version``, and ``status``.  The body after the
closing ``---`` fence is the literal prompt text fed to the LLM.

Loading is cached at module scope — registry files don't change at
runtime, and parsing YAML on every absorb call would be silly.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Prompt:
    """A single registered prompt version, ready to feed to an LLM.

    ``body`` is the literal prompt text (after the closing ``---``
    fence in the source ``.md``).  ``metadata`` is the YAML frontmatter
    parsed into a dict so callers can read tunables / vocab / schema
    declarations without re-parsing the file.
    """
    name: str
    version: str
    status: str           # "stable" | "experimental" | "deprecated"
    schema_version: int   # bump when output JSON schema breaks
    body: str
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class PromptRegistryError(Exception):
    """Raised when a registered prompt is missing or malformed.

    We intentionally fail loud rather than fall back to a hardcoded
    default — silent fallback was the BL-058 "fake-v1" bug class
    (LLM ran v2 but downstream wrote v1 schema because the fields
    weren't propagated).  Same lesson here: if the registry can't
    load v2, the right answer is a CI failure, not a degraded run.
    """


def _default_prompts_dir() -> Path:
    """The package-shipped prompts directory.

    Per-vault overrides (``<vault>/.ovp/prompts/``) are deferred to
    Phase 2 of the prompt-evolution roadmap — they require routing
    + per-source version selection that this Phase 1 doesn't do.
    """
    return Path(__file__).resolve().parent / "prompts"


class PromptRegistry:
    """Loads prompt files from disk and caches them.

    Construct one per process — module-level helpers below build a
    singleton on demand.  Tests can construct a registry pointed at a
    fixture directory to avoid touching the production prompts.
    """

    def __init__(self, prompts_dir: Path | None = None) -> None:
        self._prompts_dir = prompts_dir or _default_prompts_dir()
        self._cache: dict[tuple[str, str], Prompt] = {}
        self._lock = threading.Lock()

    @property
    def prompts_dir(self) -> Path:
        return self._prompts_dir

    def load(self, name: str, version: str) -> Prompt:
        """Return the prompt at ``<prompts_dir>/<name>/<version>.md``.

        Raises ``PromptRegistryError`` when:

          * The file doesn't exist.
          * The file has no YAML frontmatter (missing closing ``---``).
          * Frontmatter fails to parse as YAML.
          * Required keys (``prompt_name``, ``version``, ``status``,
            ``schema_version``) are missing.
          * ``prompt_name``/``version`` in frontmatter disagree with
            the requested name/version (catches typos in filenames).
        """
        cache_key = (name, version)
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        prompt_file = self._prompts_dir / name / f"{version}.md"
        if not prompt_file.is_file():
            raise PromptRegistryError(
                f"Prompt {name}@{version} not found at {prompt_file}"
            )

        try:
            text = prompt_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise PromptRegistryError(
                f"Failed to read {prompt_file}: {exc}"
            ) from exc

        prompt = self._parse(prompt_file, name, version, text)

        with self._lock:
            self._cache[cache_key] = prompt
        return prompt

    def list_versions(self, name: str) -> list[str]:
        """Every ``.md`` under ``<prompts_dir>/<name>/``, sorted."""
        prompt_dir = self._prompts_dir / name
        if not prompt_dir.is_dir():
            return []
        return sorted(p.stem for p in prompt_dir.glob("*.md"))

    def list_names(self) -> list[str]:
        """Every prompt name (subdirectory under ``prompts/``)."""
        if not self._prompts_dir.is_dir():
            return []
        return sorted(p.name for p in self._prompts_dir.iterdir() if p.is_dir())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(path: Path, expected_name: str, expected_version: str, text: str) -> Prompt:
        # Frontmatter must open with a literal '---' line and close with
        # another '---' line.  We split lines instead of using
        # ``text.index("---", 3)`` because a YAML string value containing
        # ``---`` (e.g. ``notes: "before --- after"``) would otherwise be
        # mistaken for the closing fence and silently truncate metadata.
        lines = text.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            raise PromptRegistryError(
                f"{path}: missing YAML frontmatter (file must start with a '---' line)"
            )
        closing_idx: int | None = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                closing_idx = i
                break
        if closing_idx is None:
            raise PromptRegistryError(
                f"{path}: missing closing '---' fence on frontmatter"
            )

        fm_text = "".join(lines[1:closing_idx])
        body = "".join(lines[closing_idx + 1:]).lstrip("\n")

        try:
            metadata = yaml.safe_load(fm_text) or {}
        except yaml.YAMLError as exc:
            raise PromptRegistryError(
                f"{path}: failed to parse YAML frontmatter: {exc}"
            ) from exc
        if not isinstance(metadata, dict):
            raise PromptRegistryError(
                f"{path}: frontmatter must be a YAML mapping (got {type(metadata).__name__})"
            )

        required = ("prompt_name", "version", "status", "schema_version")
        missing = [k for k in required if k not in metadata]
        if missing:
            raise PromptRegistryError(
                f"{path}: frontmatter missing required key(s): {', '.join(missing)}"
            )

        # Cross-check filename vs frontmatter — catches "I copied v2.md to
        # v3.md but forgot to update the version: field" bugs.
        fm_name = str(metadata["prompt_name"])
        fm_version = str(metadata["version"])
        if fm_name != expected_name:
            raise PromptRegistryError(
                f"{path}: frontmatter prompt_name={fm_name!r} but filename "
                f"path implies {expected_name!r}"
            )
        if fm_version != expected_version:
            raise PromptRegistryError(
                f"{path}: frontmatter version={fm_version!r} but filename "
                f"implies {expected_version!r}"
            )

        status = str(metadata["status"]).lower()
        valid_statuses = {"stable", "experimental", "deprecated"}
        if status not in valid_statuses:
            raise PromptRegistryError(
                f"{path}: frontmatter status={status!r} not in {sorted(valid_statuses)}"
            )

        try:
            schema_version = int(metadata["schema_version"])
        except (TypeError, ValueError) as exc:
            raise PromptRegistryError(
                f"{path}: frontmatter schema_version must be an int, "
                f"got {metadata['schema_version']!r}"
            ) from exc

        return Prompt(
            name=fm_name,
            version=fm_version,
            status=status,
            schema_version=schema_version,
            body=body.rstrip("\n") + "\n",  # trailing newline normalised
            metadata=dict(metadata),
        )


# ---------------------------------------------------------------------------
# Module-level singleton (production callers)
# ---------------------------------------------------------------------------


_default_registry: PromptRegistry | None = None
_default_lock = threading.Lock()


def get_default_registry() -> PromptRegistry:
    """Return the package-default registry, building it lazily.

    Tests that want isolation should construct their own
    ``PromptRegistry(prompts_dir=...)`` rather than calling this.
    """
    global _default_registry
    with _default_lock:
        if _default_registry is None:
            _default_registry = PromptRegistry()
        return _default_registry


def get_prompt(name: str, version: str) -> Prompt:
    """Convenience: load via the default registry.

    Production callers typically do::

        from .prompt_registry import get_prompt
        SYSTEM_PROMPT = get_prompt("absorb", "v2").body
    """
    return get_default_registry().load(name, version)


def reset_default_registry_for_tests() -> None:
    """Clear the module-level singleton — tests only."""
    global _default_registry
    with _default_lock:
        _default_registry = None
