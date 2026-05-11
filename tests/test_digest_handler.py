"""Tests for M20 / BL-077 daily digest handler."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ovp_pipeline.commands.digest_handler import (
    _build_digest_user_prompt,
    _collect_digest_inputs,
    _enqueue_daily,
    _latest_digest,
    handle_digest,
    main,
)
from ovp_pipeline.commands.task_dispatch import TaskContext


# ── Fixtures ──────────────────────────────────────────────────────


class _FakeLLM:
    def __init__(self, response: str = "stub digest body") -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def call(self, system_prompt: str, user_prompt: str,
             max_tokens: int = 0) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.response


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "50-Inbox" / "02-Tasks").mkdir(parents=True)
    (vault / "40-Resources" / "Generated" / "digests").mkdir(parents=True)
    (vault / "70-Archive" / "tasks").mkdir(parents=True)
    (vault / "60-Logs").mkdir(parents=True)
    return vault


def _seed_knowledge_db(
    vault: Path,
    *,
    pack: str = "research-tech",
    tensions: int = 0,
    themes: int = 0,
    open_qs: int = 0,
) -> None:
    """Create the three crystal tables and seed them with minimal
    deterministic rows.  Bypasses the full truth_store schema since
    the digest handler only reads these specific columns."""
    db_path = vault / "60-Logs" / "knowledge.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE community_crystals (
              pack TEXT NOT NULL,
              cluster_id TEXT NOT NULL,
              body_md TEXT NOT NULL,
              source_evergreen_slugs_json TEXT NOT NULL,
              synthesized_at TEXT NOT NULL,
              llm_model TEXT NOT NULL,
              prompt_version TEXT NOT NULL,
              superseded_by_synthesized_at TEXT NOT NULL DEFAULT '',
              PRIMARY KEY (pack, cluster_id, synthesized_at)
            );
            CREATE TABLE contradiction_crystals (
              pack TEXT NOT NULL,
              contradiction_id TEXT NOT NULL,
              subject_key TEXT NOT NULL,
              body_md TEXT NOT NULL,
              positive_claim_ids_json TEXT NOT NULL,
              negative_claim_ids_json TEXT NOT NULL,
              source_object_ids_json TEXT NOT NULL,
              synthesized_at TEXT NOT NULL,
              llm_model TEXT NOT NULL,
              prompt_version TEXT NOT NULL,
              superseded_by_synthesized_at TEXT NOT NULL DEFAULT '',
              PRIMARY KEY (pack, contradiction_id, synthesized_at)
            );
            CREATE TABLE crystal_scores (
              pack TEXT NOT NULL,
              crystal_kind TEXT NOT NULL,
              crystal_id TEXT NOT NULL,
              score REAL NOT NULL,
              size_norm REAL NOT NULL DEFAULT 0,
              credibility_norm REAL NOT NULL DEFAULT 0,
              contradiction_norm REAL NOT NULL DEFAULT 0,
              reuse_recency_norm REAL NOT NULL DEFAULT 0,
              evergreen_recency_norm REAL NOT NULL DEFAULT 0,
              source_diversity_norm REAL NOT NULL DEFAULT 0,
              computed_at TEXT NOT NULL,
              PRIMARY KEY (pack, crystal_kind, crystal_id)
            );
            CREATE TABLE graph_clusters (
              pack TEXT NOT NULL,
              cluster_id TEXT NOT NULL,
              cluster_kind TEXT NOT NULL,
              label TEXT NOT NULL,
              center_object_id TEXT NOT NULL DEFAULT '',
              member_object_ids_json TEXT NOT NULL DEFAULT '[]',
              score REAL NOT NULL DEFAULT 0,
              PRIMARY KEY (pack, cluster_id)
            );
            """
        )

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        for i in range(tensions):
            cid = f"contradict::t{i}"
            conn.execute(
                "INSERT INTO contradiction_crystals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    pack, cid, f"subject {i}",
                    f"body of tension {i}", "[]", "[]", "[]",
                    now, "test-model", "v1", "",
                ),
            )
            conn.execute(
                "INSERT INTO crystal_scores VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    pack, "contradiction", cid,
                    0.9 - 0.1 * i,  # descending scores
                    0.5, 0.5, 0.5, 0.0, 0.5, 0.5, now,
                ),
            )

        for i in range(themes):
            cluster_id = f"cluster::theme-{i}"
            conn.execute(
                "INSERT INTO graph_clusters VALUES (?,?,?,?,?,?,?)",
                (
                    pack, cluster_id, "louvain_community",
                    f"Theme {i}", "", "[]", 1.0,
                ),
            )
            conn.execute(
                "INSERT INTO community_crystals VALUES (?,?,?,?,?,?,?,?)",
                (
                    pack, cluster_id, f"body of theme {i}", "[]",
                    now, "test-model", "v1", "",
                ),
            )

        for i in range(open_qs):
            cid = f"contradict::oq-{i}"
            conn.execute(
                "INSERT INTO contradiction_crystals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    pack, cid, f"open question {i}",
                    f"body of open question {i}", "[]", "[]", "[]",
                    now, "test-model", "v1", "",
                ),
            )

        conn.commit()
    finally:
        conn.close()


# ── _collect_digest_inputs ───────────────────────────────────────


def test_collect_returns_empty_when_db_missing(tmp_path: Path):
    vault = _make_vault(tmp_path)
    out = _collect_digest_inputs(vault, "research-tech")
    assert out == {"tensions": [], "themes": [], "open_questions": []}


def test_collect_returns_top_tensions_descending_by_score(tmp_path: Path):
    vault = _make_vault(tmp_path)
    _seed_knowledge_db(vault, tensions=5)
    out = _collect_digest_inputs(vault, "research-tech")
    assert len(out["tensions"]) == 3  # TOP_TENSIONS_N
    scores = [t["score"] for t in out["tensions"]]
    assert scores == sorted(scores, reverse=True)


def test_collect_does_not_double_count_open_questions(tmp_path: Path):
    """An open question that already appears in ``tensions`` (top-
    scoring contradictions) is excluded from ``open_questions`` so
    the digest doesn't surface the same contradiction twice."""
    vault = _make_vault(tmp_path)
    _seed_knowledge_db(vault, tensions=3, open_qs=0)
    out = _collect_digest_inputs(vault, "research-tech")
    tension_ids = {t["id"] for t in out["tensions"]}
    open_ids = {q["id"] for q in out["open_questions"]}
    assert not (tension_ids & open_ids)


def test_collect_recent_themes_only(tmp_path: Path):
    vault = _make_vault(tmp_path)
    _seed_knowledge_db(vault, themes=2)
    out = _collect_digest_inputs(vault, "research-tech")
    assert len(out["themes"]) == 2
    assert all("Theme" in t["label"] for t in out["themes"])


# ── handle_digest ────────────────────────────────────────────────


def test_handle_digest_empty_vault_skips_llm(tmp_path: Path):
    """Empty vault: handler must NOT call the LLM (token waste) and
    must emit a stub digest with a "nothing new to surface" body."""
    vault = _make_vault(tmp_path)
    llm = _FakeLLM("should-not-be-called")
    task = vault / "50-Inbox" / "02-Tasks" / "DIGEST-daily.md"
    task.write_text("auto-enqueued", encoding="utf-8")

    ctx = TaskContext(
        vault_dir=vault, task_path=task, prefix="DIGEST",
        slug="daily", body="", pack="research-tech", llm_client=llm,
    )
    result = handle_digest(ctx)

    assert llm.calls == []  # LLM untouched
    assert "Nothing new to surface" in result.body_md
    assert result.subdir == "digests"
    assert result.metadata == {
        "tensions": 0, "themes": 0, "open_questions": 0,
    }


def test_handle_digest_with_data_calls_llm(tmp_path: Path):
    vault = _make_vault(tmp_path)
    _seed_knowledge_db(vault, tensions=2, themes=2, open_qs=0)
    llm = _FakeLLM(
        "## Tensions worth sitting with\nA vs B.\n\n"
        "## Themes you keep circling\nTheme zero recurred.\n"
    )
    task = vault / "50-Inbox" / "02-Tasks" / "DIGEST-daily.md"
    task.write_text("", encoding="utf-8")

    ctx = TaskContext(
        vault_dir=vault, task_path=task, prefix="DIGEST",
        slug="daily", body="", pack="research-tech", llm_client=llm,
    )
    result = handle_digest(ctx)

    assert len(llm.calls) == 1
    sys_prompt, user_prompt = llm.calls[0]
    assert "daily-digest handler" in sys_prompt
    assert "subject 0" in user_prompt
    assert "Theme 0" in user_prompt
    assert "A vs B" in result.body_md
    assert "Digest —" in result.body_md
    assert result.metadata["tensions"] == 2
    assert result.metadata["themes"] == 2


def test_handle_digest_injects_user_focus(tmp_path: Path):
    vault = _make_vault(tmp_path)
    (vault / "00-Polaris").mkdir(exist_ok=True)
    (vault / "00-Polaris" / "USER.md").write_text(
        "# About Me\nFocus: memory systems.\n", encoding="utf-8",
    )
    _seed_knowledge_db(vault, tensions=1, themes=1, open_qs=0)
    llm = _FakeLLM("composed")
    task = vault / "50-Inbox" / "02-Tasks" / "DIGEST-daily.md"
    task.write_text("", encoding="utf-8")

    ctx = TaskContext(
        vault_dir=vault, task_path=task, prefix="DIGEST",
        slug="daily", body="", pack="research-tech", llm_client=llm,
    )
    handle_digest(ctx)

    sys_prompt, user_prompt = llm.calls[0]
    # USER.md profile shows up in both the system prefix (via
    # llm_prefix) and the user prompt (via load_user_profile direct
    # injection in _build_digest_user_prompt).
    assert "Focus: memory systems" in sys_prompt
    assert "Focus: memory systems" in user_prompt


# ── _build_digest_user_prompt formatting ────────────────────────


def test_build_user_prompt_omits_empty_sections():
    prompt = _build_digest_user_prompt(
        {"tensions": [], "themes": [], "open_questions": []},
        user_focus="",
    )
    assert "(none)" in prompt
    # No tension / theme content because all lists empty.
    assert "score" not in prompt.lower()


def test_build_user_prompt_includes_user_focus():
    prompt = _build_digest_user_prompt(
        {"tensions": [], "themes": [], "open_questions": []},
        user_focus="Hyperfocus on agent memory.",
    )
    assert "Hyperfocus on agent memory." in prompt


# ── CLI ──────────────────────────────────────────────────────────


def test_enqueue_daily_creates_task_file(tmp_path: Path):
    vault = _make_vault(tmp_path)
    path = _enqueue_daily(vault)
    assert path.name == "DIGEST-daily.md"
    assert path.exists()
    # Idempotent: second call returns same path, doesn't overwrite.
    path.write_text("hand-edited content", encoding="utf-8")
    second = _enqueue_daily(vault)
    assert second == path
    assert path.read_text(encoding="utf-8") == "hand-edited content"


def test_latest_digest_returns_most_recent(tmp_path: Path):
    vault = _make_vault(tmp_path)
    folder = vault / "40-Resources" / "Generated" / "digests"
    (folder / "2026-05-09.md").write_text("# old", encoding="utf-8")
    (folder / "2026-05-11.md").write_text("# new", encoding="utf-8")
    (folder / "2026-05-10.md").write_text("# mid", encoding="utf-8")
    latest = _latest_digest(vault)
    assert latest is not None
    assert latest.name == "2026-05-11.md"


def test_latest_digest_returns_none_when_empty(tmp_path: Path):
    vault = _make_vault(tmp_path)
    assert _latest_digest(vault) is None


def test_cli_enqueue_daily(tmp_path: Path, capsys):
    vault = _make_vault(tmp_path)
    rc = main(["--vault-dir", str(vault), "--enqueue-daily"])
    assert rc == 0
    assert (vault / "50-Inbox" / "02-Tasks" / "DIGEST-daily.md").exists()
    out = capsys.readouterr().out
    assert "enqueued" in out


def test_cli_show_latest_empty(tmp_path: Path, capsys):
    vault = _make_vault(tmp_path)
    rc = main(["--vault-dir", str(vault), "--show-latest"])
    assert rc == 1
    assert "(no digests yet)" in capsys.readouterr().out


def test_cli_requires_action(tmp_path: Path):
    vault = _make_vault(tmp_path)
    with pytest.raises(SystemExit):
        main(["--vault-dir", str(vault)])
