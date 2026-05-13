"""``DIGEST-*`` handler — M23 daily knowledge feedback.

Replaces M20's crystal-only digest with a **four-layer daily knowledge
feedback** structure (M23 / BL-095):

  Layer 0 — today's intake from ``audit_events`` (acknowledgment)
  Layer 1 — evergreen delta from ``evergreen_revisions`` (new thinking)
  Layer 2 — connection between today's delta and existing crystals
  Layer 3 — pipeline state with stale-crystal flag (backpressure)
  Layer 4 — one concrete "worth doing next" question (LLM)

The input collector + preflight + window resolution live in
:mod:`ovp_pipeline.digest_inputs` (BL-094).  This module is just the
**LLM-prompt + body composer** that consumes a :class:`DigestInputs`
snapshot and writes markdown.

Persistence + idempotency
-------------------------

* Output is one file per operator-local window:
  ``40-Resources/Generated/digests/YYYY-MM-DD-digest-daily.md``.
  Filename comes from the task dispatcher (uses ``_today_utc_date``);
  the operator-local window boundaries are recorded inside the
  frontmatter so a UTC/local mismatch is auditable, not silently lost.
* **Input-hash idempotency gate** — before calling the LLM, the
  handler computes a stable hash over (window boundaries + sorted
  stable id sets from every layer) and compares it to any prior
  digest at the same filename.  Same hash → skip the LLM call, emit
  ``digest_skipped_no_change``.  This kills the M20-era "5 redundant
  LLM passes in one day" thrash.
* **Honest no-data path** — when Layers 0 + 1 are empty AND Layer 3
  carries no actionable signal, the handler renders a 2-line
  acknowledgment rather than asking the LLM to fabricate insight
  from old crystals.

CLI shapes (unchanged from M20)
-------------------------------

* ``ovp-digest --enqueue-daily``  — drop ``DIGEST-daily.md`` into
                                     ``50-Inbox/02-Tasks/`` so the
                                     next dispatcher run picks it up
* ``ovp-digest --run-now``         — enqueue + dispatch synchronously
* ``ovp-digest --show-latest``     — print the path of the most
                                     recent digest file
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..context_loader import load_user_profile
from ..digest_config import DigestConfig, load_digest_config
from ..digest_inputs import (
    ConnectionLayer,
    DeltaLayer,
    DigestInputs,
    IntakeLayer,
    PipelineState,
    PreflightReport,
    collect_digest_inputs,
)
from ..event_emitter import emit
from .task_dispatch import (
    TaskContext,
    TaskResult,
    dispatch_task,
    register_handler,
)

logger = logging.getLogger(__name__)

DIGESTS_SUBDIR = "digests"
SCHEMA_VERSION = 2

# Audit event types emitted by this handler.  BL-097's
# ``/ops/digest-health`` reads them.
_EVENT_GENERATED = "digest_generated"
_EVENT_SKIPPED_NO_CHANGE = "digest_skipped_no_change"


# ---------------------------------------------------------------
# LLM prompt v2 — four-question structure
# ---------------------------------------------------------------

_DIGEST_SYSTEM_PROMPT_V2 = """\
You are OVP's daily-feedback handler.  The operator feeds articles
into a knowledge vault daily; you write a short morning brief that
acknowledges today's intake, names what new thinking landed, points
to how it connects to prior knowledge, and ends with ONE specific
question or action worth taking next.

Output structure (use these literal headings; omit a section when
its inputs are empty):

## Window's intake
One short paragraph naming what came in today: counts, topics,
representative titles.  Do not summarise individual articles.

## New thinking
2-3 bullets covering the most material evergreen changes today
(new + updated).  Frame each as one thought: *what changed*, plus
*how it sits in the cluster it joined*.  When ``change_summary``
is a fallback like ``v2: updated``, prefer the evergreen's title
+ cluster context over the fallback string.

## How this window connects
1-2 short paragraphs naming where today's new evergreens touch
existing crystals or contradictions.  Be specific about the
through-line — what makes them feel related, where they
strengthen prior thinking, where they challenge it.

## Pipeline state
One short paragraph (≤ 2 sentences) on backpressure: how many
evergreens are awaiting synthesis, when synthesis last ran, which
clusters have crossed the synthesis threshold (call out stale
crystals — clusters with an existing crystal that predates today's
inputs count as unsynthesized).

## Worth doing next
Exactly ONE concrete question or action.  Forms that work well:
- "3 articles on X converged today. Want to synthesize this cluster?"
- "Today's new evergreen Y challenges the open contradiction Z.
  Resolve?"
- "Cluster W has N unsynthesized evergreens. Run synthesis?"
- "No new intake today. Continue with prior tensions, or seed
  the vault?"

Hard rules:
- Stay under 280 words total.
- ACKNOWLEDGE today's intake even when the synthesis layer is
  quiet — the operator needs to know their work landed.
- Don't list article titles individually; surface them as a chip
  ("memory systems (7), agents (3), ops (2)").
- Don't invent topics that aren't in the input layers.
- Don't write a closing call-to-think — the "Worth doing next"
  section IS the call to action.
- When a section has zero data, omit the heading entirely.
"""


# ---------------------------------------------------------------
# Handler entry point
# ---------------------------------------------------------------


def handle_digest(ctx: TaskContext) -> TaskResult:
    """Compose one digest for ``ctx.pack`` against the current vault state."""
    config = load_digest_config(ctx.vault_dir)
    inputs = collect_digest_inputs(ctx.vault_dir, ctx.pack, config=config)
    new_hash = inputs.input_hash()

    # Idempotency gate — Stage 3 of the M23 plan.  Read any prior
    # digest at the expected filename; if its ``input_hash`` matches,
    # skip the LLM call and return the prior body verbatim so the
    # dispatcher's overwrite is a no-op rewrite.
    if config.skip_unchanged:
        prior_path = _expected_output_path(ctx.vault_dir)
        prior_hash, prior_body = _read_prior_digest(prior_path)
        if prior_body and prior_hash == new_hash:
            emit(
                ctx.vault_dir,
                "pipeline.jsonl",
                _EVENT_SKIPPED_NO_CHANGE,
                {
                    "input_hash": new_hash,
                    "window_start": inputs.window_start.isoformat(),
                    "window_end": inputs.window_end.isoformat(),
                    "pack": ctx.pack,
                },
                pack=ctx.pack,
            )
            return TaskResult(
                body_md=prior_body,
                subdir=DIGESTS_SUBDIR,
                metadata={
                    "input_hash": new_hash,
                    "skipped_llm": True,
                    "reason": "no_change",
                },
            )

    # No-data path: when nothing meaningful changed, render an
    # honest acknowledgment instead of asking the LLM to fabricate
    # insight from stale crystals.
    if _is_no_data(inputs):
        body_md = _render_no_data_body(inputs)
        emit(
            ctx.vault_dir,
            "pipeline.jsonl",
            _EVENT_GENERATED,
            _generated_audit_payload(inputs, new_hash, skipped_llm=True),
            pack=ctx.pack,
        )
        return TaskResult(
            body_md=body_md,
            subdir=DIGESTS_SUBDIR,
            metadata={
                "input_hash": new_hash,
                "skipped_llm": True,
                "reason": "no_data",
                **_layer_counts(inputs),
            },
        )

    # Real digest — call the LLM with the structured input layers.
    user_focus = load_user_profile(ctx.vault_dir)
    user_prompt = _build_digest_user_prompt_v2(inputs, user_focus)
    sys_prompt = ctx.compose_system_prompt(_DIGEST_SYSTEM_PROMPT_V2)

    composed = ctx.llm_client.call(
        sys_prompt, user_prompt, max_tokens=1200,
    )
    composed = (composed or "").strip()

    body_md = _render_body(inputs, composed, new_hash, ctx)
    emit(
        ctx.vault_dir,
        "pipeline.jsonl",
        _EVENT_GENERATED,
        _generated_audit_payload(inputs, new_hash, skipped_llm=False),
        pack=ctx.pack,
    )
    return TaskResult(
        body_md=body_md,
        subdir=DIGESTS_SUBDIR,
        metadata={
            "input_hash": new_hash,
            "skipped_llm": False,
            **_layer_counts(inputs),
        },
    )


register_handler("DIGEST", handle_digest, "Daily knowledge feedback brief.")


# ---------------------------------------------------------------
# No-data + idempotency helpers
# ---------------------------------------------------------------


def _is_no_data(inputs: DigestInputs) -> bool:
    """Layers 0 + 1 + 2 empty AND Layer 3 has no actionable signal."""
    has_intake = inputs.intake.intake_events_processed > 0
    has_delta = bool(inputs.delta.new_evergreens or inputs.delta.updated_evergreens)
    has_connections = bool(
        inputs.connections.connected_community_crystals
        or inputs.connections.touched_contradictions
    )
    layer3 = inputs.pipeline_state
    has_pipeline_signal = (
        layer3.unsynthesized_evergreens > 0
        or bool(layer3.clusters_at_threshold)
        or layer3.open_contradictions_count > 0
    )
    return not (has_intake or has_delta or has_connections or has_pipeline_signal)


def _expected_output_path(vault_dir: Path | str) -> Path:
    """Compose the filename the dispatcher will write today's digest to.

    Mirrors ``task_dispatch._resolve_output_path`` for the DIGEST
    handler.  The dispatcher uses UTC for the filename even though
    M23 reports window boundaries in operator-local time — this is
    intentional for backwards-compat with the existing
    ``ovp-digest --show-latest`` glob, and operator-local timestamps
    inside the frontmatter make any tz mismatch auditable.
    """
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        Path(vault_dir)
        / "40-Resources"
        / "Generated"
        / DIGESTS_SUBDIR
        / f"{date_str}-digest-daily.md"
    )


_INPUT_HASH_RE = re.compile(r"^input_hash:\s*(\S+)\s*$", re.MULTILINE)


def _read_prior_digest(path: Path) -> tuple[str, str]:
    """Return ``(input_hash, body)`` from any existing digest at
    ``path``; both empty when the file is missing or unparseable.

    Hash comes from the frontmatter; body is the full file contents
    (we return it unchanged so the dispatcher rewrites it byte-for-byte
    when we skip the LLM call).
    """
    if not path.is_file():
        return "", ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "", ""
    if not text.startswith("---\n"):
        return "", text
    fm_end = text.find("\n---", 4)
    if fm_end < 0:
        return "", text
    fm = text[4:fm_end]
    match = _INPUT_HASH_RE.search(fm)
    return (match.group(1) if match else ""), text


def _generated_audit_payload(
    inputs: DigestInputs, input_hash: str, *, skipped_llm: bool
) -> dict[str, Any]:
    return {
        "input_hash": input_hash,
        "skipped_llm": skipped_llm,
        "window_start": inputs.window_start.isoformat(),
        "window_end": inputs.window_end.isoformat(),
        "tz": inputs.tz_name,
        "pack": inputs.pack,
        "preflight_degraded": inputs.preflight.any_degraded(),
        **_layer_counts(inputs),
    }


def _layer_counts(inputs: DigestInputs) -> dict[str, int]:
    return {
        "layer0_events": inputs.intake.intake_events_processed,
        "layer1_new": len(inputs.delta.new_evergreens),
        "layer1_updated": len(inputs.delta.updated_evergreens),
        "layer2_connected_crystals": len(
            inputs.connections.connected_community_crystals
        ),
        "layer2_touched_contradictions": len(
            inputs.connections.touched_contradictions
        ),
        "layer3_unsynth": inputs.pipeline_state.unsynthesized_evergreens,
        "layer3_clusters_at_threshold": len(
            inputs.pipeline_state.clusters_at_threshold
        ),
        "layer3_open_contradictions": inputs.pipeline_state.open_contradictions_count,
    }


# ---------------------------------------------------------------
# User prompt — structured input for the LLM
# ---------------------------------------------------------------


def _build_digest_user_prompt_v2(inputs: DigestInputs, user_focus: str) -> str:
    parts: list[str] = []
    if user_focus.strip():
        parts.append("Operator's current focus (from USER.md):\n")
        parts.append(user_focus.strip() + "\n")

    parts.append("\n# Window")
    parts.append(
        f"From {inputs.window_start.isoformat()} to "
        f"{inputs.window_end.isoformat()} ({inputs.tz_name})"
    )

    parts.append("\n# Layer 0 — Today's intake")
    parts.append(_render_layer0_for_prompt(inputs.intake))

    parts.append("\n# Layer 1 — Evergreen delta")
    parts.append(_render_layer1_for_prompt(inputs.delta, inputs.preflight))

    parts.append("\n# Layer 2 — Connections to existing knowledge")
    parts.append(_render_layer2_for_prompt(inputs.connections))

    parts.append("\n# Layer 3 — Pipeline state")
    parts.append(_render_layer3_for_prompt(inputs.pipeline_state))

    parts.append("\nNow compose the brief in the four-section format.")
    return "\n".join(parts)


def _render_layer0_for_prompt(layer: IntakeLayer) -> str:
    if layer.intake_events_processed == 0:
        return "(no intake events in this window)"
    chips = ", ".join(f"{k} ({n})" for k, n in layer.topic_distribution) or "(no topic distribution)"
    samples = "; ".join(layer.representative_samples) or "(no titles)"
    authors = ", ".join(layer.authors_or_sources) or "(no attributed sources)"
    return (
        f"- Events processed: {layer.intake_events_processed}\n"
        f"- Topic distribution: {chips}\n"
        f"- Authors / sources: {authors}\n"
        f"- Representative titles: {samples}"
    )


def _render_layer1_for_prompt(layer: DeltaLayer, preflight: PreflightReport) -> str:
    if not (layer.new_evergreens or layer.updated_evergreens):
        return "(no new or updated evergreens in this window)"
    lines: list[str] = []
    if preflight.change_note_quality != "ok":
        lines.append(
            "(NOTE: change_note quality is degraded — change_summary "
            "values are generic fallbacks; prefer title + cluster for prose.)"
        )
    if layer.new_evergreens:
        lines.append("New evergreens:")
        for d in layer.new_evergreens:
            cluster = f" (cluster: {d.cluster_id})" if d.cluster_id else ""
            lines.append(
                f"- **{d.title}** [v{d.version} {d.change_type}]{cluster}: {d.change_summary}"
            )
    if layer.updated_evergreens:
        lines.append("Updated evergreens:")
        for d in layer.updated_evergreens:
            cluster = f" (cluster: {d.cluster_id})" if d.cluster_id else ""
            lines.append(
                f"- **{d.title}** [v{d.version} {d.change_type}]{cluster}: {d.change_summary}"
            )
    return "\n".join(lines)


def _render_layer2_for_prompt(layer: ConnectionLayer) -> str:
    if not (
        layer.connected_community_crystals
        or layer.touched_contradictions
        or layer.recent_top_crystals
    ):
        return "(no detected connections to existing crystals or contradictions)"
    lines: list[str] = []
    if layer.connected_community_crystals:
        lines.append("Today's evergreens joined these existing communities:")
        for cluster_id, label in layer.connected_community_crystals:
            lines.append(f"- {label or cluster_id} (cluster_id={cluster_id})")
    if layer.touched_contradictions:
        lines.append("Today's evergreens overlap with these open contradictions:")
        for cid, subject in layer.touched_contradictions:
            lines.append(f"- {subject or cid} (contradiction_id={cid})")
    if layer.recent_top_crystals:
        lines.append("Top-scoring crystals in the vault (context, not delta):")
        for cid, kind, score in layer.recent_top_crystals:
            lines.append(f"- {kind}/{cid} score={score:.2f}")
    return "\n".join(lines)


def _render_layer3_for_prompt(layer: PipelineState) -> str:
    parts: list[str] = []
    if layer.unsynthesized_evergreens:
        parts.append(
            f"{layer.unsynthesized_evergreens} evergreens awaiting (or "
            "needing re-) synthesis."
        )
    if layer.last_synthesis_at:
        parts.append(f"Last synthesis at: {layer.last_synthesis_at}")
    else:
        parts.append("No synthesis has ever run.")
    if layer.clusters_at_threshold:
        parts.append("Clusters at or above the synthesis threshold:")
        for cid, label, count, stale in layer.clusters_at_threshold:
            flag = " (STALE crystal)" if stale else ""
            parts.append(f"- {label or cid}: {count} evergreens{flag}")
    if layer.open_contradictions_count:
        parts.append(
            f"{layer.open_contradictions_count} open contradiction"
            f"{'s' if layer.open_contradictions_count != 1 else ''} unresolved."
        )
    return "\n".join(parts) if parts else "(no notable pipeline state)"


# ---------------------------------------------------------------
# Body rendering
# ---------------------------------------------------------------


def _render_body(
    inputs: DigestInputs,
    composed: str,
    input_hash: str,
    ctx: TaskContext,
) -> str:
    frontmatter = _build_frontmatter(inputs, input_hash)
    date_label = inputs.window_end.date().isoformat()
    sources = _build_sources_section(inputs)
    footer = _build_footer(inputs, ctx)
    return (
        frontmatter
        + f"# Daily Knowledge Feedback — {date_label}\n\n"
        + composed
        + "\n"
        + sources
        + footer
    )


def _render_no_data_body(inputs: DigestInputs) -> str:
    """Honest, LLM-free body for windows with no real signal.

    Surfaces (1) the window we looked at, (2) the preflight state so
    operators can see WHY there's nothing — table missing vs empty
    vs synthesis lag — and (3) a concrete next step.
    """
    frontmatter = _build_frontmatter(inputs, inputs.input_hash())
    date_label = inputs.window_end.date().isoformat()
    preflight = inputs.preflight
    diag: list[str] = []
    if preflight.evergreen_revisions_table != "ok":
        diag.append(
            "- `evergreen_revisions` table is missing or unreadable. "
            "Run `ovp-knowledge-index` to rebuild it."
        )
    elif preflight.evergreen_revisions_recent != "ok":
        diag.append(
            "- No evergreen revisions in the last 7 days. "
            "The absorb pipeline hasn't produced anything new."
        )
    if preflight.community_crystals != "ok":
        diag.append(
            "- No community crystals yet. "
            "Run `ovp-synthesize-community-crystals` once enough evergreens accumulate."
        )
    diag_block = ("\n".join(diag) + "\n") if diag else ""
    suggestion = (
        "Drop a few articles into `50-Inbox/01-Raw/` and run the "
        "ingestion pipeline; tomorrow's digest will reflect them."
    )
    # CodeRabbit: distinguish "audit didn't run" from "audit ran +
    # no events".  The original message conflated both as
    # "No new intake in this window.", which is misleading when
    # ``ovp-knowledge-index`` simply hasn't built the audit_events
    # table yet.
    if preflight.audit_events_layer0 == "ok":
        intake_line = "No new intake in this window."
    else:
        intake_line = (
            "Intake data unavailable — the audit_events table is "
            "missing or empty.  Run `ovp-knowledge-index` to rebuild."
        )
    body = (
        frontmatter
        + f"# Daily Knowledge Feedback — {date_label}\n\n"
        "## Window's intake\n\n"
        + intake_line
        + "\n\n"
        + (f"## Pipeline state\n\n{diag_block}\n" if diag else "")
        + "## Worth doing next\n\n"
        + suggestion
        + "\n"
        + _build_footer(inputs, None)
    )
    return body


def _build_frontmatter(inputs: DigestInputs, input_hash: str) -> str:
    preflight = inputs.preflight
    return (
        "---\n"
        "type: digest\n"
        f"schema_version: {SCHEMA_VERSION}\n"
        f"generated_at: {inputs.window_end.isoformat()}\n"
        f"window_start: {inputs.window_start.isoformat()}\n"
        f"window_end: {inputs.window_end.isoformat()}\n"
        f"tz: {inputs.tz_name}\n"
        f"pack: {inputs.pack}\n"
        f"input_hash: {input_hash}\n"
        "preflight:\n"
        f"  evergreen_revisions_table: {preflight.evergreen_revisions_table}\n"
        f"  evergreen_revisions_recent: {preflight.evergreen_revisions_recent}\n"
        f"  audit_events_layer0: {preflight.audit_events_layer0}\n"
        f"  change_note_quality: {preflight.change_note_quality}\n"
        f"  graph_clusters: {preflight.graph_clusters}\n"
        f"  community_crystals: {preflight.community_crystals}\n"
        "---\n\n"
    )


def _build_sources_section(inputs: DigestInputs) -> str:
    """Surface the underlying evergreens + crystals the digest read.

    Mirrors the M20 Sources block so operators get clickable
    wikilinks from inside Obsidian.  Empty when no rows touched.
    """
    new_objects = [d.object_id for d in inputs.delta.new_evergreens]
    updated_objects = [d.object_id for d in inputs.delta.updated_evergreens]
    connected_clusters = [c[0] for c in inputs.connections.connected_community_crystals]
    contradiction_ids = [c[0] for c in inputs.connections.touched_contradictions]
    if not (new_objects or updated_objects or connected_clusters or contradiction_ids):
        return ""
    parts: list[str] = ["\n## Sources\n"]
    if new_objects or updated_objects:
        parts.append("**Evergreens this window touched**")
        seen: set[str] = set()
        for oid in [*new_objects, *updated_objects]:
            slug = _safe_wikilink(oid)
            if slug and slug not in seen:
                parts.append(f"- [[{slug}]]")
                seen.add(slug)
        parts.append("")
    if connected_clusters or contradiction_ids:
        parts.append("**Crystals + contradictions connected**")
        for cid in connected_clusters:
            label = _safe_wikilink(cid)
            if label:
                parts.append(f"- [[{label}|◆ {label}]]")
        for cid in contradiction_ids:
            label = _safe_wikilink(cid)
            if label:
                parts.append(f"- [[{label}|⚠ {label}]]")
        parts.append("")
    return "\n".join(parts) + "\n"


def _safe_wikilink(value: Any) -> str:
    """Strip wikilink-breaking characters from a slug."""
    return (
        str(value or "")
        .replace("[", " ")
        .replace("]", " ")
        .replace("|", " ")
        .replace("\n", " ")
        .strip()
    )


def _build_footer(inputs: DigestInputs, ctx: TaskContext | None) -> str:
    counts = _layer_counts(inputs)
    when = inputs.window_end.date().isoformat()
    task_ref = (
        f"`50-Inbox/02-Tasks/{ctx.task_path.name}`"
        if ctx is not None
        else "`ovp-digest --run-now`"
    )
    return (
        "\n---\n\n"
        f"*Generated by DIGEST handler on {when} from {task_ref}. "
        f"Layer 0: {counts['layer0_events']} events · "
        f"Layer 1: {counts['layer1_new']} new + {counts['layer1_updated']} updated · "
        f"Layer 2: {counts['layer2_connected_crystals']} crystals + "
        f"{counts['layer2_touched_contradictions']} contradictions · "
        f"Layer 3: {counts['layer3_unsynth']} unsynthesized, "
        f"{counts['layer3_open_contradictions']} open.*\n"
    )


# ---------------------------------------------------------------
# ovp-digest CLI (unchanged from M20)
# ---------------------------------------------------------------


def _enqueue_daily(vault_dir: Path) -> Path:
    """Drop ``DIGEST-daily.md`` into ``50-Inbox/02-Tasks/`` so the
    next dispatcher run picks it up.  Idempotent — if today's task
    file already exists, return its path without overwriting."""
    folder = vault_dir / "50-Inbox" / "02-Tasks"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "DIGEST-daily.md"
    if target.exists():
        return target
    target.write_text(
        "<!-- Auto-generated by `ovp-digest --enqueue-daily`. "
        "The handler ignores this body — vault state is pulled "
        "directly from knowledge.db. -->\n",
        encoding="utf-8",
    )
    return target


def _latest_digest(vault_dir: Path) -> Path | None:
    folder = vault_dir / "40-Resources" / "Generated" / DIGESTS_SUBDIR
    if not folder.exists():
        return None
    candidates = sorted(folder.glob("*.md"))
    return candidates[-1] if candidates else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Daily digest helpers (M23 / BL-095).",
    )
    parser.add_argument("--vault-dir", required=True, type=Path)
    parser.add_argument(
        "--enqueue-daily", action="store_true",
        help="Create DIGEST-daily.md in 50-Inbox/02-Tasks/.",
    )
    parser.add_argument(
        "--run-now", action="store_true",
        help="Enqueue then dispatch synchronously.",
    )
    parser.add_argument(
        "--show-latest", action="store_true",
        help="Print the path of the most recent digest.",
    )
    parser.add_argument(
        "--pack", default="research-tech",
        help="Pack name (default: research-tech).",
    )
    args = parser.parse_args(argv)

    if not any((args.enqueue_daily, args.run_now, args.show_latest)):
        parser.error("pass --enqueue-daily, --run-now, or --show-latest")

    vault = args.vault_dir.expanduser().resolve()
    if not vault.exists():
        print(f"error: vault dir does not exist: {vault}", file=sys.stderr)
        return 2

    if args.show_latest:
        latest = _latest_digest(vault)
        if latest is None:
            print("(no digests yet)")
            return 1
        print(latest)
        return 0

    if args.enqueue_daily or args.run_now:
        task = _enqueue_daily(vault)
        if args.run_now:
            try:
                output = dispatch_task(vault, task, pack=args.pack)
            except Exception as exc:  # noqa: BLE001
                print(f"dispatch failed: {exc}", file=sys.stderr)
                return 3
            print(output)
        else:
            print(task)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
