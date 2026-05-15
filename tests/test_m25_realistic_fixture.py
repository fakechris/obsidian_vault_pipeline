"""M25.6 realistic-fixture dogfood acceptance.

Pre-M25.6 every M24/M25 test used a hand-built fixture that
seeded ONE source per state.  That's enough to prove the kernel
classifies correctly, but it doesn't prove the operator-visible
*contracts* hold under realistic data volumes:

* card N === items-page N for high-volume states
* card secondary N === audit-page N when event_types span
  multiple registry rows
* honest-zero appears on empty states
* `/ops/events/audit` banners both render against a real-shape
  payload (not a hand-built one)

This module builds a single fixture vault that mirrors what an
operator sees after a normal week: ~50 sources, ~20 evergreens,
several clusters with mixed-freshness crystals, a handful of
failures and operator promotions, plus today's intake.  Every
M24/M25 contract is then asserted against that one fixture so a
regression on any layer breaks loudly.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ovp_pipeline.ops_lifecycle import (
    ALL_STATES,
    STATE_ACCEPTED,
    STATE_EXTRACTED,
    STATE_NEEDS_ACTION,
    STATE_RECEIVED,
    STATE_SYNTHESIZED,
)
from ovp_pipeline.ops_state import rebuild
from ovp_pipeline.ui.view_models import (
    build_events_audit_payload,
    build_items_list_payload,
    build_today_digest_payload,
)


PACK = "research-tech"


# Shape constants — tweak in one place if the fixture grows.
N_RECEIVED = 12   # raw intake today, not yet extracted
N_EXTRACTED = 8   # extraction complete, candidates upserted, not promoted
N_ACCEPTED = 18   # promoted (auto + operator)
N_SYNTHESIZED = 5  # in synthesized clusters
N_NEEDS_ACTION = 4  # failures + open contradictions


_SCHEMA = """
CREATE TABLE audit_events (
    source_log TEXT NOT NULL,
    event_type TEXT NOT NULL,
    slug TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL
);
CREATE INDEX idx_audit_events_log ON audit_events(source_log);
CREATE INDEX idx_audit_events_type ON audit_events(event_type);
CREATE TABLE objects (
    pack TEXT NOT NULL,
    object_id TEXT NOT NULL,
    object_kind TEXT NOT NULL,
    title TEXT NOT NULL,
    canonical_path TEXT NOT NULL,
    source_slug TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (pack, object_id)
);
CREATE TABLE graph_clusters (
    pack TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    cluster_kind TEXT NOT NULL,
    label TEXT NOT NULL,
    center_object_id TEXT NOT NULL,
    member_object_ids_json TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (pack, cluster_id)
);
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
CREATE TABLE evergreen_revisions (
    pack TEXT NOT NULL,
    object_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    content_md TEXT NOT NULL,
    change_type TEXT NOT NULL,
    changed_by TEXT NOT NULL DEFAULT '',
    derived_at TEXT NOT NULL,
    change_note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (pack, object_id, version)
);
CREATE TABLE pages_index (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    note_type TEXT NOT NULL,
    path TEXT NOT NULL,
    day_id TEXT NOT NULL,
    frontmatter_json TEXT NOT NULL,
    body TEXT NOT NULL
);
"""


def _emit(conn, event_type, slug, ts, payload):
    conn.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
        ("pipeline.jsonl", event_type, slug, "fixture",
         ts, json.dumps(payload)),
    )


@pytest.fixture(scope="module")
def realistic_vault(tmp_path_factory) -> Path:
    """Build a vault-shaped fixture exercising every lifecycle
    state with believable volumes.  Module-scoped so all
    acceptance assertions read the same materialised projection.
    """
    vault_dir = tmp_path_factory.mktemp("realistic_vault")
    db_path = vault_dir / "60-Logs" / "knowledge.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)

    today = datetime.now(timezone.utc)
    today_iso = today.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    yesterday_iso = (today - timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )
    last_week_iso = (today - timedelta(days=7)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )

    # ── Received (today's intake, not yet extracted) ──────────────
    for i in range(N_RECEIVED):
        slug = f"recv-src-{i:03d}"
        _emit(conn, "article_intake_only", slug, today_iso,
              {"slug": slug, "file": f"{slug}.md"})

    # ── Extracted (intake + absorb_route_decision + extraction
    # complete + candidates_upserted, but NO promote) ──────────────
    for i in range(N_EXTRACTED):
        slug = f"ext-src-{i:03d}"
        _emit(conn, "article_intake_only", slug, yesterday_iso,
              {"slug": slug, "file": f"{slug}.md"})
        _emit(conn, "absorb_route_decision", slug, yesterday_iso,
              {"slug": slug, "router_decision": "evergreen"})
        _emit(conn, "evergreen_extraction_complete", slug, today_iso,
              {"slug": slug, "concepts_extracted": 3})
        _emit(conn, "absorb_pending_upsert", slug, today_iso,
              {"slug": slug, "expected_candidates": 3})
        _emit(conn, "candidates_upserted", slug, today_iso,
              {"slug": slug, "candidates": 3})

    # ── Accepted (object rows that have promotion evidence) ──────
    # Mix of auto-promote (absorb-cat) and operator-promote
    # (governance-cat) to exercise the de-dup of the M25.3 +
    # M25.3-fix mapping.
    for i in range(N_ACCEPTED):
        slug = f"acc-obj-{i:03d}"
        conn.execute(
            "INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?, ?)",
            (PACK, slug, "evergreen", f"Accepted topic {i}",
             f"10-Knowledge/Evergreen/{slug}.md", slug, ""),
        )
        # Half via auto-promote, half via operator promote.
        event_type = "evergreen_auto_promoted" if i % 2 == 0 else "promote_concept"
        ts = today_iso if i < 4 else last_week_iso
        _emit(conn, event_type, slug, ts,
              {"slug": slug, "object_id": slug, "concept": slug})

    # ── Synthesized (cluster + active fresh crystal + members) ───
    members = [f"acc-obj-{i:03d}" for i in range(N_SYNTHESIZED)]
    crystal_ts = today_iso
    member_rev_ts = (today - timedelta(hours=12)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )
    cluster_id = "cluster::memory-systems"
    conn.execute(
        "INSERT INTO graph_clusters VALUES (?, ?, ?, ?, ?, ?, ?)",
        (PACK, cluster_id, "community", "Memory Systems",
         members[0], json.dumps(members), 0.8),
    )
    for m in members:
        conn.execute(
            "INSERT INTO evergreen_revisions "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (PACK, m, 1, "## body", "created", "fixture",
             member_rev_ts, ""),
        )
    conn.execute(
        "INSERT INTO community_crystals "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (PACK, cluster_id, "## crystal body",
         json.dumps(members), crystal_ts,
         "fake-model", "v1", ""),
    )
    _emit(conn, "community_crystal_synthesized", "", today_iso,
          {"cluster_id": cluster_id})

    # ── Needs Action (failures today + open contradictions) ──────
    for i in range(N_NEEDS_ACTION):
        slug = f"fail-src-{i:03d}"
        _emit(conn, "absorb_parse_error", slug, today_iso,
              {"slug": slug, "error": f"parse fail {i}"})

    # pages_index — feed the M25.2 source-href resolver for a
    # subset of Received sources so the smoke can verify both
    # branches.
    for i in range(0, N_RECEIVED, 4):
        slug = f"recv-src-{i:03d}"
        conn.execute(
            "INSERT INTO pages_index VALUES (?, ?, ?, ?, ?, ?, ?)",
            (slug, f"Article {i}", "interpretation",
             f"50-Inbox/01-Raw/{slug}.md", "2026-05-13", "{}", ""),
        )

    conn.commit()
    rebuild(conn, pack=PACK)
    conn.close()
    return vault_dir


# ── Acceptance: card-N === drilldown-N ────────────────────────────


def test_acceptance_received_card_n_equals_items_page_n(realistic_vault):
    digest = build_today_digest_payload(realistic_vault, pack_name=PACK)
    received = next(c for c in digest["cards"] if c["id"] == "Received")
    items = build_items_list_payload(
        realistic_vault, state="Received", pack_name=PACK, limit=1000,
    )
    assert received["primary_count"] == items["total"] == N_RECEIVED


def test_acceptance_extracted_card_n_equals_items_page_n(realistic_vault):
    digest = build_today_digest_payload(realistic_vault, pack_name=PACK)
    extracted = next(c for c in digest["cards"] if c["id"] == "Extracted")
    items = build_items_list_payload(
        realistic_vault, state="Extracted", pack_name=PACK, limit=1000,
    )
    assert extracted["primary_count"] == items["total"] == N_EXTRACTED


def test_acceptance_accepted_card_n_equals_items_page_n(realistic_vault):
    """M25.6 dogfood finding: the Accepted card counts both
    item kinds — the source whose promote event fired AND the
    object the projection materialised — because the kernel
    classifies each independently from the same evidence.

    For 18 promote events the count is 18 sources + 18 objects
    = 36.  Card N === items page N still holds (both read the
    same projection); the assertion locks the dual-count shape
    rather than the underlying 18.

    Open product question (logged in the acceptance report):
    do we want the cards to read 18 ("18 things accepted") or
    36 ("18 sources are now Accepted; 18 objects exist")?  M25.6
    surfaces the question; M26 picks the side."""
    digest = build_today_digest_payload(realistic_vault, pack_name=PACK)
    accepted = next(c for c in digest["cards"] if c["id"] == "Accepted")
    items = build_items_list_payload(
        realistic_vault, state="Accepted", pack_name=PACK, limit=1000,
    )
    assert accepted["primary_count"] == items["total"]
    # The dual-classification (source + object) means the count
    # is 2 * N_ACCEPTED.  Lock both so a regression that drops
    # one kind from ops_state fails loudly.
    assert accepted["primary_count"] == 2 * N_ACCEPTED
    # Items list carries both kinds.
    kinds_in_rows = {r["item_kind"] for r in items["rows"]}
    assert kinds_in_rows == {"source", "object"}


def test_acceptance_synthesized_card_n_equals_items_page_n(realistic_vault):
    digest = build_today_digest_payload(realistic_vault, pack_name=PACK)
    synth = next(c for c in digest["cards"] if c["id"] == "Synthesized")
    items = build_items_list_payload(
        realistic_vault, state="Synthesized", pack_name=PACK, limit=1000,
    )
    # Synthesized counts CLUSTERS (1 cluster in fixture).
    assert synth["primary_count"] == items["total"]
    assert synth["primary_count"] >= 1


def test_acceptance_needs_action_card_n_equals_items_page_n(realistic_vault):
    digest = build_today_digest_payload(realistic_vault, pack_name=PACK)
    na = next(c for c in digest["cards"] if c["id"] == "NeedsAction")
    items = build_items_list_payload(
        realistic_vault, state="NeedsAction", pack_name=PACK, limit=1000,
    )
    assert na["primary_count"] == items["total"] == N_NEEDS_ACTION


# ── Acceptance: card secondary N === audit-page N ─────────────────


def test_acceptance_received_secondary_matches_audit_page(realistic_vault):
    today_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    digest = build_today_digest_payload(
        realistic_vault, pack_name=PACK, target_date=today_date,
    )
    received = next(c for c in digest["cards"] if c["id"] == "Received")
    audit = build_events_audit_payload(
        realistic_vault,
        event_types=tuple(received["event_types"]),
        date_key=today_date,
        pack_name=PACK,
        limit=10_000,
    )
    assert received["event_count"] == audit["total"]


def test_acceptance_needs_action_secondary_matches_audit_page(realistic_vault):
    today_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    digest = build_today_digest_payload(
        realistic_vault, pack_name=PACK, target_date=today_date,
    )
    na = next(c for c in digest["cards"] if c["id"] == "NeedsAction")
    audit = build_events_audit_payload(
        realistic_vault,
        event_types=tuple(na["event_types"]),
        date_key=today_date,
        pack_name=PACK,
        limit=10_000,
    )
    assert na["event_count"] == audit["total"]


def test_acceptance_accepted_secondary_no_double_count(realistic_vault):
    """Half of the accepted items were promoted via
    ``evergreen_auto_promoted``, half via ``promote_concept``.
    Today only the first 4 fired; secondary count must equal 4
    (not 8 — that would mean we double-counted the promote pair)."""
    today_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    digest = build_today_digest_payload(
        realistic_vault, pack_name=PACK, target_date=today_date,
    )
    accepted = next(c for c in digest["cards"] if c["id"] == "Accepted")
    # 4 of N_ACCEPTED have today's timestamp (i < 4 in the fixture).
    assert accepted["event_count"] == 4


# ── Acceptance: samples come from items, not events ───────────────


def test_acceptance_received_samples_reference_items_not_events(realistic_vault):
    digest = build_today_digest_payload(realistic_vault, pack_name=PACK)
    received = next(c for c in digest["cards"] if c["id"] == "Received")
    assert received["samples"], "Received card has no samples"
    # Sample item_ids are ``recv-src-*``; they are NOT event_types.
    for s in received["samples"]:
        assert s["item_id"].startswith("recv-src-"), (
            f"Received sample is not an ops_state item: {s}"
        )
        assert s["item_kind"] == "source"


def test_acceptance_received_sample_with_pages_index_links_to_note(realistic_vault):
    """When pages_index has the slug, sample primary_href targets
    ``/note?path=…`` — a real route."""
    digest = build_today_digest_payload(realistic_vault, pack_name=PACK)
    received = next(c for c in digest["cards"] if c["id"] == "Received")
    linked = [s for s in received["samples"] if s["path"]]
    assert linked, "no Received samples carry a resolved primary_href"
    assert all(s["path"].startswith("/note?path=") for s in linked)


# ── Acceptance: honest-zero ───────────────────────────────────────


def test_acceptance_honest_zero_when_state_is_empty(tmp_path):
    """A vault with no audit_events and no objects produces 0s
    everywhere — but the page must surface honest-zero rather
    than just rendering blank."""
    from ovp_pipeline.commands._ui_renderers import _render_today_digest_page

    db_path = tmp_path / "60-Logs" / "knowledge.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    conn.commit()
    rebuild(conn, pack=PACK)
    conn.close()

    payload = build_today_digest_payload(tmp_path, pack_name=PACK)
    html = _render_today_digest_page(payload)
    # Every card shows 0 + the honest-zero ambiguity footer.
    assert "May mean: not run · no output · missing instrumentation" in html


# ── Acceptance: banners ───────────────────────────────────────────


def test_acceptance_audit_page_banner_against_realistic_payload(realistic_vault):
    """The role banner renders against a real-shape payload,
    not just a hand-built one."""
    from ovp_pipeline.commands._ui_renderers import _render_events_audit_page

    today_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    payload = build_events_audit_payload(
        realistic_vault,
        event_types=("article_intake_only",),
        date_key=today_date,
        pack_name=PACK,
    )
    html = _render_events_audit_page(payload)
    assert "Raw audit evidence" in html
    assert "/ops/events" in html


def test_acceptance_dossier_reciprocal_banner_against_realistic_payload(realistic_vault):
    """The /ops/events page renders the timeline-projection banner
    against the realistic fixture."""
    from ovp_pipeline.commands._ui_renderers import _render_events_page
    from ovp_pipeline.ui.view_models import build_event_dossier_payload

    payload = build_event_dossier_payload(realistic_vault)
    html = _render_events_page(payload)
    assert "Timeline projection view" in html
    assert "/ops/events/audit" in html


# ── Acceptance: ops_state stays consistent across rebuilds ────────


def test_acceptance_two_consecutive_rebuilds_yield_same_counts(realistic_vault):
    """If the operator hits ``ovp-ops-state --rebuild`` twice in
    quick succession (no new audit events between), the counts
    must be identical.  Stale-projection paranoia: a count that
    bounces between rebuilds is unsafe to drive a UI."""
    db_path = realistic_vault / "60-Logs" / "knowledge.db"

    def _counts():
        with sqlite3.connect(db_path) as conn:
            return dict(rebuild(conn, pack=PACK))

    first = _counts()
    second = _counts()
    assert first == second, (
        "ops_state.rebuild is not idempotent across consecutive "
        f"calls: first={first}, second={second}"
    )


# ── Acceptance: no state has a phantom row ────────────────────────


def test_acceptance_no_lifecycle_state_outside_the_five(realistic_vault):
    """Every row ``ops_state.rebuild`` writes must belong to one
    of the five visible states.  Anything else would mean the
    kernel emitted a state name the UI doesn't know how to
    render."""
    db_path = realistic_vault / "60-Logs" / "knowledge.db"
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT state FROM ops_state WHERE pack = ?",
            (PACK,),
        ).fetchall()
    states_in_db = {r[0] for r in rows}
    assert states_in_db <= set(ALL_STATES), (
        f"ops_state contains rows with unknown state: "
        f"{states_in_db - set(ALL_STATES)}"
    )
