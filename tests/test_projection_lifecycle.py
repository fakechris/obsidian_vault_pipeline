from __future__ import annotations

from datetime import datetime, timezone


def test_projection_repair_marker_supersedes_narrower_open_marker(temp_vault):
    from ovp_pipeline.projection_lifecycle import (
        list_projection_repair_markers,
        write_projection_repair_marker,
    )

    first = write_projection_repair_marker(
        temp_vault,
        kind="metadata_only",
        scope={"projection_kind": "knowledge_db", "object_ids": ["alpha"]},
        reason="missing evidence span columns",
        caused_by="doctor_check",
        authority_schema_version=1,
        projection_schema_version=1,
        created_at=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),
    )
    second = write_projection_repair_marker(
        temp_vault,
        kind="full_rebuild",
        scope={"projection_kind": "knowledge_db"},
        reason="schema incompatible",
        caused_by="schema_check",
        authority_schema_version=2,
        projection_schema_version=1,
        created_at=datetime(2026, 4, 30, 12, 1, tzinfo=timezone.utc),
    )

    markers = list_projection_repair_markers(temp_vault)

    assert [marker.marker_id for marker in markers] == [first.marker_id, second.marker_id]
    assert markers[0].superseded_by == second.marker_id
    assert markers[0].status == "superseded"
    assert markers[1].status == "open"
    assert markers[1].kind == "full_rebuild"


def test_projection_repair_marker_claim_uses_expiring_lease(temp_vault):
    from ovp_pipeline.projection_lifecycle import (
        claim_projection_repair_marker,
        list_projection_repair_markers,
        write_projection_repair_marker,
    )

    marker = write_projection_repair_marker(
        temp_vault,
        kind="full_rebuild",
        scope={"projection_kind": "knowledge_db"},
        reason="knowledge db missing",
        caused_by="startup_check",
        authority_schema_version=1,
        projection_schema_version=0,
        created_at=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),
    )

    claimed = claim_projection_repair_marker(
        temp_vault,
        marker.marker_id,
        worker_id="worker-a",
        lease_seconds=60,
        now=datetime(2026, 4, 30, 12, 1, tzinfo=timezone.utc),
    )
    blocked = claim_projection_repair_marker(
        temp_vault,
        marker.marker_id,
        worker_id="worker-b",
        lease_seconds=60,
        now=datetime(2026, 4, 30, 12, 1, 30, tzinfo=timezone.utc),
    )
    reclaimed = claim_projection_repair_marker(
        temp_vault,
        marker.marker_id,
        worker_id="worker-b",
        lease_seconds=60,
        now=datetime(2026, 4, 30, 12, 2, 1, tzinfo=timezone.utc),
    )

    assert claimed is not None
    assert blocked is None
    assert reclaimed is not None
    [current] = list_projection_repair_markers(temp_vault)
    assert current.claimed_by == "worker-b"
    assert current.claim_lease_until == datetime(2026, 4, 30, 12, 3, 1, tzinfo=timezone.utc)


def test_projection_schema_mismatch_writes_full_rebuild_marker(temp_vault):
    from ovp_pipeline.projection_lifecycle import (
        ensure_projection_schema_current,
        list_projection_repair_markers,
    )

    marker = ensure_projection_schema_current(
        temp_vault,
        projection_kind="knowledge_db",
        current_authority_schema_version=3,
        projection_schema_version=2,
        caused_by="startup_check",
        now=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),
    )

    assert marker is not None
    assert marker.kind == "full_rebuild"
    assert marker.reason == "authority_schema_version_newer_than_projection"
    assert marker.scope == {"projection_kind": "knowledge_db"}
    assert marker.authority_schema_version == 3
    assert marker.projection_schema_version == 2
    assert list_projection_repair_markers(temp_vault)[0].marker_id == marker.marker_id


def test_projection_schema_match_does_not_write_marker(temp_vault):
    from ovp_pipeline.projection_lifecycle import (
        ensure_projection_schema_current,
        list_projection_repair_markers,
    )

    marker = ensure_projection_schema_current(
        temp_vault,
        projection_kind="knowledge_db",
        current_authority_schema_version=2,
        projection_schema_version=2,
        caused_by="startup_check",
        now=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),
    )

    assert marker is None
    assert list_projection_repair_markers(temp_vault) == []
