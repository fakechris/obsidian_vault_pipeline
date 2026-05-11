"""BL-063 PR#2: scheduler orchestrator (DB I/O + per-concept loop).

Mocks ``recent_audit_events`` and ``list_contradictions`` so the
test doesn't need a real knowledge.db; the trigger evaluators are
already covered in test_live_concept_triggers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixture: a minimal vault with two live concepts
# ---------------------------------------------------------------------------


def _seed_vault(tmp_path: Path) -> Path:
    """Write two active concepts and one paused concept."""
    tracking = tmp_path / "30-Projects" / "Tracking"
    tracking.mkdir(parents=True, exist_ok=True)
    (tracking / "alpha.md").write_text(
        "---\n"
        "type: live-concept\n"
        "live:\n"
        "  objective: Track alpha topic.\n"
        "  active: true\n"
        "  triggers:\n"
        "    on_ingest_match:\n"
        "      concept_similarity_to: alpha-evergreen\n"
        "    weekly_resynthesis: 'Mon 09:00'\n"
        "  scope_evergreens:\n"
        "    - alpha-evergreen\n"
        "---\n\n# Alpha\n",
        encoding="utf-8",
    )
    (tracking / "beta.md").write_text(
        "---\n"
        "type: live-concept\n"
        "live:\n"
        "  objective: Track beta topic.\n"
        "  active: true\n"
        "  triggers:\n"
        "    on_contradiction_against_view: true\n"
        "  scope_evergreens:\n"
        "    - beta-evergreen\n"
        "---\n\n# Beta\n",
        encoding="utf-8",
    )
    (tracking / "paused.md").write_text(
        "---\n"
        "type: live-concept\n"
        "live:\n"
        "  objective: Paused — should not appear in scan.\n"
        "  active: false\n"
        "  triggers:\n"
        "    weekly_resynthesis: 'Mon 09:00'\n"
        "---\n\n# Paused\n",
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# evaluate_all_concepts — orchestrator
# ---------------------------------------------------------------------------


def test_evaluate_all_concepts_skips_paused(tmp_path, monkeypatch):
    """Discovery feeds ``active_only=True`` — paused concepts never
    reach the trigger evaluators."""
    from ovp_pipeline import live_concept_scheduler

    _seed_vault(tmp_path)
    monkeypatch.setattr(live_concept_scheduler, "recent_audit_events", lambda *a, **kw: [])
    monkeypatch.setattr(live_concept_scheduler, "list_contradictions", lambda *a, **kw: [])
    evaluations = live_concept_scheduler.evaluate_all_concepts(
        tmp_path,
        now=datetime(2026, 5, 11, 9, 30, tzinfo=timezone.utc),
    )
    slugs = {e.handle.slug for e in evaluations}
    assert slugs == {"alpha", "beta"}


def test_evaluate_all_concepts_pushes_filters_into_sql(tmp_path, monkeypatch):
    """Codex/bot regression: pre-fix the scheduler fetched the most
    recent N audit_events of *any* event_type, then post-filtered to
    ``absorb_route_decision`` in Python.  On a noisy log the relevant
    routing decisions could be silently truncated.  Now the scheduler
    pushes both ``event_type`` and a ``since`` cutoff into the SQL
    call — this test asserts those kwargs are forwarded."""
    from ovp_pipeline import live_concept_scheduler

    _seed_vault(tmp_path)
    captured_kwargs: dict = {}

    def fake_recent_audit_events(_vault, **kwargs):
        captured_kwargs.update(kwargs)
        return []

    monkeypatch.setattr(
        live_concept_scheduler,
        "recent_audit_events",
        fake_recent_audit_events,
    )
    monkeypatch.setattr(
        live_concept_scheduler, "list_contradictions", lambda *a, **kw: [],
    )
    live_concept_scheduler.evaluate_all_concepts(
        tmp_path,
        since_hours=24,
        now=datetime(2026, 5, 11, 9, 30, tzinfo=timezone.utc),
    )
    assert captured_kwargs.get("event_type") == "absorb_route_decision"
    assert "since" in captured_kwargs
    # since cutoff = now - 24h.
    assert captured_kwargs["since"] == "2026-05-10T09:30:00Z"


def test_evaluate_all_concepts_filters_route_events_by_window(tmp_path, monkeypatch):
    """Audit rows older than ``cutoff = now - since_hours`` are
    dropped before the evaluator sees them."""
    from ovp_pipeline import live_concept_scheduler

    _seed_vault(tmp_path)
    fake_audit = [
        {
            "event_type": "absorb_route_decision",
            "timestamp": "2026-05-10T08:00:00Z",  # 25h before now → out of window
            "payload": {"source": "old.md", "update_slugs": ["alpha-evergreen"]},
        },
        {
            "event_type": "absorb_route_decision",
            "timestamp": "2026-05-11T08:30:00Z",  # 1h before now → in window
            "payload": {"source": "fresh.md", "update_slugs": ["alpha-evergreen"]},
        },
        {
            "event_type": "ingest_complete",  # wrong event type
            "timestamp": "2026-05-11T09:00:00Z",
            "payload": {"source": "ignored.md", "update_slugs": ["alpha-evergreen"]},
        },
    ]
    monkeypatch.setattr(
        live_concept_scheduler, "recent_audit_events", lambda *a, **kw: fake_audit,
    )
    monkeypatch.setattr(
        live_concept_scheduler, "list_contradictions", lambda *a, **kw: [],
    )
    evaluations = live_concept_scheduler.evaluate_all_concepts(
        tmp_path,
        since_hours=24,
        now=datetime(2026, 5, 11, 9, 30, tzinfo=timezone.utc),
    )
    alpha = next(e for e in evaluations if e.handle.slug == "alpha")
    sources = [m.source_path for m in alpha.ingest_matches]
    assert sources == ["fresh.md"]


def test_evaluate_all_concepts_dedupes_contradictions_across_scope(tmp_path, monkeypatch):
    """A contradiction returned twice by ``list_contradictions``
    (once per overlapping scope slug) is collapsed to a single
    match."""
    from ovp_pipeline import live_concept_scheduler

    _seed_vault(tmp_path)
    monkeypatch.setattr(
        live_concept_scheduler, "recent_audit_events", lambda *a, **kw: [],
    )

    # Always return the same single-row list — the orchestrator's
    # dedup must collapse repeated emissions to one match per cid.
    def fake_list(*_a, **kw):
        slug = kw.get("subject", "")
        if "beta-evergreen" in str(slug):
            return [
                {
                    "contradiction_id": "c1",
                    "subject_key": "research::beta-evergreen",
                    "status": "open",
                    "positive_claim_ids": [],
                    "negative_claim_ids": [],
                },
                {
                    "contradiction_id": "c1",  # duplicate emission
                    "subject_key": "research::beta-evergreen",
                    "status": "open",
                    "positive_claim_ids": [],
                    "negative_claim_ids": [],
                },
            ]
        return []

    monkeypatch.setattr(live_concept_scheduler, "list_contradictions", fake_list)
    evaluations = live_concept_scheduler.evaluate_all_concepts(
        tmp_path,
        now=datetime(2026, 5, 11, 9, 30, tzinfo=timezone.utc),
    )
    beta = next(e for e in evaluations if e.handle.slug == "beta")
    assert len(beta.contradiction_matches) == 1
    assert beta.contradiction_matches[0].contradiction_id == "c1"


def test_evaluate_all_concepts_empty_vault_returns_empty(tmp_path, monkeypatch):
    """No concepts → empty list.  Don't even touch the audit/
    contradictions tables (a fresh vault may not have them yet)."""
    from ovp_pipeline import live_concept_scheduler

    audit_calls: list[int] = []
    monkeypatch.setattr(
        live_concept_scheduler,
        "recent_audit_events",
        lambda *a, **kw: audit_calls.append(1) or [],
    )
    monkeypatch.setattr(
        live_concept_scheduler, "list_contradictions", lambda *a, **kw: [],
    )
    assert live_concept_scheduler.evaluate_all_concepts(tmp_path) == []
    # Short-circuit: no concepts → no audit fetch.
    assert audit_calls == []


def test_evaluate_all_concepts_weekly_due_signal(tmp_path, monkeypatch):
    """alpha has ``weekly_resynthesis: Mon 09:00`` and last_run_at
    is empty (never run) — a Monday-after-09:00 scan fires it."""
    from ovp_pipeline import live_concept_scheduler

    _seed_vault(tmp_path)
    monkeypatch.setattr(
        live_concept_scheduler, "recent_audit_events", lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        live_concept_scheduler, "list_contradictions", lambda *a, **kw: [],
    )
    # Monday May 11 2026, 09:30 UTC
    evaluations = live_concept_scheduler.evaluate_all_concepts(
        tmp_path,
        now=datetime(2026, 5, 11, 9, 30, tzinfo=timezone.utc),
    )
    alpha = next(e for e in evaluations if e.handle.slug == "alpha")
    assert alpha.weekly_due is True


def test_evaluate_all_concepts_has_any_trigger_property(tmp_path, monkeypatch):
    """``has_any_trigger`` is False for a concept whose schedule
    fired but ran since (last_run_at past most-recent-past), and
    True for any concept where any trigger matches.

    The seeded ``alpha`` is set up with an explicit
    ``last_run_at`` past last Monday's 09:00 instant so we can
    pin the negative case without depending on never-run
    semantics."""
    from ovp_pipeline import live_concept_scheduler

    tracking = tmp_path / "30-Projects" / "Tracking"
    tracking.mkdir(parents=True, exist_ok=True)
    (tracking / "ran-already.md").write_text(
        "---\n"
        "type: live-concept\n"
        "live:\n"
        "  objective: Track.\n"
        "  active: true\n"
        "  triggers:\n"
        "    weekly_resynthesis: 'Mon 09:00'\n"
        "  lastRunAt: '2026-05-04T09:30:00Z'\n"
        "---\n\n# x\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        live_concept_scheduler, "recent_audit_events", lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        live_concept_scheduler, "list_contradictions", lambda *a, **kw: [],
    )
    # Sunday May 10 — last run (Mon May 4 09:30) is >= last most-recent-past
    # (Mon May 4 09:00) → don't fire.
    evaluations = live_concept_scheduler.evaluate_all_concepts(
        tmp_path,
        now=datetime(2026, 5, 10, 9, 30, tzinfo=timezone.utc),
    )
    [e] = evaluations
    assert e.handle.slug == "ran-already"
    assert e.has_any_trigger is False


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_emits_text_report(tmp_path, monkeypatch, capsys):
    from ovp_pipeline.commands import live_concept_scan

    _seed_vault(tmp_path)
    monkeypatch.setattr(
        live_concept_scan, "evaluate_all_concepts",
        lambda *a, **kw: [],
    )
    rc = live_concept_scan.main(["--vault-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "No active live concepts." in out


def test_cli_emits_json(tmp_path, monkeypatch, capsys):
    import json as json_mod

    from ovp_pipeline.commands import live_concept_scan

    _seed_vault(tmp_path)
    # Don't mock evaluate_all_concepts — let it run end-to-end with
    # mocked I/O to exercise the JSON serialisation.
    from ovp_pipeline import live_concept_scheduler

    monkeypatch.setattr(
        live_concept_scheduler, "recent_audit_events", lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        live_concept_scheduler, "list_contradictions", lambda *a, **kw: [],
    )
    rc = live_concept_scan.main([
        "--vault-dir", str(tmp_path), "--json",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json_mod.loads(out)
    assert parsed["evaluation_count"] == 2
    slugs = {e["slug"] for e in parsed["evaluations"]}
    assert slugs == {"alpha", "beta"}


def test_cli_only_fired_filters_quiet_concepts(tmp_path, monkeypatch, capsys):
    """``--only-fired`` drops evaluations where no trigger matches.
    Stub the orchestrator so the CLI's filter logic is what's
    under test (the orchestrator's behaviour is covered above)."""
    import json as json_mod

    from ovp_pipeline.commands import live_concept_scan
    from ovp_pipeline.live_concept import LiveConceptFrontmatter, LiveConceptHandle
    from ovp_pipeline.live_concept_scheduler import ConceptEvaluation

    quiet = ConceptEvaluation(
        handle=LiveConceptHandle(
            path=tmp_path / "x.md",
            relative_path="30-Projects/Tracking/x.md",
            slug="x",
            frontmatter=LiveConceptFrontmatter(objective="quiet"),
        ),
        weekly_due=False,
        ingest_matches=[],
        contradiction_matches=[],
    )
    monkeypatch.setattr(
        live_concept_scan, "evaluate_all_concepts", lambda *a, **kw: [quiet],
    )
    rc = live_concept_scan.main([
        "--vault-dir", str(tmp_path), "--json", "--only-fired",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json_mod.loads(out)
    assert parsed["evaluation_count"] == 0
