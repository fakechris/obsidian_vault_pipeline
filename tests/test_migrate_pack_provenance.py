from __future__ import annotations

import json
from pathlib import Path


def test_migrate_pack_provenance_dry_run_reports_log_only_changes(temp_vault, sample_evergreen_files):
    logs_dir = temp_vault / "60-Logs"
    transactions_dir = logs_dir / "transactions"
    transactions_dir.mkdir(parents=True, exist_ok=True)

    transaction_path = transactions_dir / "txn.json"
    transaction_path.write_text(
        json.dumps(
            {
                "id": "txn-1",
                "description": "Full pipeline (default-knowledge/full)",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    pipeline_log = logs_dir / "pipeline.jsonl"
    pipeline_log.write_text(
        json.dumps({"event_type": "graph", "pack": "default-knowledge"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    from openclaw_pipeline.migrate_pack_provenance import migrate_pack_provenance

    result = migrate_pack_provenance(temp_vault, from_pack="default-knowledge", write=False)

    assert result["from_pack"] == "default-knowledge"
    assert result["to_pack"] == "research-tech"
    assert result["files_changed"] == 2
    assert result["replacements"] >= 2
    assert "default-knowledge" in transaction_path.read_text(encoding="utf-8")
    assert "default-knowledge" in pipeline_log.read_text(encoding="utf-8")
    evergreen_path = temp_vault / "10-Knowledge" / "Evergreen" / "DCF-Valuation.md"
    assert "default-knowledge" not in evergreen_path.read_text(encoding="utf-8")


def test_migrate_pack_provenance_write_updates_logs_without_touching_notes(temp_vault, sample_evergreen_files):
    logs_dir = temp_vault / "60-Logs"
    transactions_dir = logs_dir / "transactions"
    transactions_dir.mkdir(parents=True, exist_ok=True)

    transaction_path = transactions_dir / "txn.json"
    transaction_path.write_text(
        json.dumps(
            {
                "id": "txn-1",
                "description": "Full pipeline (default-knowledge/full)",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report_path = logs_dir / "pipeline-reports" / "pipeline-report-demo.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("pack=default-knowledge\n", encoding="utf-8")
    pipeline_log = logs_dir / "pipeline.jsonl"
    pipeline_log.write_text(
        json.dumps({"event_type": "graph", "pack": "default-knowledge"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    evergreen_path = temp_vault / "10-Knowledge" / "Evergreen" / "DCF-Valuation.md"
    original_evergreen = evergreen_path.read_text(encoding="utf-8")

    from openclaw_pipeline.migrate_pack_provenance import migrate_pack_provenance

    result = migrate_pack_provenance(temp_vault, from_pack="default-knowledge", write=True)

    assert result["files_changed"] == 3
    assert "research-tech/full" in transaction_path.read_text(encoding="utf-8")
    assert "research-tech" in pipeline_log.read_text(encoding="utf-8")
    assert "research-tech" in report_path.read_text(encoding="utf-8")
    assert evergreen_path.read_text(encoding="utf-8") == original_evergreen


def test_migrate_pack_provenance_command_emits_json(temp_vault, capsys):
    logs_dir = temp_vault / "60-Logs"
    transactions_dir = logs_dir / "transactions"
    transactions_dir.mkdir(parents=True, exist_ok=True)
    (transactions_dir / "txn.json").write_text(
        json.dumps({"description": "Full pipeline (default-knowledge/full)"}, ensure_ascii=False),
        encoding="utf-8",
    )

    from openclaw_pipeline.commands.migrate_pack_provenance import main

    exit_code = main(["--vault-dir", str(temp_vault), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["to_pack"] == "research-tech"
    assert payload["files_changed"] == 1


def test_migrate_pack_provenance_replaces_only_token_bound_occurrences(temp_vault):
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    sample = logs_dir / "sample.md"
    sample.write_text(
        "default-knowledge default-knowledge/full xdefault-knowledgey\n",
        encoding="utf-8",
    )

    from openclaw_pipeline.migrate_pack_provenance import migrate_pack_provenance

    result = migrate_pack_provenance(temp_vault, from_pack="default-knowledge", write=True)
    content = sample.read_text(encoding="utf-8")

    assert result["files_changed"] == 1
    assert result["replacements"] == 2
    assert "research-tech/full" in content
    assert "xdefault-knowledgey" in content


def test_migrate_pack_provenance_skips_unreadable_files(temp_vault, monkeypatch):
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    broken = logs_dir / "broken.json"
    broken.write_text('{"pack":"default-knowledge"}', encoding="utf-8")

    original_read_text = Path.read_text

    def patched_read_text(self, *args, **kwargs):
        if self == broken:
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "broken")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", patched_read_text)

    from openclaw_pipeline.migrate_pack_provenance import migrate_pack_provenance

    result = migrate_pack_provenance(temp_vault, from_pack="default-knowledge", write=True)

    assert result["files_changed"] == 0
    assert len(result["errors"]) == 1
    assert result["errors"][0]["path"].endswith("broken.json")
