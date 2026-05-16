"""Tests for the M24 PR-B audit-identity normalization layer.

These lock the hard boundary the operator flagged: source/file
identities must never leak into the object index, and object
slugs must never leak into the source slug column.  Real-vault
payload shapes (captured from the operator vault) are used so a
regression that re-narrows the keying fails loudly.
"""

from __future__ import annotations

from ovp_pipeline.audit_identity import (
    audit_cluster_ids,
    audit_object_ids,
    audit_slug_for_column,
    collect_string_values,
)


# ── collect_string_values (nested walk) ────────────────────────────


def test_collect_finds_nested_values():
    payload = {"mutation": {"object_id": "obj-x"}, "concept": "c1"}
    assert collect_string_values(payload, ("object_id",)) == {"obj-x"}
    assert collect_string_values(payload, ("concept",)) == {"c1"}


def test_collect_walks_lists():
    payload = {"items": [{"cluster_id": "a"}, {"cluster_id": "b"}]}
    assert collect_string_values(payload, ("cluster_id",)) == {"a", "b"}


def test_collect_ignores_non_string_and_empty():
    payload = {"object_id": "", "n": 5, "nested": {"object_id": None}}
    assert collect_string_values(payload, ("object_id",)) == set()


# ── audit_object_ids ───────────────────────────────────────────────


def test_object_ids_from_evergreen_auto_promoted_real_shape():
    """The dominant historical producer (10k+ rows) — carries
    ``concept`` + ``mutation.slug`` / ``mutation.target_slug``,
    NOT ``object_id``.  Before PR-B these were invisible to the
    object kernel."""
    payload = {
        "concept": "ai-video-production-workflow",
        "source": "2026-04-02_liangdabiao_Seedance2_深度解读.md",
        "path": "/Users/x/10-Knowledge/Evergreen/ai-video-production-workflow.md",
        "mutation": {
            "action": "promote",
            "slug": "ai-video-production-workflow",
            "target_slug": "ai-video-production-workflow",
        },
    }
    ids = audit_object_ids(payload)
    assert "ai-video-production-workflow" in ids


def test_object_ids_recover_merge_target():
    """promote-vs-merge fork: a merge re-points to a DIFFERENT
    existing slug.  Both the original concept and the merge
    target must be indexed."""
    payload = {
        "concept": "duplicate-idea",
        "mutation": {
            "action": "merge",
            "slug": "duplicate-idea",
            "target_slug": "canonical-idea",
        },
    }
    ids = audit_object_ids(payload)
    assert "duplicate-idea" in ids
    assert "canonical-idea" in ids


def test_object_ids_indexes_canonical_and_verbatim():
    payload = {"object_id": "Some Mixed Case ID"}
    ids = audit_object_ids(payload)
    assert "Some Mixed Case ID" in ids  # verbatim
    # canonical form also present so an objects.object_id lookup
    # (already canonical) hits.
    from ovp_pipeline.identity import canonicalize_note_id
    assert canonicalize_note_id("Some Mixed Case ID") in ids


def test_object_ids_excludes_source_and_file():
    """HARD BOUNDARY: source/file must NOT enter the object index."""
    payload = {
        "source": "/Users/x/50-Inbox/03-Processed/2026-03/foo_原文.md",
        "file": "2026-04-04_www.usebruno.com.md",
    }
    assert audit_object_ids(payload) == set()


def test_object_ids_excludes_top_level_source_slug():
    """codex PR #247 P2-1: M24.2-era source producers
    (``absorb_pending_upsert`` / ``candidates_upserted`` /
    ``community_crystal_synthesized``) carry a TOP-LEVEL ``slug``
    that is the SOURCE slug.  It must NOT be swept into the object
    index — otherwise an object whose object_id equals that source
    slug would consume source-only absorb evidence."""
    payload = {
        "slug": "2026-04-04-some-source",
        "candidates": 3,
        "source": "2026-04-04_some_source.md",
    }
    assert audit_object_ids(payload) == set()
    # But object-context mutation.slug IS still recovered.
    obj_payload = {"mutation": {"slug": "real-object", "action": "promote"}}
    assert "real-object" in audit_object_ids(obj_payload)


# ── audit_slug_for_column ──────────────────────────────────────────


def test_slug_column_prefers_explicit_slug():
    assert audit_slug_for_column({"slug": "MyNote"}) == "mynote"


def test_slug_column_from_file_basename_real_shape():
    """``article_processed`` / ``candidates_upserted`` /
    ``article_intake_only`` carry only ``file`` — before PR-B this
    produced an empty slug column so source lifecycle never saw
    the event."""
    s = audit_slug_for_column({"file": "2026-04-04_www.usebruno.com.md"})
    assert s
    # Same source filename → same slug (dedupe across the source's
    # event stream).
    assert s == audit_slug_for_column(
        {"file": "2026-04-04_www.usebruno.com.md"}
    )


def test_slug_column_from_source_full_path():
    """``source_archived_to_processed`` carries a full path."""
    s = audit_slug_for_column({
        "source": "/Users/x/50-Inbox/02-Processing/"
        "2026-03-29_Dynamic_context_discovery_原文.md",
    })
    assert s
    assert "/" not in s  # basename only, slugified


def test_slug_column_target_path_returned_raw():
    """Lint zone-boundary contract: ``target_path`` must NOT be
    canonicalized — the rule matches by the exact key."""
    raw = "30-Projects/Alpha/Plan.md"
    assert audit_slug_for_column({"target_path": raw}) == raw


def test_slug_column_does_not_consult_object_keys():
    """HARD BOUNDARY: ``concept`` / ``mutation.*`` are object
    identities — they must NOT become the source slug column."""
    payload = {
        "concept": "some-object-slug",
        "mutation": {"slug": "some-object-slug"},
    }
    assert audit_slug_for_column(payload) == ""


def test_slug_column_empty_when_no_identity():
    assert audit_slug_for_column({"unrelated": "x"}) == ""


# ── audit_cluster_ids ──────────────────────────────────────────────


def test_cluster_ids_nested_aware():
    assert audit_cluster_ids(
        {"payload": {"cluster_id": "cluster::abc"}}
    ) == {"cluster::abc"}


def test_cluster_ids_empty_when_absent():
    assert audit_cluster_ids({"slug": "x"}) == set()
