from __future__ import annotations

from openclaw_pipeline import rebuild_registry as rebuild_source
from openclaw_pipeline import repair as repair_source
from openclaw_pipeline.commands import migrate_broken_links as migrate_command
from openclaw_pipeline.commands import rebuild_registry as rebuild_command
from openclaw_pipeline.commands import repair as repair_command
from openclaw_pipeline.concept_registry import ConceptEntry, ConceptRegistry
from openclaw_pipeline.migrate_broken_links import scan_broken_mentions


def test_rebuild_registry_reconcile_reports_drift(temp_vault, sample_evergreen_files):
    registry = ConceptRegistry(temp_vault)
    registry.add_entry(
        ConceptEntry(
            slug="orphan-entry",
            title="Orphan Entry",
            aliases=[],
            definition="No file",
            area="testing",
        )
    )
    registry.save()

    result = rebuild_source.reconcile_registry(temp_vault, write=False)

    missing_from_registry = {item["slug"] for item in result["not_in_registry"]}
    missing_from_fs = {item["slug"] for item in result["not_in_filesystem"]}

    assert "DCF-Valuation" in missing_from_registry
    assert "orphan-entry" in missing_from_fs


def test_reconcile_registry_respects_candidate_files_and_prunes_stale_on_write(temp_vault, sample_evergreen_files):
    candidate_dir = temp_vault / "10-Knowledge" / "Evergreen" / "_Candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    (candidate_dir / "live-candidate.md").write_text("# live candidate\n", encoding="utf-8")

    registry = ConceptRegistry(temp_vault)
    registry.add_entry(
        ConceptEntry(
            slug="live-candidate",
            title="Live Candidate",
            aliases=[],
            definition="has candidate file",
            area="testing",
            status="candidate",
        )
    )
    registry.add_entry(
        ConceptEntry(
            slug="stale-candidate",
            title="Stale Candidate",
            aliases=[],
            definition="no candidate file",
            area="testing",
            status="candidate",
        )
    )
    registry.save()

    dry_run = rebuild_source.reconcile_registry(temp_vault, write=False)
    missing_from_fs = {item["slug"] for item in dry_run["not_in_filesystem"]}

    assert "live-candidate" not in missing_from_fs
    assert "stale-candidate" in missing_from_fs

    rebuild_source.reconcile_registry(temp_vault, write=True)
    reloaded = ConceptRegistry(temp_vault).load()
    slugs = {entry.slug for entry in reloaded.entries}

    assert "live-candidate" in slugs
    assert "stale-candidate" not in slugs


def test_reconcile_registry_write_returns_clean_diff_after_pruning(temp_vault, sample_evergreen_files):
    registry = ConceptRegistry(temp_vault)
    registry.add_entry(
        ConceptEntry(
            slug="orphan-entry",
            title="Orphan Entry",
            aliases=[],
            definition="No file",
            area="testing",
            status="active",
        )
    )
    registry.save()

    result = rebuild_source.reconcile_registry(temp_vault, write=True)

    assert result["not_in_registry"] == []
    assert result["not_in_filesystem"] == []
    assert result["orphan_registry_entries"] == []


def test_rebuild_registry_command_wrapper_reexports_source():
    assert rebuild_command.rebuild_registry is rebuild_source.rebuild_registry
    assert rebuild_command.reconcile_registry is rebuild_source.reconcile_registry


def test_migrate_links_command_wrapper_reexports_source(temp_vault, sample_evergreen_files, sample_article):
    mentions_from_source = scan_broken_mentions(temp_vault)
    mentions_from_wrapper = migrate_command.scan_broken_mentions(temp_vault)

    assert [m.surface for m in mentions_from_wrapper] == [m.surface for m in mentions_from_source]


def test_repair_wrapper_reexports_command_source():
    assert repair_source.repair_autopilot is repair_command.repair_autopilot
    assert repair_source.repair_transactions is repair_command.repair_transactions
