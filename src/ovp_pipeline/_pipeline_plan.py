# BL-112: leaf-extracted from unified_pipeline_enhanced.py — verbatim move, no logic change.
import argparse
from ovp_pipeline.packs.loader import resolve_workflow_profile
from typing import Any
from ._pipeline_constants import BASE_PIPELINE_STEPS, STEP_ALIASES




def normalize_step_name(step: str | None) -> str | None:
    if step is None:
        return None
    return STEP_ALIASES.get(step, step)



def pipeline_steps(
    include_refine: bool = False,
    base_steps: list[str] | None = None,
) -> list[str]:
    steps = list(base_steps or BASE_PIPELINE_STEPS)
    if include_refine and "refine" not in steps:
        # M24.1: refine writes data that ``knowledge_index`` then
        # indexes, so refine MUST run before knowledge_index.
        # Pre-M24.1 we inserted at ``-1`` (penultimate) because
        # knowledge_index was the last step.  Today the last step
        # is ``ops_state``, so inserting at -1 would put refine
        # between knowledge_index and ops_state — wrong order.
        # Find knowledge_index explicitly and insert before it.
        try:
            idx = steps.index("knowledge_index")
            steps.insert(idx, "refine")
        except ValueError:
            # Profile doesn't include knowledge_index (e.g. a
            # custom slice).  Fall back to inserting near the end.
            steps.append("refine")
    return steps



def build_execution_plan(args: argparse.Namespace) -> dict[str, Any]:
    """Build the requested execution plan from CLI args."""
    include_refine = bool(getattr(args, "with_refine", False))
    incremental = bool(getattr(args, "incremental", False))
    pack_name = getattr(args, "pack", None)
    profile_name = getattr(args, "profile", None)
    pack, profile = resolve_workflow_profile(
        pack_name=pack_name,
        profile_name=profile_name,
        default_profile="full",
        runtime_adapter="pipeline_step",
    )
    selected_steps = pipeline_steps(include_refine=include_refine, base_steps=profile.stages)
    pinboard_selected_steps = [step for step in selected_steps if step != "clippings"]
    normalized_from_step = (
        normalize_step_name(args.from_step)
        if getattr(args, "from_step", None)
        else None
    )

    def plan_dict(steps: list[str], description: str, pinboard_days: int | None, pinboard_start: str | None, pinboard_end: str | None) -> dict[str, Any]:
        return {
            "pack": pack.name,
            "profile": profile.name,
            "steps": steps,
            "pinboard_days": pinboard_days,
            "pinboard_start": pinboard_start,
            "pinboard_end": pinboard_end,
            "description": description,
        }

    def slice_from_step(steps: list[str]) -> list[str]:
        if normalized_from_step and normalized_from_step in steps:
            return steps[steps.index(normalized_from_step):]
        return steps

    if args.full:
        requested_steps = slice_from_step(selected_steps)
        description = (
            f"Full pipeline from {normalized_from_step} ({pack.name}/{profile.name})"
            if normalized_from_step
            else f"Full pipeline ({pack.name}/{profile.name})"
        )
        return plan_dict(
            requested_steps,
            description,
            args.pinboard_days or 7,
            None,
            None,
        )

    if incremental:
        # BL-118: ``--incremental`` is the cheap nightly mode.  It runs
        # the entire DAG EXCEPT ``synthesize`` — that's the LLM-bounded
        # delta-resynthesis step BL-117 introduced.  Skipping it keeps
        # the incremental run zero-cost while still firing the BL-115
        # identity-match + BL-116 orphan-supersede inside
        # ``knowledge_index``, so ``/topics`` stays self-consistent.
        # Pre-BL-118 ``--incremental`` was a no-op alias for ``--full``;
        # the test ``tests/test_pipeline_plan.py`` pins the new
        # divergence so a future refactor can't quietly merge them
        # back together.
        full_steps = slice_from_step(selected_steps)
        requested_steps = [s for s in full_steps if s != "synthesize"]
        description = (
            f"Incremental pipeline from {normalized_from_step} ({pack.name}/{profile.name})"
            if normalized_from_step
            else f"Incremental pipeline ({pack.name}/{profile.name})"
        )
        return plan_dict(
            requested_steps,
            description,
            args.pinboard_days or 7,
            None,
            None,
        )

    if args.pinboard_new:
        return plan_dict(["pinboard", "pinboard_process"], "New Pinboard bookmarks only", 7, None, None)

    if args.pinboard_history:
        pinboard_start, pinboard_end = args.pinboard_history
        return plan_dict(
            pinboard_selected_steps,
            f"Historical Pinboard {pinboard_start} to {pinboard_end}",
            None,
            pinboard_start,
            pinboard_end,
        )

    if args.pinboard_days:
        return plan_dict(
            pinboard_selected_steps,
            f"Pinboard last {args.pinboard_days} days + full pipeline",
            args.pinboard_days,
            None,
            None,
        )

    if args.step:
        return plan_dict(
            [normalize_step_name(args.step)],
            f"Single step: {normalize_step_name(args.step)}",
            args.pinboard_days,
            None,
            None,
        )

    if args.from_step:
        return plan_dict(
            slice_from_step(selected_steps),
            f"From step: {normalized_from_step}",
            args.pinboard_days or 7,
            None,
            None,
        )

    return {}


__all__ = [
    'normalize_step_name',
    'pipeline_steps',
    'build_execution_plan'
]
