"""Tests for ``ovp-backfill-entities`` re-extraction dedup.

The May 2026 history rerun discovered that ``backfill_entities.py`` had
no dedup against ``entity-extractions.jsonl``: every invocation
re-processed all files in ``20-Areas/``, even ones already in the log.
This re-extraction caused 274 files to get 2-4 log entries during a
single run, doubling the LLM token cost without producing new data.

Locks the new behaviour:
  * by default, files already in the extraction log are skipped
  * ``--force`` overrides and re-processes them
  * the summary reports how many were skipped
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ovp_pipeline.commands import backfill_entities


@pytest.fixture
def vault_with_one_deepdive(temp_vault):
    """Vault with a single deep dive ready for backfill, no LLM client."""
    md = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "x_深度解读.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("---\ntitle: x\n---\nbody mentioning Claude.\n", encoding="utf-8")
    return temp_vault, md


def _read_log_lines(vault: Path) -> list[dict]:
    log = vault / "60-Logs" / "entity-extractions.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestDedupAgainstExtractionLog:

    def test_default_run_skips_already_extracted(self, vault_with_one_deepdive):
        vault, md = vault_with_one_deepdive
        # Pre-seed the extraction log so this file looks "already done".
        log = vault / "60-Logs" / "entity-extractions.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            json.dumps({
                "source_file": str(md),
                "source_slug": "x-深度解读",
                "mentions": [],
                "backfilled_at": "2026-05-01T00:00:00+00:00",
            }) + "\n",
            encoding="utf-8",
        )

        summary = backfill_entities.run(
            vault, dry_run=False, use_llm=False,  # alias-only, no LLM client needed
        )
        assert summary["files_processed"] == 0
        assert summary["files_skipped_already_extracted"] == 1
        # No new log lines written:
        assert len(_read_log_lines(vault)) == 1

    def test_force_reprocesses_already_extracted(self, vault_with_one_deepdive):
        vault, md = vault_with_one_deepdive
        log = vault / "60-Logs" / "entity-extractions.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            json.dumps({
                "source_file": str(md),
                "source_slug": "x-深度解读",
                "mentions": [],
                "backfilled_at": "2026-05-01T00:00:00+00:00",
            }) + "\n",
            encoding="utf-8",
        )

        summary = backfill_entities.run(
            vault, dry_run=False, use_llm=False, force=True,
        )
        assert summary["files_processed"] == 1
        assert summary["files_skipped_already_extracted"] == 0

    def test_fresh_vault_processes_everything(self, vault_with_one_deepdive):
        vault, _md = vault_with_one_deepdive
        # No prior extraction log → every file is "new".
        summary = backfill_entities.run(
            vault, dry_run=False, use_llm=False,
        )
        assert summary["files_processed"] == 1
        assert summary["files_skipped_already_extracted"] == 0


class TestLLMClientFactory:
    """Smoke test for src/ovp_pipeline/llm_client.py — the missing module
    that caused entity_extract to silently fall back to alias-only mode
    for months (entity_extractor / backfill_entities both import it via
    try/except ImportError: pass).
    """

    def test_module_importable(self):
        """If this fails, the file vanished and the silent-fallback is back."""
        from ovp_pipeline import llm_client  # noqa: F401
        assert hasattr(llm_client, "get_litellm_client")

    def test_returns_none_without_api_key(self, temp_vault, monkeypatch):
        from ovp_pipeline.llm_client import get_litellm_client
        # Strip every API key env var the resolver checks.
        for env_name in (
            "AUTO_VAULT_API_KEY", "SPEC_ORCH_LLM_API_KEY",
            "MINIMAX_API_KEY", "MINIMAX_CN_API_KEY",
            "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
        ):
            monkeypatch.delenv(env_name, raising=False)
        client = get_litellm_client(vault_dir=temp_vault)
        assert client is None
