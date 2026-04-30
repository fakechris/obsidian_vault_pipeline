from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

from ovp_pipeline.commands.prime import (
    SESSION_SNAPSHOT_DIR,
    build_prime_context,
    main as prime_main,
)
from ovp_pipeline.knowledge_index import rebuild_knowledge_index
from ovp_pipeline.runtime import VaultLayout


def test_build_prime_context_writes_session_snapshot_and_latest(temp_vault):
    output = build_prime_context(
        temp_vault,
        session_id="session-alpha",
        target_date=date(2026, 4, 30),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),
    )
    latest = temp_vault.joinpath(*SESSION_SNAPSHOT_DIR) / "latest.md"

    text = output.read_text(encoding="utf-8")

    assert output == temp_vault.joinpath(*SESSION_SNAPSHOT_DIR, "session-alpha.md")
    assert latest.read_text(encoding="utf-8") == text
    assert text.startswith("---\n")
    assert "type: session_snapshot" in text
    assert "session_id: session-alpha" in text
    assert "projection_kind: context_pack_projection" in text
    assert "projection_surface: ovp_prime" in text
    assert "source_context_pack: 60-Logs/working-memory/2026-04-30.md" in text
    assert "context_budget_tokens: 1200" in text
    assert "# OVP Prime — session-alpha" in text
    assert "## Working Memory Context" in text
    assert "# Working Memory — 2026-04-30" in text


def test_prime_context_uses_budgeted_working_memory(temp_vault):
    eg = temp_vault / "10-Knowledge" / "Evergreen"
    (eg / "Hot.md").write_text(
        "---\nnote_id: hot\ntitle: Hot\ntype: evergreen\ndate: 2026-04-30\n---\n# Hot\n",
        encoding="utf-8",
    )
    (eg / "Cold.md").write_text(
        "---\nnote_id: cold\ntitle: Cold\ntype: evergreen\ndate: 2026-04-30\n---\n# Cold\n",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    now = datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc)
    db = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO page_metrics "
            "(slug, last_seen_ts, reuse_count, citation_count) VALUES (?, ?, ?, ?)",
            [
                ("hot", int(now.timestamp()), 5, 12),
                ("cold", int(now.timestamp()), 1, 1),
            ],
        )
        conn.commit()

    output = build_prime_context(
        temp_vault,
        session_id="budgeted",
        target_date=now.date(),
        context_budget_tokens=10,
        now=now,
    )
    text = output.read_text(encoding="utf-8")

    assert "context_budget_tokens: 10" in text
    assert "- Selected objects: 1" in text
    assert "- Omitted by budget: 1" in text
    assert "[[hot]]" in text
    assert "[[cold]]" not in text

    events = [
        json.loads(line)
        for line in (temp_vault / "60-Logs" / "reuse-events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    prime_events = [event for event in events if event["surface"] == "ovp_prime"]
    assert len(prime_events) == 1
    assert prime_events[0]["session_id"] == "budgeted"
    assert prime_events[0]["source_slug"] == "hot"
    assert prime_events[0]["consumer_ref"] == "60-Logs/session-snapshots/budgeted.md"
    assert prime_events[0]["source_context_pack"] == "60-Logs/working-memory/2026-04-30.md"


def test_prime_cli_outputs_json_summary(temp_vault, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        [
            "ovp-prime",
            "--vault-dir",
            str(temp_vault),
            "--date",
            "2026-04-30",
            "--session-id",
            "cli-session",
            "--json",
        ],
    )

    rc = prime_main()
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["session_id"] == "cli-session"
    assert payload["path"].endswith("60-Logs/session-snapshots/cli-session.md")
    assert payload["latest_path"].endswith("60-Logs/session-snapshots/latest.md")
    assert payload["source_context_pack"].endswith("60-Logs/working-memory/2026-04-30.md")
