"""Tests for PR-G2 (BL-039): extraction-time entity prime.

Validates that the auto_evergreen_extractor injects known entities
from the entity_aliases view into the user prompt, so the LLM can
collapse name variants (Karpathy / Andrej / @karpathy) to one
canonical handle instead of inventing new ones per article.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from ovp_pipeline.auto_evergreen_extractor import EvergreenExtractor
from ovp_pipeline.entities.store import EntityStore


class _FakeLogger:
    """Minimal stand-in for ``PipelineLogger`` — note that the real
    class is a structured EVENT logger (``log(event_type, data)``),
    NOT a stdlib-style logger.  Earlier versions of these tests
    mocked a ``.warning()`` method that doesn't exist on the real
    class, masking the bug review found in PR #127.
    """

    def __init__(self):
        self.events: list[dict] = []

    def log(self, event_type: str, data: dict):
        self.events.append({"event_type": event_type, **data})


def _build_extractor(vault_dir: Path) -> EvergreenExtractor:
    return EvergreenExtractor(
        llm_client=MagicMock(),
        logger=_FakeLogger(),
        vault_dir=vault_dir,
    )


def _seed_entities(vault_dir: Path, *, persons: list[tuple[str, str, float]] | None = None,
                   orgs: list[tuple[str, str, float]] | None = None) -> None:
    """Helper to populate the entity store with simple person/org rows."""
    db = vault_dir / "60-Logs" / "knowledge.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    store = EntityStore(db_path=db)
    for handle, name, auth in (persons or []):
        store.upsert(
            entity_type="person", identity_key=handle, canonical_name=name,
            signals={"links": []}, derived_authority=auth,
            fetch_source="identity_merge",
        )
    for handle, name, auth in (orgs or []):
        store.upsert(
            entity_type="organization", identity_key=handle, canonical_name=name,
            signals={"links": []}, derived_authority=auth,
            fetch_source="identity_merge",
        )


# ---------------------------------------------------------------------------
# Block construction
# ---------------------------------------------------------------------------


class TestEntityPrimeBlock:
    def test_empty_when_no_vault_dir(self):
        # Bare extractor without a vault_dir → no prime.
        extractor = EvergreenExtractor(
            llm_client=MagicMock(), logger=_FakeLogger(),
            vault_dir=None,
        )
        assert extractor._load_entity_prime_block() == ""

    def test_empty_when_entity_store_missing(self, tmp_path):
        # Vault dir exists but no knowledge.db → empty (read paths
        # are no-side-effect; no DB gets created here).
        extractor = _build_extractor(tmp_path)
        block = extractor._load_entity_prime_block()
        assert block == ""
        # Critical: no DB file was created by the read attempt.
        assert not (tmp_path / "60-Logs" / "knowledge.db").exists()

    def test_renders_canonicals_with_aliases(self, tmp_path):
        _seed_entities(
            tmp_path,
            persons=[
                ("karpathy", "Andrej Karpathy", 0.65),
                ("simonw", "Simon Willison", 0.55),
            ],
            orgs=[
                ("anthropicai", "Anthropic", 0.70),
            ],
        )
        extractor = _build_extractor(tmp_path)
        block = extractor._load_entity_prime_block()

        # Block header is present in Chinese (matches the rest of
        # the prompt's locale).
        assert "已知实体目录" in block
        # Each canonical handle is rendered as a backticked slug.
        assert "`karpathy`" in block
        assert "`simonw`" in block
        assert "`anthropicai`" in block
        # Display-name aliases appear ("andrej karpathy" lowercased).
        assert "andrej karpathy" in block
        # @-handle aliases appear.
        assert "@karpathy" in block
        # Entity type is annotated for the LLM.
        assert "(person)" in block
        assert "(organization)" in block

    def test_caps_to_top_n_by_authority(self, tmp_path):
        # Seed more canonicals than the cap; assert highest authority
        # entries are kept.
        original = EvergreenExtractor._ENTITY_PRIME_TOP_N
        EvergreenExtractor._ENTITY_PRIME_TOP_N = 3
        try:
            _seed_entities(tmp_path, persons=[
                (f"low{i}", f"Low {i}", 0.10) for i in range(10)
            ] + [
                ("highest", "Highest", 0.95),
                ("middle", "Middle", 0.60),
                ("alsohigh", "Also High", 0.90),
            ])
            extractor = _build_extractor(tmp_path)
            block = extractor._load_entity_prime_block()
            # Top 3 by authority should be highest, alsohigh, middle.
            assert "`highest`" in block
            assert "`alsohigh`" in block
            assert "`middle`" in block
            # And NOT one of the low-authority ones.
            assert "`low0`" not in block
        finally:
            EvergreenExtractor._ENTITY_PRIME_TOP_N = original

    def test_caps_aliases_per_canonical(self, tmp_path):
        # Even when one canonical has many aliases, the prime
        # truncates to keep the prompt small.
        db = tmp_path / "60-Logs" / "knowledge.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        # We seed via JSONL since aliases array is the easiest source.
        jsonl = tmp_path / "60-Logs" / "authors.jsonl"
        import json
        jsonl.write_text(json.dumps({
            "handle": "karpathy",
            "aliases": [
                "andrej", "andrej karpathy", "andrej_karpathy",
                "karpathy.ai", "andrej_k", "ak", "kp",
            ],
            "authority": 0.95,
        }) + "\n", encoding="utf-8")

        extractor = _build_extractor(tmp_path)
        block = extractor._load_entity_prime_block()
        # Find the karpathy line and count comma-separated aliases.
        karpathy_line = next(
            line for line in block.splitlines()
            if "`karpathy`" in line
        )
        # Format: "- `karpathy` (whitelist) — 也可写作: a, b, c, d"
        aliases_part = karpathy_line.split("也可写作:", 1)[-1]
        rendered_aliases = [a.strip() for a in aliases_part.split(",") if a.strip()]
        assert len(rendered_aliases) <= EvergreenExtractor._ENTITY_PRIME_ALIAS_CAP_PER_CANONICAL


class TestEntityPrimeCaching:
    def test_block_is_cached_on_instance(self, tmp_path):
        # First call hits the entity store; second call returns the
        # cached string.  In batch mode this avoids re-loading the
        # entity table for each of the ~7000 evergreens.
        _seed_entities(tmp_path, persons=[("karpathy", "Andrej Karpathy", 0.6)])
        extractor = _build_extractor(tmp_path)
        first = extractor._load_entity_prime_block()
        second = extractor._load_entity_prime_block()
        # Same string object — cache hit.
        assert first is second


class TestEntityPrimeResilience:
    def test_corrupted_store_does_not_block_extraction(self, tmp_path):
        # If the entity layer raises for any reason, the extractor
        # falls back to "" (no prime) rather than crashing the
        # whole extraction run.  Critical because extraction is the
        # primary pipeline step — a flaky entity store must never
        # take it down.
        from unittest.mock import patch

        extractor = _build_extractor(tmp_path)
        # Make EntityStore.list_by_type throw on first access.
        with patch(
            "ovp_pipeline.entities.store.EntityStore.list_by_type",
            side_effect=RuntimeError("simulated DB corruption"),
        ):
            block = extractor._load_entity_prime_block()
        assert block == ""
        # The extractor logged a structured event so the operator
        # can spot the issue in the pipeline audit log.
        events = extractor.logger.events
        assert any(e["event_type"] == "entity_prime_unavailable" for e in events)
        # The triggering exception text is preserved for debugging.
        assert any("simulated DB corruption" in str(e.get("error", ""))
                   for e in events)
