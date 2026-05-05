"""Tests for the /topics HTTP surface (BL-046 / BL-051).

BL-051 renamed ``/atlas/curated`` → ``/topics`` (and the page title to
"Featured Topics").  Old URLs still work via 301 redirect; this test
file exercises the canonical path + asserts the new page strings.

Covers:

1. ``build_curated_atlas_payload`` shape: pack/top_n/total_chains/entries.
2. Empty case: no crystal_scores rows → ``count == 0`` + ``total_chains == 0``.
3. Renderer html: contains the entry label, score, kind pill, JSON link,
   and the "no crystals scored yet" hint when empty.
4. ``top_n`` clamping: out-of-range values get clamped to bounds.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ovp_pipeline.commands._ui_renderers import _render_curated_atlas_page
from ovp_pipeline.ui.view_models import (
    CURATED_ATLAS_DEFAULT_TOP_N,
    CURATED_ATLAS_MAX_TOP_N,
    build_curated_atlas_payload,
)


SCHEMA = """
CREATE TABLE objects (
  pack TEXT NOT NULL, object_id TEXT NOT NULL, object_kind TEXT NOT NULL,
  title TEXT NOT NULL, canonical_path TEXT NOT NULL, source_slug TEXT NOT NULL,
  PRIMARY KEY (pack, object_id)
);
CREATE TABLE graph_clusters (
  pack TEXT NOT NULL, cluster_id TEXT NOT NULL, cluster_kind TEXT NOT NULL,
  label TEXT NOT NULL, center_object_id TEXT NOT NULL,
  member_object_ids_json TEXT NOT NULL, score REAL NOT NULL DEFAULT 0.0,
  PRIMARY KEY (pack, cluster_id)
);
CREATE TABLE community_crystals (
  pack TEXT NOT NULL, cluster_id TEXT NOT NULL, body_md TEXT NOT NULL,
  source_evergreen_slugs_json TEXT NOT NULL, synthesized_at TEXT NOT NULL,
  llm_model TEXT NOT NULL, prompt_version TEXT NOT NULL,
  superseded_by_synthesized_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, cluster_id, synthesized_at)
);
CREATE TABLE contradiction_crystals (
  pack TEXT NOT NULL, contradiction_id TEXT NOT NULL,
  subject_key TEXT NOT NULL, body_md TEXT NOT NULL,
  positive_claim_ids_json TEXT NOT NULL, negative_claim_ids_json TEXT NOT NULL,
  source_object_ids_json TEXT NOT NULL, synthesized_at TEXT NOT NULL,
  llm_model TEXT NOT NULL, prompt_version TEXT NOT NULL,
  superseded_by_synthesized_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, contradiction_id, synthesized_at)
);
CREATE TABLE crystal_scores (
  pack TEXT NOT NULL, crystal_kind TEXT NOT NULL, crystal_id TEXT NOT NULL,
  score REAL NOT NULL, size_norm REAL NOT NULL DEFAULT 0,
  credibility_norm REAL NOT NULL DEFAULT 0,
  contradiction_norm REAL NOT NULL DEFAULT 0,
  reuse_recency_norm REAL NOT NULL DEFAULT 0,
  evergreen_recency_norm REAL NOT NULL DEFAULT 0,
  computed_at TEXT NOT NULL,
  PRIMARY KEY (pack, crystal_kind, crystal_id)
);
"""


PACK = "t"
RESEARCH_PACK = "research-tech"


def _setup_vault(tmp_path: Path, *, seed: bool = True, pack: str = PACK) -> Path:
    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    db_path = vault / "60-Logs" / "knowledge.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        if seed:
            conn.execute(
                "INSERT INTO graph_clusters VALUES (?,?,?,?,?,?,?)",
                (pack, "cluster::abc123", "louvain_community", "Vector search",
                 "obj-1", json.dumps(["obj-1", "obj-2"]), 0.0),
            )
            conn.execute(
                "INSERT INTO community_crystals VALUES (?,?,?,?,?,?,?,?)",
                (pack, "cluster::abc123",
                 "## 概念核心\n\nVector search lets agents recall similar memories at low cost.",
                 json.dumps(["evergreen-a", "evergreen-b"]),
                 "2026-05-04T12:00:00+00:00",
                 "minimax-m2.7-highspeed", "v1", ""),
            )
            conn.execute(
                "INSERT INTO crystal_scores VALUES (?,?,?,?,?,?,?,?,?,?)",
                (pack, "community", "cluster::abc123", 0.812,
                 0.6, 0.7, 0.0, 0.0, 0.5,
                 "2026-05-04T12:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO contradiction_crystals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (pack, "contradiction::deadbeef", "RAG vs long context",
                 "## Open question\n\nDoes long context replace RAG, or do they complement?",
                 json.dumps(["claim-pos"]), json.dumps(["claim-neg"]),
                 json.dumps(["obj-3"]),
                 "2026-05-04T12:01:00+00:00", "minimax-m2.7-highspeed", "v1", ""),
            )
            conn.execute(
                "INSERT INTO crystal_scores VALUES (?,?,?,?,?,?,?,?,?,?)",
                (pack, "contradiction", "contradiction::deadbeef", 0.654,
                 0.0, 0.5, 0.9, 0.0, 0.4,
                 "2026-05-04T12:01:00+00:00"),
            )
            conn.commit()
    finally:
        conn.close()
    return vault


class TestPayload:
    def test_returns_top_n_in_score_order(self, tmp_path):
        vault = _setup_vault(tmp_path)
        payload = build_curated_atlas_payload(vault, pack_name=PACK)
        assert payload["screen"] == "atlas/curated"
        assert payload["pack"] == PACK
        assert payload["total_chains"] == 2
        assert payload["count"] == 2
        ranks = [e["rank"] for e in payload["entries"]]
        assert ranks == [1, 2]
        # higher score (community 0.812) ranks above contradiction (0.654)
        assert payload["entries"][0]["crystal_kind"] == "community"
        assert payload["entries"][1]["crystal_kind"] == "contradiction"

    def test_note_path_uses_safe_id(self, tmp_path):
        vault = _setup_vault(tmp_path)
        payload = build_curated_atlas_payload(vault, pack_name=PACK)
        community = next(e for e in payload["entries"] if e["crystal_kind"] == "community")
        contradiction = next(e for e in payload["entries"] if e["crystal_kind"] == "contradiction")
        assert community["note_path"] == "40-Resources/Crystals/abc123.md"
        assert contradiction["note_path"] == "40-Resources/Crystals/contradiction-deadbeef.md"
        assert "/note?path=" in community["note_href"]

    def test_top_n_clamped_to_max(self, tmp_path):
        vault = _setup_vault(tmp_path, seed=False)
        payload = build_curated_atlas_payload(vault, pack_name=PACK, top_n=10_000)
        assert payload["top_n"] == CURATED_ATLAS_MAX_TOP_N

    def test_top_n_clamped_to_min(self, tmp_path):
        vault = _setup_vault(tmp_path, seed=False)
        payload = build_curated_atlas_payload(vault, pack_name=PACK, top_n=0)
        assert payload["top_n"] == 1

    def test_default_top_n_when_unspecified(self, tmp_path):
        vault = _setup_vault(tmp_path, seed=False)
        payload = build_curated_atlas_payload(vault, pack_name=PACK)
        assert payload["top_n"] == CURATED_ATLAS_DEFAULT_TOP_N

    def test_empty_case(self, tmp_path):
        vault = _setup_vault(tmp_path, seed=False)
        payload = build_curated_atlas_payload(vault, pack_name=PACK)
        assert payload["count"] == 0
        assert payload["total_chains"] == 0


class TestRenderer:
    def test_seeded_html_has_entries_and_links(self, tmp_path):
        vault = _setup_vault(tmp_path)
        payload = build_curated_atlas_payload(vault, pack_name=PACK)
        html = _render_curated_atlas_page(payload)
        # BL-051 user-facing rename: page title is now "Featured Topics".
        assert "Featured Topics" in html
        assert "Vector search" in html
        assert "score 0.812" in html
        assert "RAG vs long context" in html
        # JSON link points at the canonical /api/topics route.
        assert "/api/topics" in html
        # Score breakdown surfaced as muted line
        assert "size 0.60" in html
        assert "credibility 0.70" in html

    def test_empty_html_shows_hint(self, tmp_path):
        vault = _setup_vault(tmp_path, seed=False)
        payload = build_curated_atlas_payload(vault, pack_name=PACK)
        html = _render_curated_atlas_page(payload)
        # Empty-state hint uses the new vocabulary.
        assert "No topics synthesized yet" in html
        assert "ovp-synthesize-community-crystals" in html


class TestHttpRoute:
    """End-to-end through the actual HTTPServer to catch wiring bugs
    (import path, route table, JSON/HTML content type)."""

    def _serve(self, vault: Path):
        import threading
        from http.client import HTTPConnection

        from ovp_pipeline.commands.ui_server import create_server

        server = create_server(vault, host="127.0.0.1", port=0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, port, HTTPConnection

    def _shutdown(self, server, thread):
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    def test_api_topics_returns_json(self, tmp_path):
        vault = _setup_vault(tmp_path, pack=RESEARCH_PACK)
        server, thread, port, HTTPConnection = self._serve(vault)
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", f"/api/topics?pack={RESEARCH_PACK}")
            response = conn.getresponse()
            body = response.read().decode("utf-8")
            content_type = response.getheader("Content-Type") or ""
        finally:
            self._shutdown(server, thread)
        assert response.status == 200
        assert "application/json" in content_type
        payload = json.loads(body)
        # Internal screen identifier kept ``atlas/curated`` for now —
        # changing it would churn JS callers without user benefit.
        assert payload["screen"] == "atlas/curated"
        assert payload["count"] == 2

    def test_topics_returns_html(self, tmp_path):
        vault = _setup_vault(tmp_path, pack=RESEARCH_PACK)
        server, thread, port, HTTPConnection = self._serve(vault)
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", f"/topics?pack={RESEARCH_PACK}")
            response = conn.getresponse()
            body = response.read().decode("utf-8")
            content_type = response.getheader("Content-Type") or ""
        finally:
            self._shutdown(server, thread)
        assert response.status == 200
        assert "text/html" in content_type
        assert "Featured Topics" in body
        assert "Vector search" in body

    def test_legacy_atlas_curated_redirects_to_topics(self, tmp_path):
        """BL-051: ``/atlas/curated`` and ``/api/atlas/curated`` 301 to
        the canonical paths so PR #148 bookmarks keep working."""
        vault = _setup_vault(tmp_path, pack=RESEARCH_PACK)
        server, thread, port, HTTPConnection = self._serve(vault)
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            # Tell httplib not to auto-follow.
            conn.request("GET", f"/atlas/curated?pack={RESEARCH_PACK}")
            response = conn.getresponse()
            location_html = response.getheader("Location") or ""
            response.read()
            status_html = response.status

            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", f"/api/atlas/curated?pack={RESEARCH_PACK}")
            response = conn.getresponse()
            location_api = response.getheader("Location") or ""
            response.read()
            status_api = response.status
        finally:
            self._shutdown(server, thread)
        assert status_html == 301
        assert location_html == f"/topics?pack={RESEARCH_PACK}"
        assert status_api == 301
        assert location_api == f"/api/topics?pack={RESEARCH_PACK}"

    def test_unsupported_pack_returns_409(self, tmp_path):
        vault = _setup_vault(tmp_path)
        server, thread, port, HTTPConnection = self._serve(vault)
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/topics?pack=unknown-pack")
            response = conn.getresponse()
            response.read()
        finally:
            self._shutdown(server, thread)
        assert response.status == 409
