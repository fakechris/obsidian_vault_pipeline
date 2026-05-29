"""BL-118 — ``--incremental`` must be semantically distinct from ``--full``.

Pre-BL-118 ``_pipeline_plan.build_execution_plan`` returned the same
step list for both flags (only the ``description`` string differed),
making ``--incremental`` a no-op alias.  These tests pin the post-
BL-118 divergence: ``--incremental`` drops the ``synthesize`` step
(BL-117's LLM-bounded delta-resynthesis) while ``--full`` keeps it.

If a future refactor accidentally merges them back, these tests fail
loudly with a "the two modes are identical" message instead of
silently regressing the operator's cost surface.
"""

from __future__ import annotations

from argparse import Namespace

from ovp_pipeline.unified_pipeline_enhanced import build_execution_plan


def _base_args(**overrides) -> Namespace:
    """Common Namespace shape — overrides win.  Pre-BL-118 ``--full``
    and ``--incremental`` had identical defaults; we share the
    builder so any new field added to ``build_execution_plan``'s
    contract gets the same value in both modes."""
    defaults = dict(
        full=False,
        incremental=False,
        with_refine=False,
        pinboard_new=False,
        pinboard_history=None,
        pinboard_days=None,
        step=None,
        from_step=None,
        pack=None,
        profile=None,
    )
    defaults.update(overrides)
    return Namespace(**defaults)


def test_incremental_excludes_synthesize():
    """``--incremental`` removes ``synthesize`` from the DAG so the
    nightly run does zero LLM work, while still running every other
    step (including ``knowledge_index`` which fires BL-115/116's
    identity match + orphan supersede)."""
    plan = build_execution_plan(_base_args(incremental=True))
    assert "synthesize" not in plan["steps"]
    # Every other step is still in there.
    for required in ("absorb", "knowledge_index", "ops_state"):
        assert required in plan["steps"]


def test_full_includes_synthesize():
    """``--full`` is the LLM-bounded mode — ``synthesize`` (BL-117)
    must be in the step list."""
    plan = build_execution_plan(_base_args(full=True))
    assert "synthesize" in plan["steps"]


def test_incremental_and_full_produce_different_step_lists():
    """The headline contract: the two modes are NOT aliases.
    Pre-BL-118 this assertion would have failed (identical lists);
    post-BL-118 they differ by exactly one step (``synthesize``).
    """
    incremental = build_execution_plan(_base_args(incremental=True))
    full = build_execution_plan(_base_args(full=True))
    assert incremental["steps"] != full["steps"]
    # The diff is exactly ``synthesize``.
    assert set(full["steps"]) - set(incremental["steps"]) == {"synthesize"}
    assert set(incremental["steps"]) - set(full["steps"]) == set()


def test_incremental_step_ordering_preserves_dag():
    """Dropping ``synthesize`` must not reshuffle the surviving
    steps — the DAG order matters for downstream consumers
    (e.g. ``ops_state`` must still run after ``knowledge_index``)."""
    plan = build_execution_plan(_base_args(incremental=True))
    steps = plan["steps"]
    # knowledge_index must precede ops_state (they exchange data
    # via knowledge.db tables that knowledge_index writes).
    assert steps.index("knowledge_index") < steps.index("ops_state")
