"""Tests for the refresh-source-authority wrapper.

Mocks the three sub-CLIs so we never hit the real APIs.  Verifies:
  * each step is invoked with --vault-dir + --max-age-days passed through
  * --skip-* flags actually skip
  * status JSON is written with per-step rc + ok + elapsed_s
  * --strict makes a failed step bubble out as non-zero exit
  * lock file blocks a concurrent run with a live PID
  * stale lock (PID dead) is silently stolen
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from ovp_pipeline.commands import refresh_source_authority as rsa


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    return vault


# ---------------------------------------------------------------------------
# Step orchestration
# ---------------------------------------------------------------------------


class TestStepOrchestration:
    def test_all_three_steps_invoked_with_pass_through_args(self, tmp_path):
        vault = _make_vault(tmp_path)
        captured: dict[str, list[str]] = {}

        def fake(name):
            def _impl(argv):
                captured[name] = argv
                return 0
            return _impl

        with patch.object(rsa, "_pid_alive", return_value=False), \
             patch("ovp_pipeline.commands.backfill_twitter_authors.main",
                   side_effect=fake("twitter")), \
             patch("ovp_pipeline.commands.backfill_github.main",
                   side_effect=fake("github")), \
             patch("ovp_pipeline.commands.merge_identities.main",
                   side_effect=fake("merge")):
            rc = rsa.main(["--vault-dir", str(vault), "--max-age-days", "7"])
        assert rc == 0
        # Each backfill saw --vault-dir + --max-age-days
        assert "--vault-dir" in captured["twitter"]
        assert "--max-age-days" in captured["twitter"]
        assert captured["twitter"][captured["twitter"].index("--max-age-days") + 1] == "7"
        assert "--max-age-days" in captured["github"]
        # merge_identities only takes --vault-dir
        assert "--max-age-days" not in captured["merge"]

    def test_skip_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        called: set[str] = set()

        def make(name):
            def _impl(_argv):
                called.add(name)
                return 0
            return _impl

        with patch("ovp_pipeline.commands.backfill_twitter_authors.main",
                   side_effect=make("twitter")), \
             patch("ovp_pipeline.commands.backfill_github.main",
                   side_effect=make("github")), \
             patch("ovp_pipeline.commands.merge_identities.main",
                   side_effect=make("merge")):
            rc = rsa.main([
                "--vault-dir", str(vault),
                "--skip-twitter",
                "--skip-merge",
            ])
        assert rc == 0
        assert called == {"github"}


# ---------------------------------------------------------------------------
# Status JSON
# ---------------------------------------------------------------------------


class TestStatusFile:
    def test_status_json_records_each_step(self, tmp_path):
        vault = _make_vault(tmp_path)
        with patch("ovp_pipeline.commands.backfill_twitter_authors.main",
                   return_value=0), \
             patch("ovp_pipeline.commands.backfill_github.main",
                   return_value=0), \
             patch("ovp_pipeline.commands.merge_identities.main",
                   return_value=0):
            rsa.main(["--vault-dir", str(vault)])
        status = json.loads(
            (vault / "60-Logs" / "entity_refresh_status.json").read_text(
                encoding="utf-8",
            ),
        )
        assert status["all_ok"] is True
        assert set(status["steps"]) == {
            "twitter_backfill", "github_backfill", "identity_merge",
        }
        for step in status["steps"].values():
            assert step["rc"] == 0
            assert step["ok"] is True
            assert "elapsed_s" in step

    def test_failed_step_marks_all_ok_false(self, tmp_path):
        vault = _make_vault(tmp_path)
        with patch("ovp_pipeline.commands.backfill_twitter_authors.main",
                   return_value=0), \
             patch("ovp_pipeline.commands.backfill_github.main",
                   return_value=1), \
             patch("ovp_pipeline.commands.merge_identities.main",
                   return_value=0):
            rc = rsa.main(["--vault-dir", str(vault)])
        # Default exit is 0 even with a failed step (cron-friendly)
        assert rc == 0
        status = json.loads(
            (vault / "60-Logs" / "entity_refresh_status.json").read_text(
                encoding="utf-8",
            ),
        )
        assert status["all_ok"] is False
        assert status["steps"]["github_backfill"]["ok"] is False

    def test_strict_mode_returns_nonzero_on_failure(self, tmp_path):
        vault = _make_vault(tmp_path)
        with patch("ovp_pipeline.commands.backfill_twitter_authors.main",
                   return_value=0), \
             patch("ovp_pipeline.commands.backfill_github.main",
                   return_value=2), \
             patch("ovp_pipeline.commands.merge_identities.main",
                   return_value=0):
            rc = rsa.main(["--vault-dir", str(vault), "--strict"])
        assert rc == 1


class TestExceptionsCaptured:
    def test_step_exception_does_not_abort_run(self, tmp_path):
        vault = _make_vault(tmp_path)

        def boom(_argv):
            raise RuntimeError("boom")

        with patch("ovp_pipeline.commands.backfill_twitter_authors.main",
                   side_effect=boom), \
             patch("ovp_pipeline.commands.backfill_github.main",
                   return_value=0), \
             patch("ovp_pipeline.commands.merge_identities.main",
                   return_value=0):
            rc = rsa.main(["--vault-dir", str(vault)])
        # Default mode returns 0 even on raised exception — cron should
        # not retry-storm; status JSON has the diagnosis.
        assert rc == 0
        status = json.loads(
            (vault / "60-Logs" / "entity_refresh_status.json").read_text(
                encoding="utf-8",
            ),
        )
        assert status["all_ok"] is False
        assert status["steps"]["twitter_backfill"]["ok"] is False
        # Subsequent steps still ran.
        assert status["steps"]["github_backfill"]["ok"] is True


# ---------------------------------------------------------------------------
# Lockfile
# ---------------------------------------------------------------------------


class TestLockfile:
    def test_concurrent_live_lock_aborts(self, tmp_path):
        # Pre-create a lockfile claiming a "live" PID.
        vault = _make_vault(tmp_path)
        lock = vault / "60-Logs" / rsa._LOCKFILE_NAME
        lock.write_text(str(os.getpid()), encoding="utf-8")

        # _pid_alive returns True for current PID — lock is live.
        with pytest.raises(SystemExit):
            rsa.main(["--vault-dir", str(vault)])

    def test_stale_lock_is_stolen(self, tmp_path):
        vault = _make_vault(tmp_path)
        lock = vault / "60-Logs" / rsa._LOCKFILE_NAME
        # Write a PID that's almost certainly dead (negative or huge)
        lock.write_text("999999", encoding="utf-8")

        with patch.object(rsa, "_pid_alive", return_value=False), \
             patch("ovp_pipeline.commands.backfill_twitter_authors.main",
                   return_value=0), \
             patch("ovp_pipeline.commands.backfill_github.main",
                   return_value=0), \
             patch("ovp_pipeline.commands.merge_identities.main",
                   return_value=0):
            rc = rsa.main(["--vault-dir", str(vault)])
        assert rc == 0
        # Lock file released after run
        assert not lock.exists()

    def test_lock_released_on_exit(self, tmp_path):
        vault = _make_vault(tmp_path)
        with patch("ovp_pipeline.commands.backfill_twitter_authors.main",
                   return_value=0), \
             patch("ovp_pipeline.commands.backfill_github.main",
                   return_value=0), \
             patch("ovp_pipeline.commands.merge_identities.main",
                   return_value=0):
            rsa.main(["--vault-dir", str(vault)])
        lock = vault / "60-Logs" / rsa._LOCKFILE_NAME
        assert not lock.exists()


# ---------------------------------------------------------------------------
# Vault validation
# ---------------------------------------------------------------------------


class TestVaultValidation:
    def test_missing_vault_returns_2(self, tmp_path):
        rc = rsa.main(["--vault-dir", str(tmp_path / "no-such-dir")])
        assert rc == 2
