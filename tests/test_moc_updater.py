from pathlib import Path

from openclaw_pipeline.auto_moc_updater import MOCUpdater, PipelineLogger


def test_update_area_moc_creates_missing_topics_moc(temp_vault):
    logger = PipelineLogger(temp_vault / "60-Logs" / "pipeline.jsonl")
    updater = MOCUpdater(temp_vault, logger)

    deep_dive = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "2026-04-01_Test_深度解读.md"
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text("# Test\n", encoding="utf-8")

    moc_path = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "AI MOC.md"
    assert moc_path.exists() is False

    result = updater.update_area_moc("AI-Research", dry_run=False)

    assert moc_path.exists()
    content = moc_path.read_text(encoding="utf-8")
    assert "[[2026-04-01_Test_深度解读]]" in content
    assert result["files_added"] == 1
    assert result["errors"] == []
