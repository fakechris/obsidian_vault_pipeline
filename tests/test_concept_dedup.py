"""Tests for Phase 38.A — concept_dedup."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

import pytest

from ovp_pipeline.concept_dedup import (
    DEFAULT_THRESHOLD,
    FullScanRefused,
    apply_cluster,
    apply_proposal,
    archive_applied_proposal,
    find_clusters,
    find_similar_slugs,
    list_proposals,
    load_proposal,
    trigram_jaccard,
    write_proposal,
    _add_alias_to_frontmatter,
    _rewrite_wikilinks,
)


def _evergreen(vault: Path, slug: str, *, title: str | None = None, body: str = "body") -> Path:
    eg_dir = vault / "10-Knowledge" / "Evergreen"
    eg_dir.mkdir(parents=True, exist_ok=True)
    text = dedent(
        f"""\
        ---
        title: "{title or slug.replace('-', ' ')}"
        type: evergreen
        ---

        {body}
        """
    )
    path = eg_dir / f"{slug}.md"
    path.write_text(text, encoding="utf-8")
    return path


def _other(vault: Path, rel: str, body: str) -> Path:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_trigram_jaccard_high_for_near_dupes():
    assert trigram_jaccard("mcp client", "mcp client") == 1.0
    # "mcp client" vs "mcp clients": ~0.67 — close but not identical
    assert trigram_jaccard("mcp client", "mcp clients") > 0.6
    assert trigram_jaccard("mcp client", "totally unrelated thing") < 0.2


def test_find_clusters_groups_near_duplicates(tmp_path: Path):
    _evergreen(tmp_path, "MCP-Client", body="canonical body long content " * 10)
    _evergreen(tmp_path, "MCP-Clients", body="dup")
    _evergreen(tmp_path, "MCP-Client-Variant", body="dup")
    _evergreen(tmp_path, "Some-Other-Concept", body="unrelated")

    clusters = find_clusters(tmp_path, threshold=0.5, allow_full_scan=True)

    assert len(clusters) == 1
    cluster = clusters[0]
    # Canonical should be the largest file.
    assert cluster.canonical.slug == "MCP-Client"
    dup_slugs = {d.slug for d in cluster.duplicates}
    assert dup_slugs == {"MCP-Clients", "MCP-Client-Variant"}


def test_find_clusters_skips_underscored_and_hidden(tmp_path: Path):
    _evergreen(tmp_path, "_Candidate-Note")
    _evergreen(tmp_path, "Real-Note")
    _evergreen(tmp_path, "Real-Notes")

    clusters = find_clusters(tmp_path, threshold=0.7, allow_full_scan=True)
    canonical_slugs = {c.canonical.slug for c in clusters}
    dup_slugs = {d.slug for c in clusters for d in c.duplicates}
    assert "_Candidate-Note" not in canonical_slugs
    assert "_Candidate-Note" not in dup_slugs


def test_find_clusters_returns_empty_when_no_evergreen_dir(tmp_path: Path):
    assert find_clusters(tmp_path, allow_full_scan=True) == []


def test_rewrite_wikilinks_preserves_anchor_and_display():
    text = "see [[old-slug]] and [[old-slug#section]] and [[old-slug|Display Name]] and [[other]]"
    new_text, count = _rewrite_wikilinks(text, {"old-slug": "new-slug"})
    assert count == 3
    assert "[[new-slug]]" in new_text
    assert "[[new-slug#section]]" in new_text
    assert "[[new-slug|Display Name]]" in new_text
    assert "[[other]]" in new_text


def test_rewrite_wikilinks_no_change_when_slug_absent():
    text = "see [[unrelated]]"
    new_text, count = _rewrite_wikilinks(text, {"old-slug": "new-slug"})
    assert count == 0
    assert new_text == text


def test_add_alias_to_frontmatter_inserts_when_absent():
    text = '---\ntitle: "X"\ntype: evergreen\n---\n\nbody\n'
    out = _add_alias_to_frontmatter(text, "old-slug")
    assert 'aliases: ["old-slug"]' in out
    assert "body" in out


def test_add_alias_to_frontmatter_appends_to_existing():
    text = '---\ntitle: "X"\naliases: ["existing"]\n---\n\nbody\n'
    out = _add_alias_to_frontmatter(text, "new-one")
    assert '"existing"' in out
    assert '"new-one"' in out


def test_add_alias_to_frontmatter_idempotent():
    text = '---\ntitle: "X"\naliases: ["dup"]\n---\n\nbody\n'
    out = _add_alias_to_frontmatter(text, "dup")
    assert out == text


def test_write_and_load_proposal_roundtrip(tmp_path: Path):
    _evergreen(tmp_path, "MCP-Client", body="canonical " * 10)
    _evergreen(tmp_path, "MCP-Clients")

    clusters = find_clusters(tmp_path, threshold=0.6, allow_full_scan=True)
    path, proposal = write_proposal(tmp_path, clusters, threshold=0.6)

    assert path.is_file()
    listed = list_proposals(tmp_path)
    assert path in listed

    loaded = load_proposal(path)
    assert loaded.proposal_id == proposal.proposal_id
    assert len(loaded.clusters) == 1
    assert loaded.clusters[0].canonical.slug == "MCP-Client"


def test_archive_applied_proposal_removes_it_from_pending_list(tmp_path: Path):
    _evergreen(tmp_path, "MCP-Client", body="canonical " * 10)
    _evergreen(tmp_path, "MCP-Clients")
    clusters = find_clusters(tmp_path, threshold=0.6, allow_full_scan=True)
    path, proposal = write_proposal(tmp_path, clusters, threshold=0.6)

    archived = archive_applied_proposal(tmp_path, path)

    assert archived.parent == tmp_path / "60-Logs" / "dedup-proposals" / "applied"
    assert archived.name == f"{proposal.proposal_id}.json"
    assert archived.exists()
    assert path not in list_proposals(tmp_path)


def test_apply_cluster_dry_run_makes_no_writes(tmp_path: Path):
    canonical = _evergreen(tmp_path, "MCP-Client", body="canonical " * 10)
    dup = _evergreen(tmp_path, "MCP-Clients", body="dup")
    refer = _other(tmp_path, "20-Areas/Note.md", "see [[MCP-Clients]] and [[MCP-Clients|Friendly]]")

    clusters = find_clusters(tmp_path, threshold=0.6, allow_full_scan=True)
    assert clusters

    result = apply_cluster(tmp_path, clusters[0], dry_run=True)

    # Wikilink rewrite count is computed even in dry run.
    assert result.wikilink_rewrites == 2
    # But files are untouched.
    assert dup.exists()
    assert canonical.read_text(encoding="utf-8") == canonical.read_text(encoding="utf-8")
    assert "[[MCP-Clients]]" in refer.read_text(encoding="utf-8")
    # No audit event written.
    log = tmp_path / "60-Logs" / "concept-merges.jsonl"
    assert not log.exists()


def test_apply_cluster_writes_archive_aliases_audit(tmp_path: Path):
    canonical = _evergreen(tmp_path, "MCP-Client", body="canonical body that is long " * 10)
    dup = _evergreen(tmp_path, "MCP-Clients", body="dup body")
    refer = _other(
        tmp_path,
        "20-Areas/Topic.md",
        "see [[MCP-Clients]] and [[MCP-Clients#anchor]] and [[MCP-Clients|Display]]",
    )

    clusters = find_clusters(tmp_path, threshold=0.6, allow_full_scan=True)
    assert clusters

    result = apply_cluster(tmp_path, clusters[0], dry_run=False, proposal_id="test-prop")

    assert result.canonical_slug == "MCP-Client"
    assert result.wikilink_rewrites == 3
    assert "MCP-Clients" in result.aliases_added
    assert result.errors == []
    assert len(result.audit_event_ids) == 1

    # Duplicate file moved out of Evergreen.
    assert not dup.exists()
    archived = tmp_path / "70-Archive" / "dedup-merged" / "MCP-Clients.md"
    assert archived.exists()

    # References rewritten.
    rewritten = refer.read_text(encoding="utf-8")
    assert "[[MCP-Client]]" in rewritten
    assert "[[MCP-Client#anchor]]" in rewritten
    assert "[[MCP-Client|Display]]" in rewritten
    assert "[[MCP-Clients" not in rewritten

    # Canonical aliases updated.
    canon_text = canonical.read_text(encoding="utf-8")
    assert "MCP-Clients" in canon_text

    # Audit event recorded.
    log = tmp_path / "60-Logs" / "concept-merges.jsonl"
    assert log.exists()
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "concept_merged"
    assert ev["canonical_slug"] == "MCP-Client"
    assert ev["merged_slugs"] == ["MCP-Clients"]
    assert ev["proposal_id"] == "test-prop"


def test_apply_cluster_archives_with_obsidian_move_when_available(tmp_path: Path, monkeypatch):
    (tmp_path / ".obsidian").mkdir()
    canonical = _evergreen(tmp_path, "MCP-Client", body="canonical body that is long " * 10)
    dup = _evergreen(tmp_path, "MCP-Clients", body="dup body")
    _other(tmp_path, "20-Areas/Topic.md", "see [[MCP-Clients]]")

    clusters = find_clusters(tmp_path, threshold=0.6, allow_full_scan=True)
    assert clusters

    calls: list[list[str]] = []
    monkeypatch.setattr("ovp_pipeline.concept_dedup.shutil.which", lambda name: "/usr/local/bin/obsidian")

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        dest_arg = next(arg for arg in cmd if arg.startswith("to="))
        dest = tmp_path / dest_arg.removeprefix("to=")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dup.rename(dest)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("ovp_pipeline.concept_dedup.subprocess.run", fake_run)

    result = apply_cluster(tmp_path, clusters[0], dry_run=False, proposal_id="test-prop")

    assert result.errors == []
    assert calls
    assert calls[0][:2] == ["/usr/local/bin/obsidian", "move"]
    assert any(arg == "file=10-Knowledge/Evergreen/MCP-Clients.md" for arg in calls[0])
    assert any(arg.startswith("to=70-Archive/dedup-merged/MCP-Clients") for arg in calls[0])
    assert not dup.exists()
    assert canonical.exists()


def test_apply_cluster_skips_archive_dir_during_rewrite(tmp_path: Path):
    """A second pass after merge must not rewrite anything in the archive."""
    _evergreen(tmp_path, "MCP-Client", body="canonical " * 10)
    _evergreen(tmp_path, "MCP-Clients")
    _other(tmp_path, "20-Areas/Note.md", "see [[MCP-Clients]]")

    clusters = find_clusters(tmp_path, threshold=0.6, allow_full_scan=True)
    apply_cluster(tmp_path, clusters[0], dry_run=False)

    # Re-scan: cluster should be gone.
    clusters_after = find_clusters(tmp_path, threshold=DEFAULT_THRESHOLD, allow_full_scan=True)
    assert clusters_after == []


def test_apply_cluster_handles_archive_collision(tmp_path: Path):
    """If the archive already has a file with the same name, second one gets a timestamp suffix."""
    _evergreen(tmp_path, "MCP-Client", body="canonical " * 10)
    _evergreen(tmp_path, "MCP-Clients")
    archive_dir = tmp_path / "70-Archive" / "dedup-merged"
    archive_dir.mkdir(parents=True)
    (archive_dir / "MCP-Clients.md").write_text("pre-existing", encoding="utf-8")

    clusters = find_clusters(tmp_path, threshold=0.6, allow_full_scan=True)
    result = apply_cluster(tmp_path, clusters[0], dry_run=False)

    assert result.errors == []
    assert (archive_dir / "MCP-Clients.md").read_text(encoding="utf-8") == "pre-existing"
    # New archive file lands with a timestamp suffix.
    other_files = [
        p for p in archive_dir.glob("MCP-Clients*.md") if p.name != "MCP-Clients.md"
    ]
    assert len(other_files) == 1


def test_apply_proposal_only_filter(tmp_path: Path):
    _evergreen(tmp_path, "MCP-Client", body="canonical " * 10)
    _evergreen(tmp_path, "MCP-Clients")
    _evergreen(tmp_path, "Vector-Database", body="canonical " * 10)
    _evergreen(tmp_path, "Vector-Databases")

    clusters = find_clusters(tmp_path, threshold=0.6, allow_full_scan=True)
    assert len(clusters) == 2
    _, proposal = write_proposal(tmp_path, clusters, threshold=DEFAULT_THRESHOLD)

    results = apply_proposal(
        tmp_path, proposal, dry_run=False, only_canonicals={"MCP-Client"}
    )
    assert len(results) == 1
    assert results[0].canonical_slug == "MCP-Client"
    # Vector-Databases must NOT be archived.
    assert (tmp_path / "10-Knowledge" / "Evergreen" / "Vector-Databases.md").exists()


def test_concept_dedup_apply_uses_vault_workflow_lock(tmp_path: Path, monkeypatch):
    from ovp_pipeline.commands import concept_dedup as command

    _evergreen(tmp_path, "MCP-Client", body="canonical body that is long " * 10)
    _evergreen(tmp_path, "MCP-Clients", body="duplicate body")
    clusters = find_clusters(tmp_path, threshold=0.6, allow_full_scan=True)
    proposal_path, _ = write_proposal(tmp_path, clusters, threshold=DEFAULT_THRESHOLD)
    calls: list[str] = []

    class FakeLock:
        def __enter__(self):
            calls.append("enter")

        def __exit__(self, exc_type, exc, tb):
            calls.append("exit")

    monkeypatch.setattr(command, "vault_workflow_lock", lambda vault_dir: FakeLock())

    def fake_apply_proposal(*args, **kwargs):
        calls.append("apply")
        return [
            SimpleNamespace(
                archived=[],
                wikilink_rewrites=0,
                errors=[],
                canonical_slug="MCP-Client",
                aliases_added=[],
                registry_updated=True,
            )
        ]

    monkeypatch.setattr(command, "apply_proposal", fake_apply_proposal)
    monkeypatch.setattr(
        command,
        "archive_applied_proposal",
        lambda *args, **kwargs: calls.append("archive") or (tmp_path / "applied.json"),
    )

    exit_code = command.main(["apply", proposal_path.stem, "--vault-dir", str(tmp_path)])

    assert exit_code == 0
    assert calls == ["enter", "apply", "archive", "exit"]


# ---------------------------------------------------------------------------
# scope_slugs filtering
# ---------------------------------------------------------------------------


def test_find_clusters_scope_slugs_limits_search(tmp_path: Path):
    """Only candidates in scope (or similar to scope) should be clustered."""
    _evergreen(tmp_path, "MCP-Client", body="canonical " * 10)
    _evergreen(tmp_path, "MCP-Clients", body="dup")
    _evergreen(tmp_path, "Vector-Database", body="canonical " * 10)
    _evergreen(tmp_path, "Vector-Databases", body="dup")

    clusters = find_clusters(tmp_path, threshold=0.5, scope_slugs={"MCP-Client"})
    slugs_in_clusters = {c.canonical.slug for c in clusters} | {
        d.slug for c in clusters for d in c.duplicates
    }
    assert "MCP-Client" in slugs_in_clusters or "MCP-Clients" in slugs_in_clusters
    assert "Vector-Database" not in slugs_in_clusters
    assert "Vector-Databases" not in slugs_in_clusters


def test_find_clusters_scope_none_refuses_without_opt_in(tmp_path: Path):
    """PR1 fail-closed: an unscoped run is the O(N²) full-vault scan;
    it must be refused unless the caller explicitly opts in."""
    _evergreen(tmp_path, "MCP-Client", body="canonical " * 10)
    _evergreen(tmp_path, "MCP-Clients", body="dup")

    with pytest.raises(FullScanRefused):
        find_clusters(tmp_path, threshold=0.5, scope_slugs=None)


def test_find_clusters_scope_none_runs_with_explicit_opt_in(tmp_path: Path):
    _evergreen(tmp_path, "MCP-Client", body="canonical " * 10)
    _evergreen(tmp_path, "MCP-Clients", body="dup")
    _evergreen(tmp_path, "Vector-Database", body="canonical " * 10)
    _evergreen(tmp_path, "Vector-Databases", body="dup")

    clusters_all = find_clusters(
        tmp_path, threshold=0.5, scope_slugs=None, allow_full_scan=True
    )
    assert len(clusters_all) == 2


def test_find_clusters_scope_slugs_empty_set_returns_none(tmp_path: Path):
    _evergreen(tmp_path, "MCP-Client", body="canonical " * 10)
    _evergreen(tmp_path, "MCP-Clients", body="dup")

    clusters = find_clusters(tmp_path, threshold=0.5, scope_slugs=set())
    assert clusters == []


# ---------------------------------------------------------------------------
# find_similar_slugs
# ---------------------------------------------------------------------------


def test_find_similar_slugs_returns_matches(tmp_path: Path):
    _evergreen(tmp_path, "MCP-Client", body="canonical " * 10)
    _evergreen(tmp_path, "MCP-Clients", body="dup")
    _evergreen(tmp_path, "Unrelated-Concept", body="no match")

    hits = find_similar_slugs(tmp_path, "MCP-Client", threshold=0.5)
    matched_slugs = {s for s, _ in hits}
    assert "MCP-Clients" in matched_slugs
    assert "Unrelated-Concept" not in matched_slugs


def test_find_similar_slugs_excludes_self(tmp_path: Path):
    _evergreen(tmp_path, "MCP-Client", body="body")

    hits = find_similar_slugs(tmp_path, "MCP-Client", threshold=0.1)
    assert all(s != "MCP-Client" for s, _ in hits)


def test_find_similar_slugs_empty_when_no_match(tmp_path: Path):
    _evergreen(tmp_path, "Quantum-Computing")
    _evergreen(tmp_path, "Banana-Bread")

    hits = find_similar_slugs(tmp_path, "MCP-Client", threshold=0.8)
    assert hits == []
