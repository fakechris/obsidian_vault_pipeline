"""Phase 36 — query feedback router.

Coverage:
* ``route_candidate_concepts`` adds new concepts to the registry (state=candidate)
* duplicate candidate is bumped, not double-inserted
* ``route_open_questions`` appends one JSONL line per question
* ``route_writing_prompts`` appends to ``00-Polaris/Writing-Prompts.md`` and
  preserves prior content (manual edits between appends survive)
* ``route_proposed_relations`` lands in the Phase 35 review queue
* each route emits a ``feedback_yield`` event into ``60-Logs/pipeline.jsonl``
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ovp_pipeline.extraction.semantic_relations import SemanticRelationCandidate
from ovp_pipeline.feedback_router import (
    CandidateConcept,
    OpenQuestion,
    WritingPrompt,
    route_candidate_concepts,
    route_open_questions,
    route_proposed_relations,
    route_writing_prompts,
)
from ovp_pipeline.packs.loader import load_pack
from ovp_pipeline.runtime import VaultLayout


def _read_pipeline_events(vault_dir: Path) -> list[dict]:
    log = vault_dir / "60-Logs" / "pipeline.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Candidate concepts
# ---------------------------------------------------------------------------


def test_route_candidate_concepts_adds_to_registry(temp_vault):
    pack = load_pack("research-tech")
    inserted = route_candidate_concepts(
        [CandidateConcept(term="Diffusion Routing", definition="x", area="AI-Research")],
        vault_dir=temp_vault,
        pack=pack,
    )
    assert inserted == 1

    from ovp_pipeline.concept_registry import ConceptRegistry

    registry = ConceptRegistry(temp_vault).load()
    assert registry.find_by_slug("diffusion-routing") is not None


def test_route_candidate_concepts_dedupes_existing(temp_vault):
    pack = load_pack("research-tech")
    route_candidate_concepts(
        [CandidateConcept(term="Diffusion Routing")],
        vault_dir=temp_vault,
        pack=pack,
    )
    # second time should return 0 (already present, just bumped)
    inserted = route_candidate_concepts(
        [CandidateConcept(term="Diffusion Routing")],
        vault_dir=temp_vault,
        pack=pack,
    )
    assert inserted == 0


# ---------------------------------------------------------------------------
# Open questions
# ---------------------------------------------------------------------------


def test_route_open_questions_appends_jsonl(temp_vault):
    pack = load_pack("research-tech")
    written = route_open_questions(
        [OpenQuestion(question="Why does X?"), OpenQuestion(question="How does Y?")],
        vault_dir=temp_vault,
        pack=pack,
    )
    assert written == 2
    log = temp_vault / "60-Logs" / "open-questions.jsonl"
    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0]["question"] == "Why does X?"


def test_route_open_questions_idempotent_appends(temp_vault):
    pack = load_pack("research-tech")
    route_open_questions([OpenQuestion(question="A?")], vault_dir=temp_vault, pack=pack)
    route_open_questions([OpenQuestion(question="B?")], vault_dir=temp_vault, pack=pack)
    log = temp_vault / "60-Logs" / "open-questions.jsonl"
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


# ---------------------------------------------------------------------------
# Writing prompts (the tricky one — append-only inside accepted zone)
# ---------------------------------------------------------------------------


def test_route_writing_prompts_appends_and_preserves_manual_edits(temp_vault):
    """Plan §7.5: insert manual edit between two appends; assert survival."""
    pack = load_pack("research-tech")

    # First append
    route_writing_prompts(
        [WritingPrompt(prompt="What if agents had episodic memory?")],
        vault_dir=temp_vault,
        pack=pack,
    )

    target = temp_vault / "00-Polaris" / "Writing-Prompts.md"
    body_after_first = target.read_text(encoding="utf-8")
    assert "episodic memory" in body_after_first

    # Manual edit between appends (simulating user typing notes)
    target.write_text(body_after_first + "\n## Manual Section\n\nUser-typed note.\n", encoding="utf-8")
    manual_marker = "User-typed note"

    # Second append
    route_writing_prompts(
        [WritingPrompt(prompt="What is the limit of context length?", rationale="Per Anthropic docs")],
        vault_dir=temp_vault,
        pack=pack,
    )

    final = target.read_text(encoding="utf-8")
    assert manual_marker in final  # manual edit survived
    assert "context length" in final  # second prompt landed
    assert "episodic memory" in final  # first prompt still present


def test_route_writing_prompts_blocked_for_pack_without_append_zone(temp_vault):
    """If a pack accidentally drops Writing-Prompts.md from append_only, the
    enforce_zone_write call must refuse the write."""
    from ovp_pipeline.workspace_promotion import ZoneViolation

    pack = load_pack("research-tech")
    target = temp_vault / "00-Polaris" / "Writing-Prompts.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Prompts\n", encoding="utf-8")
    # research-tech accepts Writing-Prompts.md as append_only — this should pass.
    route_writing_prompts(
        [WritingPrompt(prompt="ok")],
        vault_dir=temp_vault,
        pack=pack,
    )
    # Now point at an accepted file that is NOT append_only and verify the gate fires.
    sealed = temp_vault / "10-Knowledge" / "Evergreen" / "Sealed.md"
    sealed.parent.mkdir(parents=True, exist_ok=True)
    sealed.write_text("# Sealed\n", encoding="utf-8")
    from ovp_pipeline.workspace_promotion import WRITE_MODE_APPEND, enforce_zone_write

    with pytest.raises(ZoneViolation):
        enforce_zone_write(sealed, pack=pack, vault_dir=temp_vault, mode=WRITE_MODE_APPEND)


# ---------------------------------------------------------------------------
# Proposed relations → Phase 35 queue
# ---------------------------------------------------------------------------


def test_route_proposed_relations_writes_to_review_queue(temp_vault):
    pack = load_pack("research-tech")
    candidate = SemanticRelationCandidate(
        relation_type="uses",
        source_object_id="ai-agent",
        target_object_id="rag",
        source_slug="agent-memory",
        evidence_quote="AI Agent uses RAG",
        confidence=0.8,
        content_hash="abc",
        pack=pack.name,
    )
    paths = route_proposed_relations([candidate], vault_dir=temp_vault, pack=pack)
    assert len(paths) == 1

    layout = VaultLayout.from_vault(temp_vault)
    queue_files = list((layout.review_queue_dir / "semantic-relations").glob("*.json"))
    assert len(queue_files) == 1


# ---------------------------------------------------------------------------
# feedback_yield audit
# ---------------------------------------------------------------------------


def test_doctor_feedback_payload_counts_yields(temp_vault):
    pack = load_pack("research-tech")
    route_candidate_concepts(
        [CandidateConcept(term="X")],
        vault_dir=temp_vault,
        pack=pack,
    )
    route_open_questions([OpenQuestion(question="?")], vault_dir=temp_vault, pack=pack)

    from ovp_pipeline.commands.doctor import _feedback_payload

    payload = _feedback_payload(temp_vault, pack_name=pack.name)
    assert payload["candidate_yield"] == 1
    assert payload["open_questions"] == 1
    assert payload["events_total"] == 2


def test_ui_open_questions_fragment_renders(temp_vault):
    pack = load_pack("research-tech")
    route_open_questions(
        [OpenQuestion(question="What is X?"), OpenQuestion(question="What is Y?")],
        vault_dir=temp_vault,
        pack=pack,
    )
    from ovp_pipeline.commands.ui_server import (
        _build_open_questions_payload,
        _render_open_questions_fragment,
    )

    payload = _build_open_questions_payload(temp_vault)
    html = _render_open_questions_fragment(payload)
    assert "What is X?" in html
    assert "What is Y?" in html


def test_ui_writing_prompts_fragment_renders(temp_vault):
    pack = load_pack("research-tech")
    route_writing_prompts(
        [WritingPrompt(prompt="Draft something about retrieval")],
        vault_dir=temp_vault,
        pack=pack,
    )
    from ovp_pipeline.commands.ui_server import (
        _build_writing_prompts_payload,
        _render_writing_prompts_fragment,
    )

    payload = _build_writing_prompts_payload(temp_vault)
    html = _render_writing_prompts_fragment(payload)
    assert "retrieval" in html


def test_router_emits_feedback_yield_events(temp_vault):
    pack = load_pack("research-tech")
    route_candidate_concepts(
        [CandidateConcept(term="X-Memory")],
        vault_dir=temp_vault,
        pack=pack,
    )
    route_open_questions(
        [OpenQuestion(question="Q?")],
        vault_dir=temp_vault,
        pack=pack,
    )
    events = _read_pipeline_events(temp_vault)
    yields = [e for e in events if e.get("event_type") == "feedback_yield"]
    streams = {e["stream"] for e in yields}
    assert "candidate_concept" in streams
    assert "open_question" in streams
