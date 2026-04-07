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
