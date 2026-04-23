"""Phase 33 — re-locatable evidence (locator/hash/context/status/verified_at).

Coverage matrix from plan §4.6:

* schema survives rebuild
* backfill yields ``unverified`` (not silent-pass) when source path is missing
* source mutation flips ``verified → stale``
* deletion flips ``verified → broken``
* lint refuses promotion of research-tech claim missing ``locator``/``content_hash``
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ovp_pipeline.evidence import (
    compute_content_hash,
    compute_locator,
    compute_retrieval_context,
    verify_evidence_row,
)
from ovp_pipeline.knowledge_index import rebuild_knowledge_index
from ovp_pipeline.runtime import VaultLayout
from ovp_pipeline.truth_store import (
    EVIDENCE_STATUS_BROKEN,
    EVIDENCE_STATUS_STALE,
    EVIDENCE_STATUS_UNVERIFIED,
    EVIDENCE_STATUS_VERIFIED,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_source(temp_vault: Path, name: str, body: str) -> Path:
    """Write a markdown source file under Evergreen/ and return its absolute path."""
    path = temp_vault / "10-Knowledge" / "Evergreen" / name
    path.write_text(body, encoding="utf-8")
    return path


def _seed_claim_evidence_row(
    db_path: Path,
    *,
    pack: str,
    claim_id: str,
    source_slug: str,
    quote_text: str,
    locator: str = "",
    content_hash: str = "",
    retrieval_context: str = "",
    status: str = EVIDENCE_STATUS_UNVERIFIED,
    verified_at: str = "",
) -> None:
    """Insert one ``claim_evidence`` row directly. Bypasses rebuild for test isolation."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO claim_evidence
              (pack, claim_id, source_slug, evidence_kind, quote_text,
               locator, content_hash, retrieval_context, status, verified_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pack,
                claim_id,
                source_slug,
                "page_summary",
                quote_text,
                locator,
                content_hash,
                retrieval_context,
                status,
                verified_at,
            ),
        )
        conn.commit()


def _clear_claim_evidence(db_path: Path) -> None:
    """Drop rebuild-emitted rows so tests can assert on isolated seeded fixtures."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM claim_evidence")
        conn.commit()


# ---------------------------------------------------------------------------
# 1. Schema survives rebuild
# ---------------------------------------------------------------------------


def test_claim_evidence_schema_has_phase33_columns(temp_vault):
    """Rebuild produces a knowledge.db whose claim_evidence table carries
    locator/content_hash/retrieval_context/status/verified_at."""
    _write_source(
        temp_vault,
        "Source.md",
        """---
note_id: source-note
title: Source Note
type: evergreen
date: 2026-04-22
---

# Source Note
""",
    )
    rebuild_knowledge_index(temp_vault)

    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(claim_evidence)")}
    assert {
        "locator",
        "content_hash",
        "retrieval_context",
        "status",
        "verified_at",
    }.issubset(columns)


def test_relations_schema_has_phase33_columns(temp_vault):
    """Phase 35 will need evidence on relations rows; schema must already carry them."""
    _write_source(
        temp_vault,
        "Source.md",
        """---
note_id: source-note
title: Source Note
type: evergreen
date: 2026-04-22
---

# Source Note
""",
    )
    rebuild_knowledge_index(temp_vault)

    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(relations)")}
    assert {
        "locator",
        "content_hash",
        "retrieval_context",
        "status",
        "verified_at",
    }.issubset(columns)


# ---------------------------------------------------------------------------
# 2. evidence.py helpers — content_hash / locator / retrieval_context
# ---------------------------------------------------------------------------


def test_compute_content_hash_is_stable_and_changes_on_mutation(tmp_path):
    src = tmp_path / "snippet.md"
    src.write_text("# Heading\n\nbody text", encoding="utf-8")
    first = compute_content_hash(src)
    second = compute_content_hash(src)
    assert first == second
    assert first  # non-empty

    src.write_text("# Heading\n\nMUTATED body", encoding="utf-8")
    after = compute_content_hash(src)
    assert after and after != first


def test_compute_content_hash_returns_empty_for_missing_file(tmp_path):
    assert compute_content_hash(tmp_path / "absent.md") == ""


def test_compute_locator_returns_section_paragraph_pointer(tmp_path):
    src = tmp_path / "doc.md"
    src.write_text(
        "# Top\n\nintro paragraph\n\n## Sub Section\n\nfirst para\n\n"
        "the unique sentence we want.\n\n## Other\n\nelsewhere\n",
        encoding="utf-8",
    )
    locator = compute_locator(src, "the unique sentence we want.")
    assert locator.startswith("section#sub-section@")


def test_compute_locator_returns_empty_when_quote_missing(tmp_path):
    src = tmp_path / "doc.md"
    src.write_text("# Heading\n\nsome body", encoding="utf-8")
    assert compute_locator(src, "absent quote") == ""


def test_compute_retrieval_context_centers_on_quote(tmp_path):
    src = tmp_path / "doc.md"
    body = "prefix " * 30 + "ANCHOR" + " suffix" * 30
    src.write_text(body, encoding="utf-8")
    context = compute_retrieval_context(src, "ANCHOR", radius=20)
    assert "ANCHOR" in context
    assert len(context) <= len("ANCHOR") + 40 + 5  # radius padding tolerance


# ---------------------------------------------------------------------------
# 3. verify_evidence_row state machine
# ---------------------------------------------------------------------------


def test_verify_evidence_row_unverified_when_no_stored_hash(tmp_path):
    src = tmp_path / "vault" / "10-Knowledge" / "Evergreen" / "Source.md"
    src.parent.mkdir(parents=True)
    src.write_text("body", encoding="utf-8")

    status, verified_at = verify_evidence_row(
        {
            "source_slug": "10-Knowledge/Evergreen/Source.md",
            "quote_text": "body",
            "content_hash": "",
        },
        vault_dir=tmp_path / "vault",
    )
    assert status == EVIDENCE_STATUS_UNVERIFIED
    assert verified_at == ""


def test_verify_evidence_row_verified_when_hash_matches(tmp_path):
    src = tmp_path / "vault" / "10-Knowledge" / "Evergreen" / "Source.md"
    src.parent.mkdir(parents=True)
    src.write_text("anchor body", encoding="utf-8")
    stored = compute_content_hash(src)

    status, verified_at = verify_evidence_row(
        {
            "source_slug": "10-Knowledge/Evergreen/Source.md",
            "quote_text": "anchor body",
            "content_hash": stored,
        },
        vault_dir=tmp_path / "vault",
    )
    assert status == EVIDENCE_STATUS_VERIFIED
    assert verified_at  # ISO-8601 stamp


def test_verify_evidence_row_stale_when_source_mutates(tmp_path):
    src = tmp_path / "vault" / "10-Knowledge" / "Evergreen" / "Source.md"
    src.parent.mkdir(parents=True)
    src.write_text("anchor body", encoding="utf-8")
    stored = compute_content_hash(src)
    src.write_text("anchor body MUTATED", encoding="utf-8")

    status, verified_at = verify_evidence_row(
        {
            "source_slug": "10-Knowledge/Evergreen/Source.md",
            "quote_text": "anchor body",
            "content_hash": stored,
        },
        vault_dir=tmp_path / "vault",
    )
    assert status == EVIDENCE_STATUS_STALE
    assert verified_at


def test_verify_evidence_row_broken_when_source_deleted(tmp_path):
    src = tmp_path / "vault" / "10-Knowledge" / "Evergreen" / "Source.md"
    src.parent.mkdir(parents=True)
    src.write_text("anchor body", encoding="utf-8")
    stored = compute_content_hash(src)
    src.unlink()

    status, verified_at = verify_evidence_row(
        {
            "source_slug": "10-Knowledge/Evergreen/Source.md",
            "quote_text": "anchor body",
            "content_hash": stored,
        },
        vault_dir=tmp_path / "vault",
    )
    assert status == EVIDENCE_STATUS_BROKEN
    assert verified_at  # broken still gets a timestamp


# ---------------------------------------------------------------------------
# 4. ovp-evidence backfill / verify CLI
# ---------------------------------------------------------------------------


def test_evidence_backfill_unverified_for_seeded_row_without_hash(temp_vault, capsys):
    """A row with quote_text but no content_hash backfills to a real hash and
    verifies to ``verified`` — not silent-pass."""
    src = _write_source(
        temp_vault,
        "Anchor.md",
        """---
note_id: anchor
title: Anchor
type: evergreen
date: 2026-04-22
---

# Anchor

The signal phrase to locate.
""",
    )
    rebuild_knowledge_index(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db

    _seed_claim_evidence_row(
        db_path,
        pack="research-tech",
        claim_id="claim-1",
        source_slug="10-Knowledge/Evergreen/Anchor.md",
        quote_text="The signal phrase to locate.",
    )

    from ovp_pipeline.commands.evidence_verify import main as evidence_main

    rc = evidence_main([
        "backfill",
        "--vault-dir", str(temp_vault),
        "--pack", "research-tech",
        "--json",
    ])
    assert rc == 0
    captured = json.loads(capsys.readouterr().out)
    summaries = {row["table"]: row for row in captured["summaries"]}
    assert summaries["claim_evidence"]["examined"] >= 1
    assert summaries["claim_evidence"]["backfilled"] >= 1
    assert summaries["claim_evidence"]["by_status"][EVIDENCE_STATUS_VERIFIED] >= 1

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT locator, content_hash, status, verified_at FROM claim_evidence "
            "WHERE pack='research-tech' AND claim_id='claim-1'"
        ).fetchone()
    locator, content_hash, status, verified_at = row
    assert content_hash  # backfilled
    assert locator.startswith("section#")  # quote was locatable
    assert status == EVIDENCE_STATUS_VERIFIED
    assert verified_at


def test_evidence_verify_flips_verified_to_stale_after_source_mutation(temp_vault, capsys):
    src = _write_source(
        temp_vault,
        "Mutating.md",
        """---
note_id: mutating
title: Mutating
type: evergreen
date: 2026-04-22
---

# Mutating

original signal phrase
""",
    )
    rebuild_knowledge_index(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    stored = compute_content_hash(src)
    _seed_claim_evidence_row(
        db_path,
        pack="research-tech",
        claim_id="claim-mut",
        source_slug="10-Knowledge/Evergreen/Mutating.md",
        quote_text="original signal phrase",
        locator="section#mutating@0",
        content_hash=stored,
        status=EVIDENCE_STATUS_VERIFIED,
        verified_at="2026-04-21T00:00:00Z",
    )

    src.write_text(src.read_text(encoding="utf-8") + "\n\nappended", encoding="utf-8")

    from ovp_pipeline.commands.evidence_verify import main as evidence_main

    rc = evidence_main([
        "verify",
        "--vault-dir", str(temp_vault),
        "--pack", "research-tech",
        "--recent", "0",
        "--json",
    ])
    assert rc == 0
    capsys.readouterr()

    with sqlite3.connect(db_path) as conn:
        status = conn.execute(
            "SELECT status FROM claim_evidence WHERE claim_id='claim-mut'"
        ).fetchone()[0]
    assert status == EVIDENCE_STATUS_STALE


def test_evidence_verify_flips_verified_to_broken_after_source_deletion(temp_vault, capsys):
    src = _write_source(
        temp_vault,
        "Doomed.md",
        """---
note_id: doomed
title: Doomed
type: evergreen
date: 2026-04-22
---

# Doomed

phrase before deletion
""",
    )
    rebuild_knowledge_index(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    stored = compute_content_hash(src)
    _seed_claim_evidence_row(
        db_path,
        pack="research-tech",
        claim_id="claim-doom",
        source_slug="10-Knowledge/Evergreen/Doomed.md",
        quote_text="phrase before deletion",
        content_hash=stored,
        status=EVIDENCE_STATUS_VERIFIED,
        verified_at="2026-04-21T00:00:00Z",
    )
    src.unlink()

    from ovp_pipeline.commands.evidence_verify import main as evidence_main

    rc = evidence_main([
        "verify",
        "--vault-dir", str(temp_vault),
        "--pack", "research-tech",
        "--recent", "0",
        "--json",
    ])
    assert rc == 0
    capsys.readouterr()

    with sqlite3.connect(db_path) as conn:
        status = conn.execute(
            "SELECT status FROM claim_evidence WHERE claim_id='claim-doom'"
        ).fetchone()[0]
    assert status == EVIDENCE_STATUS_BROKEN


def test_evidence_verify_emits_audit_event(temp_vault, capsys):
    """Verifier must drop a single ``evidence_reverified`` event into pipeline.jsonl
    so reuse-event trustedness can recompute on the next index rebuild."""
    _write_source(
        temp_vault,
        "Audited.md",
        """---
note_id: audited
title: Audited
type: evergreen
date: 2026-04-22
---

# Audited
""",
    )
    rebuild_knowledge_index(temp_vault)

    from ovp_pipeline.commands.evidence_verify import main as evidence_main

    rc = evidence_main([
        "verify",
        "--vault-dir", str(temp_vault),
        "--pack", "research-tech",
        "--recent", "0",
        "--json",
    ])
    assert rc == 0
    capsys.readouterr()

    log_path = VaultLayout.from_vault(temp_vault).pipeline_log
    assert log_path.exists()
    events = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(event.get("event_type") == "evidence_reverified" for event in events)


# ---------------------------------------------------------------------------
# 5. Lint EVIDENCE_INCOMPLETE on research-tech
# ---------------------------------------------------------------------------


def test_lint_flags_research_tech_claim_missing_locator(temp_vault):
    src = _write_source(
        temp_vault,
        "Tracked.md",
        """---
note_id: tracked
title: Tracked
type: evergreen
date: 2026-04-22
---

# Tracked

evidentiary phrase
""",
    )
    rebuild_knowledge_index(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    _clear_claim_evidence(db_path)
    _seed_claim_evidence_row(
        db_path,
        pack="research-tech",
        claim_id="claim-incomplete",
        source_slug="10-Knowledge/Evergreen/Tracked.md",
        quote_text="evidentiary phrase",
        locator="",      # missing
        content_hash="", # missing
    )

    from ovp_pipeline.lint_checker import KnowledgeLinter

    linter = KnowledgeLinter(temp_vault, wigs_mode=False)
    linter.scan()
    linter.check_evidence_completeness()

    incomplete = [
        issue for issue in linter.issues if issue.type == KnowledgeLinter.EVIDENCE_INCOMPLETE
    ]
    assert len(incomplete) == 1
    assert "locator" in incomplete[0].message
    assert "content_hash" in incomplete[0].message


def test_lint_skips_default_knowledge_pack_for_evidence_completeness(temp_vault):
    """default-knowledge is permissive — incomplete rows must not lint until
    Phase 34 lets the pack opt in."""
    _write_source(
        temp_vault,
        "Lenient.md",
        """---
note_id: lenient
title: Lenient
type: evergreen
date: 2026-04-22
---

# Lenient

phrase
""",
    )
    rebuild_knowledge_index(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    _clear_claim_evidence(db_path)
    _seed_claim_evidence_row(
        db_path,
        pack="default-knowledge",
        claim_id="claim-default",
        source_slug="10-Knowledge/Evergreen/Lenient.md",
        quote_text="phrase",
    )

    from ovp_pipeline.lint_checker import KnowledgeLinter

    linter = KnowledgeLinter(temp_vault, wigs_mode=False)
    linter.scan()
    linter.check_evidence_completeness()
    incomplete = [
        issue for issue in linter.issues if issue.type == KnowledgeLinter.EVIDENCE_INCOMPLETE
    ]
    assert incomplete == []


# ---------------------------------------------------------------------------
# 6. Doctor Evidence Health panel
# ---------------------------------------------------------------------------


def test_doctor_payload_includes_evidence_health(temp_vault):
    _write_source(
        temp_vault,
        "Watched.md",
        """---
note_id: watched
title: Watched
type: evergreen
date: 2026-04-22
---

# Watched
""",
    )
    rebuild_knowledge_index(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    _seed_claim_evidence_row(
        db_path,
        pack="research-tech",
        claim_id="claim-watched-1",
        source_slug="10-Knowledge/Evergreen/Watched.md",
        quote_text="x",
        status=EVIDENCE_STATUS_STALE,
    )
    _seed_claim_evidence_row(
        db_path,
        pack="research-tech",
        claim_id="claim-watched-2",
        source_slug="10-Knowledge/Evergreen/Watched.md",
        quote_text="y",
        status=EVIDENCE_STATUS_BROKEN,
    )

    from ovp_pipeline.commands.doctor import _payload

    payload = _payload("research-tech", temp_vault)
    health = payload["evidence_health"]
    assert health["knowledge_db_exists"] is True
    assert health["claim_evidence"].get(EVIDENCE_STATUS_STALE) == 1
    assert health["claim_evidence"].get(EVIDENCE_STATUS_BROKEN) == 1
    stale_sources = {row["source_slug"] for row in health["top_stale_sources"]}
    assert "10-Knowledge/Evergreen/Watched.md" in stale_sources


def test_evidence_verify_does_not_clobber_sibling_relation_rows(temp_vault, capsys):
    """Two relation rows sharing (pack, source, target, type) but different
    evidence slugs must verify independently. Regression for the missing
    ``evidence_source_slug`` in the UPDATE WHERE clause — the old key matched
    both rows, so verifying one would silently overwrite the other.
    """
    src1 = _write_source(
        temp_vault,
        "DeepDive-One.md",
        "---\nnote_id: dd1\ntitle: DD1\ntype: derived\ndate: 2026-04-22\n---\n\nFirst quote.\n",
    )
    src2 = _write_source(
        temp_vault,
        "DeepDive-Two.md",
        "---\nnote_id: dd2\ntitle: DD2\ntype: derived\ndate: 2026-04-22\n---\n\nSecond quote.\n",
    )
    rebuild_knowledge_index(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db

    # Two rows: same triple, different evidence slugs + quotes + content_hashes.
    with sqlite3.connect(db_path) as conn:
        for slug, quote, src in (
            ("10-Knowledge/Evergreen/DeepDive-One.md", "First quote.", src1),
            ("10-Knowledge/Evergreen/DeepDive-Two.md", "Second quote.", src2),
        ):
            conn.execute(
                """
                INSERT INTO relations
                  (pack, source_object_id, target_object_id, relation_type,
                   evidence_source_slug, quote_text, locator, content_hash,
                   retrieval_context, status, verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "research-tech", "ai-agent", "rag", "uses",
                    slug, quote, "", "", "",
                    EVIDENCE_STATUS_UNVERIFIED, "",
                ),
            )
        conn.commit()

    from ovp_pipeline.commands.evidence_verify import main as evidence_main

    rc = evidence_main([
        "backfill",
        "--vault-dir", str(temp_vault),
        "--pack", "research-tech",
        "--json",
    ])
    assert rc == 0
    capsys.readouterr()

    expected_hash_1 = compute_content_hash(src1)
    expected_hash_2 = compute_content_hash(src2)
    with sqlite3.connect(db_path) as conn:
        rows = dict(
            conn.execute(
                "SELECT evidence_source_slug, content_hash FROM relations "
                "WHERE pack='research-tech' AND source_object_id='ai-agent'"
            ).fetchall()
        )
    assert rows["10-Knowledge/Evergreen/DeepDive-One.md"] == expected_hash_1
    assert rows["10-Knowledge/Evergreen/DeepDive-Two.md"] == expected_hash_2
    assert expected_hash_1 != expected_hash_2  # the bug masked precisely this
