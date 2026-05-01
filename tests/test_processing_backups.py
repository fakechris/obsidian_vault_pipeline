from ovp_pipeline.commands.cleanup_processing_backups import main as cleanup_main
from ovp_pipeline.processing_backups import (
    build_processed_source_lookup,
    cleanup_orphan_processing_backups,
)
from ovp_pipeline.runtime import VaultLayout


def test_cleanup_orphan_processing_backup_requires_processed_source_content(temp_vault):
    layout = VaultLayout.from_vault(temp_vault)
    layout.processing_dir.mkdir(parents=True, exist_ok=True)
    processed_dir = layout.processed_month_dir(__import__("datetime").datetime(2026, 4, 7))
    processed_dir.mkdir(parents=True, exist_ok=True)

    backup = layout.processing_dir / "Article.md.backup"
    processed = processed_dir / "Article.md"
    backup.write_text(
        "---\ntitle: Article\n---\n\n![remote](https://example.com/a.png)\n\nBody text.\n",
        encoding="utf-8",
    )
    processed.write_text(
        "---\ntitle: Article\n---\n\n![remote](attachments/2026-04/a.png)\n\nBody text.\n",
        encoding="utf-8",
    )

    checks = cleanup_orphan_processing_backups(layout, apply=True)

    assert len(checks) == 1
    assert checks[0].ok is True
    assert checks[0].processed_path == processed
    assert not backup.exists()
    assert processed.exists()


def test_cleanup_orphan_processing_backup_skips_missing_or_mismatched_processed_source(temp_vault):
    layout = VaultLayout.from_vault(temp_vault)
    layout.processing_dir.mkdir(parents=True, exist_ok=True)
    processed_dir = layout.processed_month_dir(__import__("datetime").datetime(2026, 4, 7))
    processed_dir.mkdir(parents=True, exist_ok=True)

    missing = layout.processing_dir / "Missing.md.backup"
    mismatch = layout.processing_dir / "Mismatch.md.backup"
    missing.write_text("# Missing\n\nBody text.\n", encoding="utf-8")
    mismatch.write_text("# Mismatch\n\nOriginal body.\n", encoding="utf-8")
    (processed_dir / "Mismatch.md").write_text("# Mismatch\n\nDifferent body.\n", encoding="utf-8")

    checks = cleanup_orphan_processing_backups(layout, apply=True)
    reasons = {check.backup_path.name: check.reason for check in checks}

    assert reasons == {
        "Missing.md.backup": "processed_match_missing",
        "Mismatch.md.backup": "content_mismatch",
    }
    assert missing.exists()
    assert mismatch.exists()


def test_cleanup_orphan_processing_backups_indexes_processed_sources_once(temp_vault, monkeypatch):
    import ovp_pipeline.processing_backups as processing_backups

    layout = VaultLayout.from_vault(temp_vault)
    layout.processing_dir.mkdir(parents=True, exist_ok=True)
    processed_dir = layout.processed_month_dir(__import__("datetime").datetime(2026, 4, 7))
    processed_dir.mkdir(parents=True, exist_ok=True)
    (layout.processing_dir / "One.md.backup").write_text("# One\n", encoding="utf-8")
    (layout.processing_dir / "Two.md.backup").write_text("# Two\n", encoding="utf-8")
    (processed_dir / "One.md").write_text("# One\n", encoding="utf-8")
    (processed_dir / "Two.md").write_text("# Two\n", encoding="utf-8")

    call_count = 0
    real_build_lookup = build_processed_source_lookup

    def counting_build_lookup(observed_layout):
        nonlocal call_count
        call_count += 1
        return real_build_lookup(observed_layout)

    monkeypatch.setattr(processing_backups, "build_processed_source_lookup", counting_build_lookup)

    checks = cleanup_orphan_processing_backups(layout, apply=False)

    assert call_count == 1
    assert [check.ok for check in checks] == [True, True]


def test_cleanup_processing_backups_command_reports_skips(temp_vault, capsys):
    layout = VaultLayout.from_vault(temp_vault)
    layout.processing_dir.mkdir(parents=True, exist_ok=True)
    (layout.processing_dir / "Missing.md.backup").write_text("# Missing\n", encoding="utf-8")

    exit_code = cleanup_main(["--vault-dir", str(temp_vault)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "total=1" in captured.out
    assert "skipped=1" in captured.out


def test_archive_source_to_processed_deletes_verified_processing_backup(temp_vault):
    from ovp_pipeline.auto_article_processor import AutoArticleProcessor, PipelineLogger, TransactionManager

    layout = VaultLayout.from_vault(temp_vault)
    layout.processing_dir.mkdir(parents=True, exist_ok=True)
    source = layout.processing_dir / "2026-04-07_Article.md"
    backup = layout.processing_dir / "2026-04-07_Article.md.backup"
    source.write_text(
        "---\ndate: 2026-04-07\n---\n\n![remote](attachments/2026-04/a.png)\n\nBody text.\n",
        encoding="utf-8",
    )
    backup.write_text(
        "---\ndate: 2026-04-07\n---\n\n![remote](https://example.com/a.png)\n\nBody text.\n",
        encoding="utf-8",
    )
    processor = AutoArticleProcessor(
        temp_vault,
        PipelineLogger(temp_vault / "60-Logs" / "pipeline.jsonl"),
        TransactionManager(temp_vault / "60-Logs" / "transactions"),
    )

    archived = processor._archive_source_to_processed(source)

    assert archived.exists()
    assert not source.exists()
    assert not backup.exists()
