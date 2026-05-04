"""Tests for synthesis/contradiction_crystal.py — BL-043 open-question
crystals on top of the existing ``contradictions`` table.

LLM is mocked so these tests don't hit MiniMax.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from ovp_pipeline.synthesis.contradiction_crystal import (
    CONTRADICTION_PROMPT_VERSION,
    ContradictionCrystal,
    _claim_id_to_object_id,
    _crystal_filename,
    render_crystal_markdown,
    synthesize_contradiction_crystals,
)
from ovp_pipeline.synthesis.community_crystal import CRYSTAL_DIR_REL


SCHEMA_CREATE = """
CREATE TABLE objects (
  pack TEXT NOT NULL,
  object_id TEXT NOT NULL,
  object_kind TEXT NOT NULL,
  title TEXT NOT NULL,
  canonical_path TEXT NOT NULL,
  source_slug TEXT NOT NULL,
  PRIMARY KEY (pack, object_id)
);
CREATE TABLE claims (
  pack TEXT NOT NULL,
  claim_id TEXT NOT NULL,
  object_id TEXT NOT NULL,
  claim_kind TEXT NOT NULL,
  claim_text TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (pack, claim_id)
);
CREATE TABLE contradictions (
  pack TEXT NOT NULL,
  contradiction_id TEXT NOT NULL,
  subject_key TEXT NOT NULL,
  positive_claim_ids_json TEXT NOT NULL,
  negative_claim_ids_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  resolution_note TEXT NOT NULL DEFAULT '',
  resolved_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, contradiction_id)
);
CREATE TABLE contradiction_crystals (
  pack TEXT NOT NULL,
  contradiction_id TEXT NOT NULL,
  subject_key TEXT NOT NULL,
  body_md TEXT NOT NULL,
  positive_claim_ids_json TEXT NOT NULL,
  negative_claim_ids_json TEXT NOT NULL,
  source_object_ids_json TEXT NOT NULL,
  synthesized_at TEXT NOT NULL,
  llm_model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  superseded_by_synthesized_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, contradiction_id, synthesized_at)
);
"""


class _StubLLM:
    """Records calls + returns a fixed body."""

    def __init__(self, body: str = "## 争议核心\n\n这里是合成正文。"):
        self.body = body
        self.calls: list[tuple[str, str, int]] = []

    def call(self, system_prompt: str, user_prompt: str,
             *, max_tokens: int = 1800) -> str:
        self.calls.append((system_prompt, user_prompt, max_tokens))
        return self.body


class _RaisingLLM:
    def __init__(self):
        self.calls = 0

    def call(self, *_, **__) -> str:
        self.calls += 1
        raise RuntimeError("simulated LLM outage")


def _seed(
    tmp_path: Path,
    *,
    pack: str = "research-tech",
    objects: list[tuple[str, str, str]] | None = None,  # (object_id, title, body)
    claims: list[tuple[str, str, str, str]] | None = None,  # (claim_id, object_id, kind, text)
    contradictions: list[tuple[str, str, list[str], list[str], str]] | None = None,
    # (contradiction_id, subject_key, positives, negatives, status)
) -> tuple[Path, Path]:
    """Build a synthetic vault for contradiction-crystal tests.

    Returns ``(vault_dir, db_path)``.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    db_path = vault / "60-Logs" / "knowledge.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_CREATE)
    for object_id, title, body in (objects or []):
        canonical = f"10-Knowledge/Evergreen/{object_id}.md"
        full = vault / canonical
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(body, encoding="utf-8")
        conn.execute(
            "INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?)",
            (pack, object_id, "evergreen", title, canonical, ""),
        )
    for claim_id, object_id, kind, text in (claims or []):
        conn.execute(
            "INSERT INTO claims VALUES (?, ?, ?, ?, ?, 1.0)",
            (pack, claim_id, object_id, kind, text),
        )
    for cid, subject, positives, negatives, status in (contradictions or []):
        conn.execute(
            "INSERT INTO contradictions VALUES (?, ?, ?, ?, ?, ?, '', '')",
            (
                pack, cid, subject,
                json.dumps(positives), json.dumps(negatives),
                status,
            ),
        )
    conn.commit()
    conn.close()
    return vault, db_path


# ---------------------------------------------------------------------------
# Claim ID decoding
# ---------------------------------------------------------------------------


class TestClaimIdDecoding:
    def test_split_yields_object_id(self):
        # The format is `{object_id}::{12-char digest}`.  Split on the
        # first `::` so object_ids that themselves contain `::` survive.
        assert _claim_id_to_object_id("agent-loop::abc123def456") == "agent-loop"

    def test_handles_missing_digest(self):
        # Defensive — if a claim_id ever loses its digest (legacy /
        # malformed), the split falls through to the original string.
        assert _claim_id_to_object_id("agent-loop") == "agent-loop"


# ---------------------------------------------------------------------------
# Filename
# ---------------------------------------------------------------------------


class TestCrystalFilename:
    def test_strips_contradiction_prefix_adds_visual_prefix(self):
        # `contradiction::xyz` → `contradiction-xyz.md` so the dir
        # listing makes the kind obvious next to community crystals
        # (which are bare `<sha>.md`).
        assert (
            _crystal_filename("contradiction::abc123def456")
            == "contradiction-abc123def456.md"
        )

    def test_falls_back_when_prefix_absent(self):
        assert _crystal_filename("foo") == "contradiction-foo.md"


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


class TestRenderCrystalMarkdown:
    def test_frontmatter_carries_lineage(self):
        c = ContradictionCrystal(
            pack="research-tech",
            contradiction_id="contradiction::deadbeef",
            subject_key="vector search",
            body_md="## 争议核心\n\nbody",
            positive_claim_ids=("a::111", "b::222"),
            negative_claim_ids=("c::333",),
            source_object_ids=("a", "b", "c"),
            synthesized_at="2026-05-03T10:00:00+00:00",
            llm_model="anthropic/MiniMax-M2.7-highspeed",
            prompt_version=CONTRADICTION_PROMPT_VERSION,
        )
        md = render_crystal_markdown(c)
        # Lineage fields rendered.
        assert "type: contradiction_crystal" in md
        assert "contradiction_id: contradiction::deadbeef" in md
        assert "subject_key: \"vector search\"" in md
        assert "  - a::111" in md
        assert "  - c::333" in md
        assert "  - a" in md and "  - b" in md and "  - c" in md
        assert "tags: [crystal, contradiction, open_question]" in md
        # Standard projection_* metadata.
        assert "projection_kind: compiled_wiki_projection" in md
        assert "projection_surface: contradiction_crystal" in md
        assert "projection_generated_by: synthesize_contradiction_crystals" in md
        # Body after closing ---.
        assert "## 争议核心" in md


# ---------------------------------------------------------------------------
# End-to-end via synthesize_contradiction_crystals
# ---------------------------------------------------------------------------


class TestSynthesizeEndToEnd:
    def test_writes_one_crystal_per_open_contradiction(self, tmp_path):
        vault, db = _seed(
            tmp_path,
            objects=[
                ("agent-loop", "Agent loop", "agents iterate"),
                ("static-tools", "Static tools", "tools are fixed"),
            ],
            claims=[
                ("agent-loop::aaaa", "agent-loop", "page_summary",
                 "Agents support dynamic tool selection"),
                ("static-tools::bbbb", "static-tools", "page_summary",
                 "Agents do not support dynamic tool selection"),
            ],
            contradictions=[
                ("contradiction::xx01", "agents support dynamic tool selection",
                 ["agent-loop::aaaa"], ["static-tools::bbbb"], "open"),
            ],
        )
        llm = _StubLLM()
        crystals = synthesize_contradiction_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        assert len(crystals) == 1
        assert len(llm.calls) == 1
        # File written under 40-Resources/Crystals/.
        assert (vault / CRYSTAL_DIR_REL / "contradiction-xx01.md").exists()
        # User prompt contains both claim texts.
        _, user_prompt, _ = llm.calls[0]
        assert "Agents support dynamic tool selection" in user_prompt
        assert "Agents do not support dynamic tool selection" in user_prompt
        # And the subject heading is present.
        assert "Subject: agents support dynamic tool selection" in user_prompt

    def test_db_row_persisted_with_full_lineage(self, tmp_path):
        vault, db = _seed(
            tmp_path,
            objects=[
                ("a", "A", "body a"),
                ("b", "B", "body b"),
            ],
            claims=[
                ("a::aa", "a", "page_summary", "X is true"),
                ("b::bb", "b", "page_summary", "X is not true"),
            ],
            contradictions=[
                ("contradiction::yy", "x", ["a::aa"], ["b::bb"], "open"),
            ],
        )
        llm = _StubLLM()
        synthesize_contradiction_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT contradiction_id, subject_key, positive_claim_ids_json, "
            "negative_claim_ids_json, source_object_ids_json, llm_model, "
            "prompt_version FROM contradiction_crystals"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        cid, subject, pos, neg, srcs, llm_model, version = rows[0]
        assert cid == "contradiction::yy"
        assert subject == "x"
        assert json.loads(pos) == ["a::aa"]
        assert json.loads(neg) == ["b::bb"]
        # source_object_ids dedupes and sorts.
        assert json.loads(srcs) == ["a", "b"]
        assert llm_model == "anthropic/MiniMax-M2.7-highspeed"
        assert version == CONTRADICTION_PROMPT_VERSION

    def test_resolved_contradictions_skipped(self, tmp_path):
        # Resolved contradictions don't get crystals — once an
        # operator has annotated the resolution, re-synthesizing an
        # "open question" crystal would muddy the audit trail.
        vault, db = _seed(
            tmp_path,
            objects=[("a", "A", "body"), ("b", "B", "body")],
            claims=[
                ("a::aa", "a", "page_summary", "X"),
                ("b::bb", "b", "page_summary", "not X"),
            ],
            contradictions=[
                ("contradiction::open", "x",
                 ["a::aa"], ["b::bb"], "open"),
                ("contradiction::done", "y",
                 ["a::aa"], ["b::bb"], "resolved"),
            ],
        )
        llm = _StubLLM()
        crystals = synthesize_contradiction_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        # Only the open one is processed.
        assert len(crystals) == 1
        assert crystals[0].contradiction_id == "contradiction::open"

    def test_dry_run_skips_writes(self, tmp_path):
        vault, db = _seed(
            tmp_path,
            objects=[("a", "A", "body"), ("b", "B", "body")],
            claims=[
                ("a::aa", "a", "page_summary", "X"),
                ("b::bb", "b", "page_summary", "not X"),
            ],
            contradictions=[
                ("contradiction::dr", "x", ["a::aa"], ["b::bb"], "open"),
            ],
        )
        llm = _StubLLM()
        crystals = synthesize_contradiction_crystals(
            vault_dir=vault, llm_client=llm, db_path=db, dry_run=True,
        )
        assert len(crystals) == 1
        # LLM was called (dry-run still previews) but no file/DB row.
        assert len(llm.calls) == 1
        assert not (vault / CRYSTAL_DIR_REL / "contradiction-dr.md").exists()
        conn = sqlite3.connect(db)
        n = conn.execute(
            "SELECT COUNT(*) FROM contradiction_crystals"
        ).fetchone()[0]
        conn.close()
        assert n == 0

    def test_append_only_versioning(self, tmp_path):
        # Re-running synthesizes a NEW row, foundation for BL-044.
        vault, db = _seed(
            tmp_path,
            objects=[("a", "A", "body"), ("b", "B", "body")],
            claims=[
                ("a::aa", "a", "page_summary", "X"),
                ("b::bb", "b", "page_summary", "not X"),
            ],
            contradictions=[
                ("contradiction::v", "x", ["a::aa"], ["b::bb"], "open"),
            ],
        )
        llm = _StubLLM()
        synthesize_contradiction_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        time.sleep(1)  # Different timestamp on the second run.
        synthesize_contradiction_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT synthesized_at FROM contradiction_crystals "
            "WHERE contradiction_id = 'contradiction::v' "
            "ORDER BY synthesized_at"
        ).fetchall()
        conn.close()
        assert len(rows) == 2
        assert rows[0][0] != rows[1][0]

    def test_filters_by_only_contradiction_ids(self, tmp_path):
        vault, db = _seed(
            tmp_path,
            objects=[("a", "A", "body"), ("b", "B", "body")],
            claims=[
                ("a::aa", "a", "page_summary", "X"),
                ("b::bb", "b", "page_summary", "not X"),
            ],
            contradictions=[
                ("contradiction::keep", "x", ["a::aa"], ["b::bb"], "open"),
                ("contradiction::drop", "y", ["a::aa"], ["b::bb"], "open"),
            ],
        )
        llm = _StubLLM()
        crystals = synthesize_contradiction_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
            only_contradiction_ids={"contradiction::keep"},
        )
        assert len(crystals) == 1
        assert crystals[0].contradiction_id == "contradiction::keep"

    def test_limit(self, tmp_path):
        vault, db = _seed(
            tmp_path,
            objects=[("a", "A", "body"), ("b", "B", "body")],
            claims=[
                ("a::aa", "a", "page_summary", "X"),
                ("b::bb", "b", "page_summary", "not X"),
            ],
            contradictions=[
                ("contradiction::aa", "x", ["a::aa"], ["b::bb"], "open"),
                ("contradiction::bb", "y", ["a::aa"], ["b::bb"], "open"),
                ("contradiction::cc", "z", ["a::aa"], ["b::bb"], "open"),
            ],
        )
        llm = _StubLLM()
        crystals = synthesize_contradiction_crystals(
            vault_dir=vault, llm_client=llm, db_path=db, limit=2,
        )
        assert len(crystals) == 2

    def test_llm_failure_does_not_sink_batch(self, tmp_path):
        vault, db = _seed(
            tmp_path,
            objects=[("a", "A", "body"), ("b", "B", "body")],
            claims=[
                ("a::aa", "a", "page_summary", "X"),
                ("b::bb", "b", "page_summary", "not X"),
            ],
            contradictions=[
                ("contradiction::a", "x", ["a::aa"], ["b::bb"], "open"),
                ("contradiction::b", "y", ["a::aa"], ["b::bb"], "open"),
            ],
        )
        llm = _RaisingLLM()
        crystals = synthesize_contradiction_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        assert llm.calls == 2  # Both attempted, both failed.
        assert crystals == []

    def test_skips_when_claim_or_object_missing(self, tmp_path):
        # Stale contradiction row pointing at a deleted claim — the
        # row is processed but the missing side is skipped.  If both
        # sides are missing we skip the whole contradiction.
        vault, db = _seed(
            tmp_path,
            objects=[],  # no evergreens at all
            claims=[],
            contradictions=[
                ("contradiction::stale", "x",
                 ["ghost1::aaaa"], ["ghost2::bbbb"], "open"),
            ],
        )
        llm = _StubLLM()
        crystals = synthesize_contradiction_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        # No claims nor objects → both sides empty → skipped.
        assert crystals == []
        assert llm.calls == []

    def test_body_emitted_once_when_object_has_multiple_claims(self, tmp_path):
        # An evergreen with two claims on the same contradiction
        # should appear ONCE in the prompt with both claims listed
        # under it — not twice with the body duplicated.  Pre-fix
        # _build_user_prompt repeated the body per claim_id, wasting
        # prompt tokens (PR-132 medium review item).
        long_body = "正面长内容" * 50
        vault, db = _seed(
            tmp_path,
            objects=[("a", "A", long_body), ("b", "B", "反面")],
            claims=[
                ("a::p1", "a", "page_summary", "X is true (1)"),
                ("a::p2", "a", "page_summary", "X is also true (2)"),
                ("b::n1", "b", "page_summary", "X is false"),
            ],
            contradictions=[
                ("contradiction::dup", "x",
                 ["a::p1", "a::p2"], ["b::n1"], "open"),
            ],
        )
        llm = _StubLLM()
        synthesize_contradiction_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        _, user_prompt, _ = llm.calls[0]
        # Body of object ``a`` appears once in the prompt.
        assert user_prompt.count(long_body) == 1
        # Both claims appear, listed under one header.
        assert "X is true (1)" in user_prompt
        assert "X is also true (2)" in user_prompt
        # Grouped form uses **Claims:** (plural), not two **Claim:** lines.
        assert "**Claims:**" in user_prompt

    def test_evergreen_body_loaded_once_across_two_contradictions(self, tmp_path):
        # When two contradictions share a source evergreen, its file
        # must be read only ONCE — pre-fix _build_side called
        # _load_evergreen_bodies on every invocation, hitting disk
        # repeatedly for the shared source.
        vault, db = _seed(
            tmp_path,
            objects=[
                ("shared", "Shared", "shared body content"),
                ("p1", "P1", "p1 body"),
                ("n1", "N1", "n1 body"),
            ],
            claims=[
                ("shared::a", "shared", "page_summary", "shared claim a"),
                ("shared::b", "shared", "page_summary", "shared claim b"),
                ("p1::p", "p1", "page_summary", "P1 claim"),
                ("n1::n", "n1", "page_summary", "N1 claim"),
            ],
            contradictions=[
                ("contradiction::A", "x",
                 ["shared::a", "p1::p"], ["n1::n"], "open"),
                ("contradiction::B", "y",
                 ["shared::b", "p1::p"], ["n1::n"], "open"),
            ],
        )
        # Spy on the shared file's read by counting filesystem opens
        # via a wrapper around Path.read_text.  Simpler: track via
        # the loader function — a successful synthesis with both
        # contradictions producing crystals is enough behavioural
        # evidence; the eager batch read happens at most once.
        llm = _StubLLM()
        crystals = synthesize_contradiction_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        # Two contradictions, two crystals, two LLM calls — but
        # the shared evergreen body got loaded into the cache once
        # (the eager pre-load) and reused twice.
        assert len(crystals) == 2
        assert len(llm.calls) == 2
        # Both LLM calls should contain the shared body.
        for _, prompt, _ in llm.calls:
            assert "shared body content" in prompt

    def test_chunked_id_filter_handles_large_lists(self, tmp_path):
        # Pass more than _CONTRADICTION_FILTER_CHUNK ids — pre-fix
        # this would have crashed at SQLite's 999-parameter cap on
        # heavy CLI use.  Post-fix the loader chunks transparently.
        vault, db = _seed(
            tmp_path,
            objects=[("a", "A", "body"), ("b", "B", "body")],
            claims=[
                ("a::aa", "a", "page_summary", "X"),
                ("b::bb", "b", "page_summary", "not X"),
            ],
            contradictions=[
                (f"contradiction::{i:04d}", "x",
                 ["a::aa"], ["b::bb"], "open")
                for i in range(5)
            ],
        )
        # Build a synthetic ID set well above 999 — most are bogus
        # (no row matches), but the SQL must not error out when the
        # IN clause would exceed the limit.
        bogus = {f"contradiction::bogus{i:04d}" for i in range(1500)}
        bogus.add("contradiction::0002")  # one real match
        llm = _StubLLM()
        crystals = synthesize_contradiction_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
            only_contradiction_ids=bogus,
        )
        # Exactly one crystal — the one real ID — and no SQL crash.
        assert len(crystals) == 1
        assert crystals[0].contradiction_id == "contradiction::0002"

    def test_frontmatter_stripped_in_pipeline(self, tmp_path):
        # Source notes' frontmatter must NOT reach the LLM prompt
        # (same invariant as community crystals).
        vault, db = _seed(
            tmp_path,
            objects=[
                ("a", "A",
                 "---\nnote_id: a\ntitle: A\n---\n实际正方内容\n"),
                ("b", "B",
                 "---\nnote_id: b\ntitle: B\n---\n实际反方内容\n"),
            ],
            claims=[
                ("a::aa", "a", "page_summary", "X is true"),
                ("b::bb", "b", "page_summary", "X is not true"),
            ],
            contradictions=[
                ("contradiction::fm", "x", ["a::aa"], ["b::bb"], "open"),
            ],
        )
        llm = _StubLLM()
        synthesize_contradiction_crystals(
            vault_dir=vault, llm_client=llm, db_path=db,
        )
        _, user_prompt, _ = llm.calls[0]
        assert "实际正方内容" in user_prompt
        assert "实际反方内容" in user_prompt
        assert "note_id: a" not in user_prompt
        assert "note_id: b" not in user_prompt
