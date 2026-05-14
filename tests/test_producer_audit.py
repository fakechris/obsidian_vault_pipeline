"""Tests for the M24.2 producer audit.

The audit's job is to surface the instrumentation gap that
``docs/operational-lifecycle.md`` §Honest-zero calls out as
cause 3 (producer ran successfully but doesn't emit the audit row
we count).  These tests lock the contract registry, the verifier
behaviour, and the cross-module invariant that every kernel-read
event_type has at least one declared producer.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ovp_pipeline.event_evidence_registry import (
    classify,
    event_types_for_category,
)
from ovp_pipeline.producer_audit import (
    CONTRACTS,
    ProducerContract,
    all_declared_event_types,
    audit_against_log,
    producer_for_event_type,
)


_AUDIT_SCHEMA = """
CREATE TABLE audit_events (
    source_log TEXT NOT NULL,
    event_type TEXT NOT NULL,
    slug TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL
);
"""


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_AUDIT_SCHEMA)
    return conn


def _emit(conn, event_type, *, ts=None, slug=""):
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
        ("pipeline.jsonl", event_type, slug, "test", ts, "{}"),
    )
    conn.commit()


# ── Registry shape ────────────────────────────────────────────────


def test_contracts_have_unique_producer_names():
    """A duplicate producer name would make ``producer_for_event_type``
    nondeterministic and break the audit output's grouping."""
    names = [c.producer for c in CONTRACTS]
    assert len(names) == len(set(names))


def test_every_must_emit_event_type_is_registered():
    """A producer that declares an event the registry doesn't know
    about is a bug — the kernel wouldn't classify the row."""
    for contract in CONTRACTS:
        for et in contract.must_emit:
            assert classify(et) is not None, (
                f"{contract.producer} declares {et!r} but it is not "
                "in event_evidence_registry"
            )


def test_every_may_emit_event_type_is_registered():
    """Same invariant for branch-governed events."""
    for contract in CONTRACTS:
        for et in contract.may_emit:
            assert classify(et) is not None, (
                f"{contract.producer} declares {et!r} but it is not "
                "in event_evidence_registry"
            )


def test_kernel_read_events_have_a_producer():
    """The lifecycle kernel branches on a known set of event_types
    (Accepted / NeedsAction / Synthesized signals).  Every one of
    them must have a hot-path producer declared here — otherwise
    the kernel reads a row no producer ever writes, which is the
    exact bug M24.2 exists to prevent.
    """
    # Pulled from ops_lifecycle.py:_state_for_event_type
    kernel_reads = {
        "evergreen_auto_promoted",
        "promote_concept",
        "candidates_upserted",
        "evergreen_extraction_complete",
        "absorb_route_decision",
        "community_crystal_synthesized",
        "absorb_pending_upsert",
    }
    declared = all_declared_event_types(include_may=True)
    missing = kernel_reads - declared
    assert not missing, (
        f"Kernel reads {missing} but no hot-path producer declares "
        "them.  Add a contract to producer_audit.CONTRACTS."
    )


def test_producer_for_event_type_returns_first_match():
    contract = producer_for_event_type("article_intake_only")
    assert contract is not None
    assert contract.producer == "auto_article_processor"


def test_producer_for_event_type_returns_none_for_unknown():
    assert producer_for_event_type("never_emitted_anywhere_x") is None


# ── Audit verifier ────────────────────────────────────────────────


def test_audit_flags_every_must_emit_as_missing_on_empty_log():
    """No producer ever ran → every must-emit appears as missing."""
    conn = _make_db()
    report = audit_against_log(conn, window_days=7)
    missing = [f for f in report.findings if f.severity == "missing"]
    assert missing, "empty log should produce missing findings"
    # Spot-check: the article processor's must_emit appears.
    types = {f.event_type for f in missing}
    assert "article_intake_only" in types


def test_audit_marks_emitted_events_ok():
    conn = _make_db()
    _emit(conn, "article_intake_only", slug="src-x")
    report = audit_against_log(conn, window_days=7)
    finding = next(
        f for f in report.findings
        if f.event_type == "article_intake_only"
    )
    assert finding.severity == "ok"
    assert finding.count_in_window == 1
    assert finding.last_seen


def test_audit_window_excludes_old_rows():
    """A must-emit row outside the window still counts as missing."""
    conn = _make_db()
    old_ts = (
        datetime.now(timezone.utc) - timedelta(days=30)
    ).isoformat()
    _emit(conn, "article_intake_only", ts=old_ts)
    report = audit_against_log(conn, window_days=7)
    finding = next(
        f for f in report.findings
        if f.event_type == "article_intake_only"
    )
    assert finding.severity == "missing"
    assert finding.count_in_window == 0


def test_audit_does_not_flag_may_emit_absence():
    """may_emit events are branch-governed; absence is NOT a finding."""
    conn = _make_db()
    # Emit only the article processor's must_emit, none of its may_emit.
    _emit(conn, "article_intake_only")
    report = audit_against_log(conn, window_days=7)
    # No finding for ``article_error`` even though it's may_emit and absent.
    et_in_report = {f.event_type for f in report.findings}
    assert "article_error" not in et_in_report


def test_audit_flags_unknown_event_types_as_drift():
    """A row whose event_type isn't declared in any contract surfaces
    in ``unknown_event_types`` so the operator can investigate."""
    conn = _make_db()
    _emit(conn, "something_we_never_promised", slug="x")
    report = audit_against_log(conn, window_days=7)
    assert "something_we_never_promised" in report.unknown_event_types


def test_audit_groups_findings_per_producer():
    """Every must_emit event gets one finding row per producer; an
    extractor with 4 must_emit values produces 4 finding rows."""
    conn = _make_db()
    extractor_finds = [
        f for f in audit_against_log(conn, window_days=7).findings
        if f.producer == "auto_evergreen_extractor"
    ]
    # auto_evergreen_extractor.must_emit has 4 entries
    # (extraction_complete + absorb_pending_upsert +
    # candidates_upserted + evergreen_auto_promoted).
    assert len(extractor_finds) == 4


# ── Cross-surface integration ─────────────────────────────────────


def test_intake_category_events_all_have_a_producer():
    """Every user-visible intake event in the registry should have
    a hot-path producer.  Catches drift in either direction."""
    intake_types = set(event_types_for_category("intake"))
    declared = all_declared_event_types(include_may=True)
    # The intake category is the noisiest — only a subset is
    # hot-path-tracked in M24.2.  Assert at least the most-traffic
    # ones are covered.
    must_cover = {
        "article_intake_only",
        "clippings_processed",
        "github_intake_completed",
    }
    assert must_cover <= intake_types
    assert must_cover <= declared
