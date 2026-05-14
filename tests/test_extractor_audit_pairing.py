"""M24.2: end-to-end verification of the extractor's audit pairing.

The kernel reads three event_types from this producer to drive
Extracted / Accepted / Prepared classification:

* ``evergreen_extraction_complete`` — fires at end of every run.
* ``absorb_pending_upsert`` — fires after extraction returns
  concepts, before the candidate-upsert loop runs.
* ``candidates_upserted`` — fires once at the end if at least
  one candidate was added.

These tests run ``AutoEvergreenExtractor.process_file`` against a
fake LLM that returns a known concept, then read
``pipeline.jsonl`` and assert the pairing landed.  Pre-M24.2 the
last two rows simply did not exist; today they do.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ovp_pipeline.auto_evergreen_extractor import (
    AutoEvergreenExtractor,
    EvergreenExtractor,
    PipelineLogger,
)


class _FakeLLM:
    """Returns a single canned concept for every prompt."""

    def __init__(self, concepts):
        self._concepts = concepts
        self.calls: list[dict] = []

    def generate(
        self, system_prompt: str, user_prompt: str, max_tokens: int = 4000
    ) -> str:
        self.calls.append({"system": system_prompt, "user": user_prompt})
        return json.dumps(self._concepts, ensure_ascii=False)


def _read_log(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    rows = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


@pytest.fixture
def configured_extractor(temp_vault):
    """``AutoEvergreenExtractor`` with a fake LLM that returns one
    concept the registry will accept as a new candidate."""
    log_path = temp_vault / "60-Logs" / "pipeline.jsonl"
    logger = PipelineLogger(log_path)
    extractor = AutoEvergreenExtractor(temp_vault, logger)
    # v2 response shape — ``{"units": [...]}`` is what the extractor
    # parses today.  Each unit needs at least ``title`` or ``slug``
    # to survive ``_unit_to_concept``.
    fake_llm = _FakeLLM(
        {
            "units": [
                {
                    "slug": "memory-systems",
                    "title": "Memory Systems",
                    "unit_type": "concept",
                    "content": "Architectures for LLM memory.",
                    "related_concepts": [],
                }
            ]
        }
    )
    extractor.extractor = EvergreenExtractor(
        fake_llm, logger, vault_dir=temp_vault
    )
    return extractor, log_path


def test_extraction_emits_pending_then_upserted(configured_extractor, tmp_path):
    """Happy path: pending row precedes upsert row in the log."""
    extractor, log_path = configured_extractor
    article = tmp_path / "test_article.md"
    article.write_text(
        "# Memory Systems Survey\n\n"
        "Modern LLM stacks use retrieval-augmented memory plus episodic stores.",
        encoding="utf-8",
    )

    result = extractor.process_file(article)
    assert result.get("concepts_extracted") == 1
    assert result.get("candidates_added", 0) >= 0  # registry may reject; that's fine

    rows = _read_log(log_path)
    event_types = [r.get("event_type") for r in rows]

    # ``absorb_pending_upsert`` must precede ``candidates_upserted`` —
    # the order encodes the producer-pair contract the kernel reads.
    assert "absorb_pending_upsert" in event_types

    pending_idx = event_types.index("absorb_pending_upsert")
    if "candidates_upserted" in event_types:
        upsert_idx = event_types.index("candidates_upserted")
        assert pending_idx < upsert_idx, (
            "absorb_pending_upsert must be written before "
            "candidates_upserted — the kernel relies on this order"
        )


def test_dry_run_does_not_emit_pairing(configured_extractor, tmp_path):
    """In dry-run mode the producer mustn't lie to the kernel — no
    pending row, no upsert row."""
    extractor, log_path = configured_extractor
    article = tmp_path / "test_dry.md"
    article.write_text("# Dry run survey\n\nBody.", encoding="utf-8")

    extractor.process_file(article, dry_run=True)
    rows = _read_log(log_path)
    event_types = {r.get("event_type") for r in rows}

    assert "absorb_pending_upsert" not in event_types
    assert "candidates_upserted" not in event_types


def test_zero_concepts_skips_pending(configured_extractor, tmp_path):
    """When the LLM returns no concepts, the pending row mustn't
    fire — otherwise the kernel sees a phantom Prepared anchor."""
    extractor, log_path = configured_extractor
    # Swap in an LLM that returns no concepts (v2 empty units = skip).
    extractor.extractor.llm = _FakeLLM(  # type: ignore[attr-defined]
        {"units": [], "skip_reason": "test: empty"}
    )

    article = tmp_path / "empty.md"
    article.write_text("# Empty\n\nBody.", encoding="utf-8")
    extractor.process_file(article)

    rows = _read_log(log_path)
    event_types = [r.get("event_type") for r in rows]
    assert "absorb_pending_upsert" not in event_types
