"""Regression guard: workflow profile stages must include every
step in ``BASE_PIPELINE_STEPS``.

M25.6 dogfood pass on the operator vault caught a real bug — the
new M24.1 ``ops_state`` step landed in ``BASE_PIPELINE_STEPS`` but
the ``WorkflowProfile.stages`` list in
``packs/research_tech/shared.py`` was not updated.  Profile takes
precedence in ``_run_pipeline_locked``, so ``ops_state`` never
actually ran during ``ovp --incremental`` and the projection on
the live vault stayed stale.

This module locks the contract so a future PR that adds a DAG
step but forgets to wire it into the workflow profile fails CI
loudly.
"""

from __future__ import annotations


def test_research_tech_full_profile_includes_every_base_step():
    """Every step in ``BASE_PIPELINE_STEPS`` MUST appear in the
    ``full`` profile's stages list.  A step missing from the
    profile silently runs nothing on ``ovp --full`` /
    ``ovp --incremental``, which is the bug M25.6 caught."""
    from ovp_pipeline.packs.loader import load_pack
    from ovp_pipeline.unified_pipeline_enhanced import BASE_PIPELINE_STEPS

    pack = load_pack("research-tech")
    profiles = {p.name: p for p in pack.workflow_profiles()}
    assert "full" in profiles

    full_stages = set(profiles["full"].stages)
    missing = set(BASE_PIPELINE_STEPS) - full_stages
    assert not missing, (
        f"WorkflowProfile 'full' is missing DAG steps {sorted(missing)}.  "
        "Add them to ``packs/research_tech/shared.py::build_workflow_profiles``"
        " so ``ovp --full`` and ``ovp --incremental`` actually run them."
    )


def test_research_tech_full_profile_stages_are_in_canonical_order():
    """The order matters — ``ops_state`` reads what
    ``knowledge_index`` just rebuilt, so it must run AFTER.
    Lock the relative order so a re-order doesn't accidentally
    flip the dependency."""
    from ovp_pipeline.packs.loader import load_pack
    from ovp_pipeline.unified_pipeline_enhanced import BASE_PIPELINE_STEPS

    pack = load_pack("research-tech")
    full = next(p for p in pack.workflow_profiles() if p.name == "full")
    # Stage indices must be monotone in the base order.
    base_index = {s: i for i, s in enumerate(BASE_PIPELINE_STEPS)}
    indices = [base_index[s] for s in full.stages if s in base_index]
    assert indices == sorted(indices), (
        f"Profile 'full' stages out of order: got {full.stages}, "
        f"canonical order is {BASE_PIPELINE_STEPS}"
    )


def test_autopilot_profile_includes_ops_state():
    """Autopilot runtime also runs ops_state at end of cycle —
    same reason as the full profile.  Lock it independently."""
    from ovp_pipeline.packs.loader import load_pack

    pack = load_pack("research-tech")
    autopilot = next(
        p for p in pack.workflow_profiles() if p.name == "autopilot"
    )
    assert "ops_state" in autopilot.stages, (
        "Autopilot profile must run ops_state — without it the "
        "projection gets stale after every autopilot cycle."
    )
    # Order: must come after knowledge_index.
    ki_idx = autopilot.stages.index("knowledge_index")
    os_idx = autopilot.stages.index("ops_state")
    assert os_idx > ki_idx
