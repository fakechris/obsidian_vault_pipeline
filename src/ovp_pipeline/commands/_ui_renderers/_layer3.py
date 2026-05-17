# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
from __future__ import annotations

from ._constants import *
from ._layer0 import *
from ._layer1 import *
from ._layer2 import *




def _linkify_related_knowledge_section(
    vault_dir: Path, markdown: str, *, requested_pack: str = ""
) -> str:
    output_lines: list[str] = []
    in_related = False

    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            in_related = stripped.lstrip("#").strip() == "关联知识"
            output_lines.append(line)
            continue
        if in_related and re.match(r"^- [^\[][^—]+ — ", stripped):
            concept, sep, remainder = stripped[2:].partition(" — ")
            concept = concept.strip()
            resolved = _lookup_wikilink_target(vault_dir, concept, requested_pack=requested_pack)
            href = resolved[0] if resolved else _search_href(concept, requested_pack)
            emoji = "🔍" if _is_search_href(href) else "🎯"
            output_lines.append(f"- [{emoji} {concept}]({href}) — {remainder}")
            continue
        output_lines.append(line)

    return "\n".join(output_lines)



def _render_actions_page(payload: dict) -> str:
    query = payload.get("query", "")
    selected_status = payload.get("status", "")
    requested_pack = payload.get("requested_pack", "")
    next_path = _shell_href("/ops/actions", requested_pack)
    governance_contract_card = _render_governance_contract_card(payload)
    options = ["", "queued", "running", "succeeded", "failed", "blocked", "dismissed", "obsolete"]
    option_html = "".join(
        f"<option value='{escape(option)}' {'selected' if option == selected_status else ''}>"
        f"{escape(option or 'all statuses')}</option>"
        for option in options
    )
    items = (
        "".join(
            "<li>"
            f"<span class='pill'>{escape(str(item['status']))}</span> "
            f"<span class='pill'>{escape(str(item['action_kind']))}</span> "
            + (
                " <span class='pill'>safe</span>"
                if item.get("safe_to_run")
                else " <span class='pill'>manual</span>"
            )
            + " "
            + f"{escape(str(item['title']))}"
            + (
                f"<div class='muted'>Target: {escape(str(item['target_ref']))}</div>"
                if item.get("target_ref")
                else ""
            )
            + (
                f"<div class='muted'>Created at {_ts(item['created_at'])}</div>"
                if item.get("created_at")
                else ""
            )
            + (
                f"<div class='muted'>Retry count: {int(item.get('retry_count') or 0)}</div>"
                if item.get("retry_count") is not None
                else ""
            )
            + (
                f"<div class='muted'>Failure bucket: {escape(str(item['failure_bucket']))}</div>"
                if item.get("failure_bucket")
                else ""
            )
            + (
                f"<div class='muted'>Impact: {escape(str(item['impact_summary']['impact_label']))}</div>"
                if item.get("impact_summary", {}).get("impact_label")
                else ""
            )
            + (
                f"<div class='muted'>{escape(str(item['impact_summary']['impact_detail']))}</div>"
                if item.get("impact_summary", {}).get("impact_detail")
                else ""
            )
            + (
                f"<div class='muted'>Processor: {escape(str(item['processor_mode']))}</div>"
                if item.get("processor_mode")
                else ""
            )
            + (
                f"<div class='muted'>Resolver: {escape(str(item['resolution_kind']))}</div>"
                if item.get("resolution_kind")
                else ""
            )
            + (
                f"<div class='muted'>Dispatch: {escape(str(item['dispatch_mode']))}</div>"
                if item.get("dispatch_mode")
                else ""
            )
            + (
                f"<div class='muted'>Rule: {escape(str(item['resolver_rule_name']))}</div>"
                if item.get("resolver_rule_name")
                else ""
            )
            + (
                f"<div class='muted'>Governance contract: {escape(str(item['governance_provider_name']))} · {escape(str(item['governance_provider_pack']))}</div>"
                if item.get("governance_provider_name") or item.get("governance_provider_pack")
                else ""
            )
            + (
                f"<div class='muted'>Handler contract: {escape(str(item['handler_provider_name']))} · {escape(str(item['handler_provider_pack']))}</div>"
                if item.get("handler_provider_name") or item.get("handler_provider_pack")
                else ""
            )
            + (
                f"<div class='muted'>Processor contract: {escape(str(item['processor_provider_name']))} · {escape(str(item['processor_provider_pack']))}</div>"
                if item.get("processor_provider_name") or item.get("processor_provider_pack")
                else ""
            )
            + (
                f"<div class='muted'>Source signal: {'active' if item.get('source_signal_active') else 'inactive'}</div>"
                if "source_signal_active" in item
                else ""
            )
            + (
                f"<div class='muted'>Precondition: {escape(str(item['precondition_status']))}</div>"
                if item.get("precondition_status")
                else ""
            )
            + (
                f"<div class='muted'>Blocked reason: {escape(str(item['blocked_reason']))}</div>"
                if item.get("blocked_reason")
                else ""
            )
            + (
                f"<div class='muted'>Obsolete reason: {escape(str(item['obsolete_reason']))}</div>"
                if item.get("obsolete_reason")
                else ""
            )
            + (
                f"<div class='muted'>Last result: {escape(str(item['last_result_summary']))}</div>"
                if item.get("last_result_summary")
                else ""
            )
            + (
                f"<div class='muted'>Inputs: {escape(', '.join(str(value) for value in item['processor_inputs']))}</div>"
                if item.get("processor_inputs")
                else ""
            )
            + (
                f"<div class='muted'>Outputs: {escape(', '.join(str(value) for value in item['processor_outputs']))}</div>"
                if item.get("processor_outputs")
                else ""
            )
            + (
                f"<div class='muted'>Quality hooks: {escape(', '.join(str(value) for value in item['processor_quality_hooks']))}</div>"
                if item.get("processor_quality_hooks")
                else ""
            )
            + (
                "<form method='post' action='/ops/actions/retry' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
                + f"<input type='hidden' name='action_id' value='{escape(str(item['action_id']))}' />"
                + f"<input type='hidden' name='next' value='{escape(next_path)}' />"
                + "<button type='submit'>Retry</button>"
                + "</form>"
                if item.get("status") in {"failed", "blocked", "obsolete"}
                else ""
            )
            + (
                "<form method='post' action='/ops/actions/dismiss' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
                + f"<input type='hidden' name='action_id' value='{escape(str(item['action_id']))}' />"
                + f"<input type='hidden' name='next' value='{escape(next_path)}' />"
                + "<button type='submit'>Dismiss</button>"
                + "</form>"
                if item.get("status") in {"queued", "failed", "blocked", "obsolete", "running"}
                else ""
            )
            + "</li>"
            for item in payload["items"]
        )
        or "<li class='muted'>No queued actions yet.</li>"
    )
    return _layout(
        "Action Queue",
        "".join(
            [
                "<h1>Action Queue</h1>",
                _render_page_help(
                    "Action queue",
                    what=(
                        "Commands the workflow worker should run.  Items get"
                        " here from <strong>/ops/queue/signals</strong> "
                        "(<em>Queue action</em>), periodic pipeline jobs (e.g."
                        " backfill cron), or manual enqueue via the CLI."
                    ),
                    can=(
                        "<strong>Run next</strong> dequeues a single item to"
                        " the action worker.  <strong>Run batch</strong>"
                        " processes up to 5 in one pass.  <strong>Retry</strong>"
                        " requeues a failed action; <strong>Dismiss</strong>"
                        " removes it from the queue without running."
                    ),
                    effect=(
                        "Run/Retry actually executes the queued command (may"
                        " mutate the truth store, the vault, or external"
                        " services depending on the action).  Dismiss only"
                        " marks the row as dismissed; nothing else changes."
                    ),
                ),
                "<p class='muted'>Asynchronous queue consumption is opt-in. Run <code>python -m ovp_pipeline.commands.run_actions --vault-dir &lt;vault&gt; --loop</code> or start the UI with <code>--with-action-worker</code> to spawn a detached worker process.</p>",
                "<form method='post' action='/ops/actions/run-next' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='hidden' name='next' value='{escape(next_path)}' />",
                "<button type='submit'>Run next queued action</button>",
                "</form>",
                "<form method='post' action='/ops/actions/run-batch' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
                "<input type='hidden' name='limit' value='5' />",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='hidden' name='next' value='{escape(next_path)}' />",
                "<button type='submit'>Run 5 queued actions</button>",
                "</form>",
                "<form method='post' action='/ops/actions/run-batch' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
                "<input type='hidden' name='limit' value='5' />",
                "<input type='hidden' name='safe_only' value='1' />",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='hidden' name='next' value='{escape(next_path)}' />",
                "<button type='submit'>Run 5 safe queued actions</button>",
                "</form>",
                "<form method='get' action='/ops/actions' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Search actions' />",
                f"<select name='status'>{option_html}</select>",
                "<button type='submit'>Filter</button>",
                "</form>",
                (
                    f"<p class='muted'>{payload['count']} actions in the current execution surface. "
                    f"{payload.get('impact_counts', {}).get('productive', 0)} productive, "
                    f"{payload.get('impact_counts', {}).get('waiting', 0)} waiting, "
                    f"{payload.get('impact_counts', {}).get('failed', 0) + payload.get('impact_counts', {}).get('stalled', 0)} failed/stalled. "
                    f"{payload.get('queued_safe_count', 0)} queued safe actions. {payload.get('failed_count', 0)} failed actions.</p>"
                ),
                governance_contract_card,
                f"<section class='card'><ul class='list-tight'>{items}</ul></section>",
            ]
        ),
        requested_pack=requested_pack,
    )



def _render_candidates_page(payload: dict) -> str:
    query = str(payload.get("query") or "")
    requested_pack = str(payload.get("requested_pack") or "")
    candidate_warning = str(payload.get("candidate_warning") or "")
    operator_rail = _render_operator_rail(payload)
    status_counts = " ".join(
        f"<span class='pill'>{escape(str(status))}: {escape(str(count))}</span>"
        for status, count in (payload.get("status_counts") or {}).items()
    )
    warning_card = (
        f"<section class='card warning'><h2>Review Warning</h2><p>{escape(candidate_warning)}</p></section>"
        if candidate_warning
        else ""
    )
    pagination = _render_candidates_pagination(payload)
    return _layout(
        "Candidate Workbench",
        "".join(
            [
                "<h1>Candidate Workbench</h1>",
                _render_page_help(
                    "Concept candidates",
                    what=(
                        "Concept slugs the absorb pipeline thinks deserve their"
                        " own Evergreen note.  They are still proposals — only a"
                        " <strong>Promote</strong> turns one into a canonical"
                        " object."
                    ),
                    can=(
                        "<strong>Promote</strong> creates an Evergreen note from"
                        " the candidate.  <strong>Merge</strong> rewrites links"
                        " into an existing object (target slug required)."
                        "  <strong>Reject</strong> drops the candidate as"
                        " spurious or duplicate."
                    ),
                    effect=(
                        "Promote and Merge mutate the truth store (objects,"
                        " relations) and trigger a re-index; Reject only marks"
                        " the candidate as resolved.  All three are reversible"
                        " by re-running the absorb step on the source."
                    ),
                ),
                "<p class='muted'>Review registry candidates before they become canonical Evergreen objects. "
                "Promote creates an active note, merge rewrites candidate links into an existing object, "
                "and reject removes the pending candidate artifact.</p>",
                "<form method='get' action='/ops/queue/concepts' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter candidates' />",
                f"<input type='hidden' name='limit' value='{escape(str(payload.get('limit') or DEFAULT_CANDIDATE_BROWSER_LIMIT))}' />",
                "<button type='submit'>Search</button>",
                "</form>",
                f"<p class='muted'>{escape(str(payload.get('count') or 0))} candidate(s) in view.</p>",
                pagination,
                f"<section class='card'><h2>Status</h2><div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>{status_counts}</div></section>",
                operator_rail,
                warning_card,
                f"<section class='card'><h2>Review Queue</h2>{_render_candidate_items(payload)}</section>",
            ]
        ),
        requested_pack=requested_pack,
    )



def _render_contradictions_page(payload: dict) -> str:
    status = payload.get("status", "")
    query = payload.get("query", "")
    requested_pack = payload.get("requested_pack", "")
    next_path = "/ops/contradictions" + (
        f"?pack={quote(requested_pack, safe='')}" if requested_pack else ""
    )
    assembly_contract_card = _render_assembly_contract_card(payload)
    operator_rail_card = _render_operator_rail(payload)
    lead_sections, remaining_sections = _split_lead_compiled_sections(
        payload.get("compiled_sections", [])
    )
    detection_notes = "".join(f"<li>{escape(note)}</li>" for note in payload["detection_notes"])
    scope_summary = payload["scope_summary"]
    scope_summary_items = (
        f"<li>Items: {scope_summary['item_count']}</li>"
        f"<li>Objects in scope: {scope_summary['object_count']}</li>"
        f"<li>Source notes in scope: {scope_summary['source_note_count']}</li>"
    )
    detection_contract = payload["detection_contract"]
    detection_contract_items = (
        f"<li>Model: {escape(detection_contract['model'])}</li>"
        + f"<li>Confidence: {escape(detection_contract['confidence'])}</li>"
        + f"<li>Polarity semantics: {escape(str(detection_contract.get('polarity_semantics') or ''))}</li>"
        + f"<li>Evidence semantics: {escape(str(detection_contract.get('evidence_semantics') or ''))}</li>"
        + "".join(
            f"<li>Status bucket {escape(str(bucket))}: {count}</li>"
            for bucket, count in detection_contract["status_buckets"].items()
        )
        + "".join(
            f"<li>Status {escape(str(status_name))}: {escape(text)}</li>"
            for status_name, text in detection_contract["status_explanations"].items()
        )
    )
    section_nav = "".join(
        f'<a href="{escape(str(item["href"]))}">{escape(str(item["label"]))}</a>'
        for item in payload.get("section_nav", [])
    )
    items = (
        "".join(
            "<li>"
            + (
                f"<label><input type='checkbox' form='contradiction-batch-form' name='contradiction_id' value='{escape(item['contradiction_id'])}' /> batch</label> "
                if item["status"] == "open"
                else ""
            )
            + f"<span class='pill'>{escape(item['status'])}</span>{escape(item['subject_key'])}"
            + f" <span class='muted'>[{escape(item['detection_model'])} / {escape(item['detection_confidence'])} / {escape(item['status_bucket'])}]</span>"
            + f"<div class='muted'>Status Meaning: {escape(item['status_explanation'])}</div>"
            + (
                "<div class='muted'>Scope Summary: "
                + f"{item['scope_summary']['object_count']} objects, "
                + f"{item['scope_summary']['positive_claim_count']} positive claims, "
                + f"{item['scope_summary']['negative_claim_count']} negative claims, "
                + f"{item['scope_summary']['source_note_count']} source notes"
                + "</div>"
            )
            + (
                " <span class='muted'>"
                + ", ".join(
                    f'<a href="{escape(link["path"])}">{escape(item["object_titles"].get(link["object_id"], link["object_id"]))}</a>'
                    for link in item["object_links"]
                )
                + "</span>"
                if item["object_links"]
                else ""
            )
            + f"<div class='muted'>Source Notes: {_render_named_note_links(item['provenance']['source_notes'], requested_pack=requested_pack)}</div>"
            + f"<div class='muted'>Atlas / MOC: {_render_named_note_links(item['provenance']['mocs'], requested_pack=requested_pack)}</div>"
            + (
                "<details><summary>Ranked Evidence</summary><ol class='list-tight'>"
                + "".join(
                    f"<li>#{evidence['rank']} {escape(evidence['polarity'])}: {escape(evidence['quote_text'])} "
                    + f"<span class='muted'>({escape(evidence['object_title'])} / {escape(evidence['source_slug'])} / {escape(evidence['evidence_kind'])})</span></li>"
                    for evidence in item["ranked_evidence"]
                )
                + "</ol></details>"
                if item["ranked_evidence"]
                else ""
            )
            + (
                "<details><summary>Claim Evidence</summary><ul class='list-tight'>"
                + "".join(
                    "<li>Positive: "
                    + f"{escape(claim['claim_text'])} <span class='muted'>({escape(claim['object_title'])})</span>"
                    + (
                        "<ul class='list-tight'>"
                        + "".join(
                            f"<li>{escape(evidence['evidence_kind'])}: {escape(evidence['quote_text'])} <span class='muted'>({escape(evidence['source_slug'])})</span></li>"
                            for evidence in claim["evidence"]
                        )
                        + "</ul>"
                        if claim["evidence"]
                        else ""
                    )
                    + "</li>"
                    for claim in item["positive_claims"]
                )
                + "".join(
                    "<li>Negative: "
                    + f"{escape(claim['claim_text'])} <span class='muted'>({escape(claim['object_title'])})</span>"
                    + (
                        "<ul class='list-tight'>"
                        + "".join(
                            f"<li>{escape(evidence['evidence_kind'])}: {escape(evidence['quote_text'])} <span class='muted'>({escape(evidence['source_slug'])})</span></li>"
                            for evidence in claim["evidence"]
                        )
                        + "</ul>"
                        if claim["evidence"]
                        else ""
                    )
                    + "</li>"
                    for claim in item["negative_claims"]
                )
                + "</ul></details>"
            )
            + f"<div class='muted'>Tension Summary: {escape(str(item.get('tension_summary') or ''))}</div>"
            + (
                "<details><summary>Review History</summary><ul class='list-tight'>"
                + "".join(
                    f"<li>{_ts(history['timestamp'])} <span class='pill'>{escape(str(history['event_type']))}</span>"
                    + (
                        f"<div class='muted'>Status: {escape(str(history['status']))}</div>"
                        if history.get("status")
                        else ""
                    )
                    + (
                        f"<div class='muted'>Note: {escape(str(history['note']))}</div>"
                        if history.get("note")
                        else ""
                    )
                    + "</li>"
                    for history in item["review_history"]
                )
                + "</ul></details>"
                if item["review_history"]
                else ""
            )
            + (
                f"<div class='muted'>Resolution Note: {escape(item['resolution_note'])}</div>"
                if item.get("resolution_note")
                else ""
            )
            + (
                f"<div class='muted'>Resolved At: {escape(item['resolved_at'])}</div>"
                if item.get("resolved_at")
                else ""
            )
            + (
                "<form method='post' action='/ops/contradictions/resolve' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
                f"<input type='hidden' name='contradiction_id' value='{escape(item['contradiction_id'])}' />"
                f"<input type='hidden' name='next' value='{escape(next_path)}' />"
                "<select name='status'>"
                "<option value='resolved_keep_positive'>resolved_keep_positive</option>"
                "<option value='resolved_keep_negative'>resolved_keep_negative</option>"
                "<option value='dismissed'>dismissed</option>"
                "<option value='needs_human'>needs_human</option>"
                "</select>"
                "<input type='text' name='note' placeholder='Resolution note' />"
                "<label><input type='checkbox' name='rebuild_summaries' value='1' /> rebuild summaries</label>"
                "<button type='submit'>Resolve</button>"
                "</form>"
                if item["status"] == "open"
                else ""
            )
            + "</li>"
            for item in payload["items"]
        )
        or f"<li>{escape(payload['empty_state'])}</li>"
    )
    return _layout(
        "Contradictions",
        "".join(
            [
                "<h1>Contradictions</h1>",
                _render_page_help(
                    "Contradictions",
                    what=(
                        "Pairs of claims the contradiction detector flagged"
                        " as semantically incompatible.  Each row points at"
                        " a positive-claim set and a negative-claim set; only"
                        " a human can pick which side is canonical."
                    ),
                    can=(
                        "<strong>resolved_keep_positive</strong> marks the"
                        " positive claims as canonical and supersedes the"
                        " negative side.  <strong>resolved_keep_negative</strong>"
                        " is the mirror image."
                        "  <strong>dismissed</strong> closes the row as a"
                        " false alarm without changing either side."
                        "  <strong>needs_human</strong> leaves it open for"
                        " deeper review."
                    ),
                    effect=(
                        "Keep-positive / Keep-negative tag the rejected claims"
                        " as superseded and trigger a re-compile of any"
                        " downstream summaries that quoted them."
                        "  Dismissed only updates the contradiction row."
                        "  ‘Rebuild summaries’ kicks the compile queue once."
                    ),
                ),
                "<form method='get' action='/ops/queue/contradictions'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                "<select name='status'>",
                f"<option value=''{' selected' if not status else ''}>all</option>",
                f"<option value='open'{' selected' if status == 'open' else ''}>open</option>",
                f"<option value='resolved'{' selected' if status == 'resolved' else ''}>resolved</option>",
                "</select> ",
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter contradictions' /> ",
                "<button type='submit'>Filter</button>",
                "</form>",
                f"<p class='muted'>{payload['count']} records, {payload['open_count']} open.",
                f" Pack scope: {escape(requested_pack)}." if requested_pack else "",
                "</p>",
                _render_compiled_sections(lead_sections),
                operator_rail_card,
                assembly_contract_card,
                f"<nav class='subnav'>{section_nav}</nav>" if section_nav else "",
                _render_compiled_sections(remaining_sections),
                f"<section class='card'><h2>Detection Notes</h2><ul class='list-tight'>{detection_notes}</ul></section>",
                "<section class='card'>",
                "<h2>Batch Resolve</h2>",
                "<form id='contradiction-batch-form' method='post' action='/ops/contradictions/resolve' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
                f"<input type='hidden' name='next' value='{escape(next_path)}' />",
                "<select name='status'>",
                "<option value='resolved_keep_positive'>resolved_keep_positive</option>",
                "<option value='resolved_keep_negative'>resolved_keep_negative</option>",
                "<option value='dismissed'>dismissed</option>",
                "<option value='needs_human'>needs_human</option>",
                "</select>",
                "<input type='text' name='note' placeholder='Resolution note for selected rows' />",
                "<label><input type='checkbox' name='rebuild_summaries' value='1' /> rebuild summaries</label>",
                "<button type='submit'>Resolve Selected</button>",
                "</form>",
                "</section>",
                f"<section class='card'><h2>Scope Summary</h2><ul class='list-tight'>{scope_summary_items}</ul></section>",
                f"<section class='card'><h2>Detection Contract</h2><ul class='list-tight'>{detection_contract_items}</ul></section>",
                f"<section class='card'><ul class='list-tight'>{items}</ul></section>",
            ]
        ),
        requested_pack=requested_pack,
    )



def _render_dashboard(payload: dict) -> str:
    requested_pack = payload.get("requested_pack", "")
    runtime_card = _render_runtime_card(payload.get("runtime"))
    run_history_card = _render_run_history_card(payload.get("runtime"))
    runtime_state_card = _render_runtime_state_card(payload.get("runtime_state"))
    research_overview = payload.get("research_overview", {})
    research_overview_supported = research_overview.get("status") == "supported"
    orientation = payload.get("orientation", {})
    signals_surface_contract = _render_surface_contract_card(payload["signals"])
    production_surface_contract = _render_surface_contract_card(payload["production"])
    orientation_assembly_contract = (
        _render_assembly_contract_card(orientation) if isinstance(orientation, dict) else ""
    )
    orientation_governance_contract = (
        _render_governance_contract_card(orientation) if isinstance(orientation, dict) else ""
    )
    entry_sections_html = _render_compiled_sections(payload.get("entry_sections", []))
    workflow_groups_html = _render_workflow_groups(payload.get("workflow_groups", []))
    object_items = (
        "".join(
            f'<li><a href="{escape(_object_href(item["object_id"], item.get("object_path", "")))}">{escape(item["title"])}</a></li>'
            for item in payload["objects"]["items"]
        )
        or "<li>None</li>"
    )
    contradiction_items = (
        "".join(
            f'<li><span class="pill">{escape(item["status"])}</span>{escape(item["subject_key"])}</li>'
            for item in payload["contradictions"]["items"]
        )
        or "<li>None</li>"
    )
    event_items = (
        "".join(
            f"<li>{escape(item['event_date'])} - "
            f'<a href="{escape(item["object_path"])}">{escape(item["title"])}</a></li>'
            for item in payload["events"]["items"]
        )
        or "<li>None</li>"
    )
    stale_summary_items = (
        "".join(
            f'<li><a href="{escape(item["object_path"])}">{escape(item["title"])}</a> '
            f"<span class='muted'>({escape(item['summary_text'])})</span></li>"
            for item in payload["stale_summaries"]["items"]
        )
        or "<li>None</li>"
    )
    evolution_items = _render_evolution_candidates(
        payload["evolution"]["items"],
        compact=False,
        requested_pack=requested_pack,
        next_path=_shell_href("/ops/evolution", requested_pack),
    )
    production_gap_items = (
        "".join(
            f'<li><span class="pill">{escape(item["stage_label"].replace("_", " "))}</span> '
            f'<a href="{escape(_note_href(item["note_path"], requested_pack))}">{escape(item["title"])}</a>'
            f"<div class='muted'>Missing: {escape(item['detail'])}</div></li>"
            for item in payload["production"]["weak_points"]
        )
        or "<li class='muted'>No production-chain weak points surfaced.</li>"
    )
    signal_items = (
        "".join(
            f'<li><span class="pill">{escape(item["signal_type"])}</span> '
            f'<a href="{escape(item["source_path"])}">{escape(item["title"])}</a>'
            f"<div class='muted'>{escape(item['detail'])}</div></li>"
            for item in payload["signals"]["items"]
        )
        or "<li class='muted'>No active signals surfaced.</li>"
    )
    priority_items = (
        "".join(
            f'<li><span class="pill">{escape(item["kind"].replace("_", " "))}</span> '
            f'<a href="{escape(item["path"])}">{escape(item["label"])}</a>'
            f"<div class='muted'>{escape(item['detail'])}</div></li>"
            for item in payload["priorities"]
        )
        or "<li class='muted'>No urgent maintenance items surfaced.</li>"
    )

    def _tile(label, value, *, warn=False):
        warn_cls = " warn" if warn else ""
        return (
            "<div class='card' style='margin:0'>"
            f"<div class='muted tiny'>{label}</div>"
            f"<div class='metric-num{warn_cls}' style='margin-top:4px'>{value}</div>"
            "</div>"
        )

    stats_cards = [
        _tile("Objects Indexed", payload["objects"]["count"]),
        _tile("Signal Count", payload["signals"]["count"]),
        _tile("Weak Point Count", payload["production"]["weak_point_count"]),
    ]
    if research_overview_supported:
        stats_cards[1:1] = [
            _tile(
                "Contradictions Open",
                payload["contradictions"]["open_count"],
                warn=int(payload["contradictions"]["open_count"]) > 0,
            ),
            _tile("Event Count", payload["events"]["count"]),
            _tile(
                "Stale Summary Count",
                payload["stale_summaries"]["count"],
                warn=int(payload["stale_summaries"]["count"]) > 0,
            ),
            _tile("Evolution Candidates", payload["evolution"]["candidate_count"]),
        ]
    research_overview_card = (
        ""
        if research_overview_supported
        else (
            "<section class='card'><h2>Research Overview</h2>"
            f"<p class='muted'>{escape(str(research_overview.get('reason') or ''))}</p>"
            "</section>"
        )
    )
    left_sections = [
        f"<section class='card'><h2>Needs Attention Now</h2><ul class='list-tight'>{priority_items}</ul></section>",
        f"<section class='card'><h2>Recent Objects</h2><ul class='list-tight'>{object_items}</ul></section>",
    ]
    if research_overview_supported:
        left_sections.extend(
            [
                f"<section class='card'><h2><a href='{escape(_shell_href('/ops/evolution', requested_pack))}'>Evolution</a></h2>{evolution_items}</section>",
                f"<section class='card'><h2><a href='{escape(payload['events']['browser_path'])}'>Recent Events</a></h2><ul class='list-tight'>{event_items}</ul></section>",
                f"<section class='card'><h2><a href='{escape(payload['stale_summaries']['browser_path'])}'>Stale Summaries</a></h2><ul class='list-tight'>{stale_summary_items}</ul></section>",
            ]
        )
    else:
        left_sections.append(research_overview_card)
    right_sections = [
        signals_surface_contract,
        f"<section class='card'><h2><a href='{escape(payload['signals']['browser_path'])}'>Signals</a></h2><ul class='list-tight'>{signal_items}</ul></section>",
        production_surface_contract,
        f"<section class='card'><h2><a href='{escape(payload['production']['browser_path'])}'>Production Weak Points</a></h2><ul class='list-tight'>{production_gap_items}</ul></section>",
    ]
    if research_overview_supported:
        right_sections.append(
            f"<section class='card'><h2><a href='{escape(payload['contradictions']['browser_path'])}'>Contradiction Queue</a></h2><ul class='list-tight'>{contradiction_items}</ul></section>"
        )
    right_sections.append(
        _render_review_history(payload["recent_review_actions"], title="Recent Review Actions")
    )
    foyer = payload.get("foyer") or {}
    foyer_today_path = str(foyer.get("today_path") or "/ops/today")
    foyer_queue_path = str(foyer.get("queue_path") or "/ops/queue")
    foyer_runs_path = str(foyer.get("runs_path") or "/ops/runs")
    foyer_today_summary = str(foyer.get("today_summary") or "—")
    foyer_queue_summary = str(foyer.get("queue_summary") or "—")
    last_run = foyer.get("last_run") or {}
    if last_run:
        last_run_summary = (
            f"{escape(str(last_run.get('workflow_type', '')))}"
            f" — <strong>{escape(str(last_run.get('status', '')))}</strong>"
            f" {_ts(str(last_run.get('started_at', ''))[:19])}"
        )
        last_run_link = (
            f"<a href='{escape(str(last_run.get('detail_href') or foyer_runs_path))}'>open →</a>"
        )
    else:
        last_run_summary = "<span class='muted'>no runs yet</span>"
        last_run_link = f"<a href='{escape(foyer_runs_path)}'>open →</a>"
    foyer_block = (
        "<section class='card'>"
        "<h2>Maintainer Foyer</h2>"
        "<table class='kv'>"
        f"<tr><th>Today</th><td>{escape(foyer_today_summary)}"
        f" <a href='{escape(foyer_today_path)}'>see →</a></td></tr>"
        f"<tr><th>Queue</th><td>{escape(foyer_queue_summary)}"
        f" <a href='{escape(foyer_queue_path)}'>see →</a></td></tr>"
        f"<tr><th>Last run</th><td>{last_run_summary} {last_run_link}</td></tr>"
        "</table>"
        "</section>"
    )

    dashboard_body = "".join(
        [
            "<h1>OVP Truth UI</h1>",
            "<p class='muted'>Read-only browser over <code>knowledge.db</code>. JSON APIs remain available at <code>/api/*</code>, including <code>/api/objects</code>.",
            f"{' Pack scope: ' + escape(requested_pack) + '.' if requested_pack else ''}</p>",
            foyer_block,
            runtime_card,
            runtime_state_card,
            run_history_card,
            "<section class='grid stats'>",
            "".join(stats_cards),
            "</section>",
            "<section style='display:grid;gap:1rem'>",
            "<section class='card'><h2>Workflow Map</h2><p class='muted'>Start here if you do not yet know which route to open. Each group maps one common operator workflow onto the current shell.</p></section>",
            workflow_groups_html,
            "<section class='card'><h2>Where To Start</h2><p class='muted'>Use the workflow map above to choose a route, then inspect the attention queues and knowledge surfaces below.</p></section>",
            orientation_assembly_contract,
            orientation_governance_contract,
            entry_sections_html,
            "</section>",
            "<section class='grid two-col'>",
            "<div style='display:grid;gap:1rem'>",
            "".join(left_sections),
            "</div>",
            "<div style='display:grid;gap:1rem'>",
            "".join(right_sections),
            "</div>",
            "</section>",
        ]
    )
    return _layout(
        "OVP Truth UI",
        dashboard_body,
        requested_pack=requested_pack,
        auto_refresh_seconds=10,
    )



def _render_evolution_browser_page(payload: dict) -> str:
    query = payload.get("query", "")
    status = payload.get("status", "all")
    selected_link_type = payload.get("link_type", "")
    requested_pack = payload.get("requested_pack", "")
    next_path = _shell_href("/ops/evolution", requested_pack)
    type_counts = (
        "".join(
            f"<span class='pill'>{escape(link_type)}: {count}</span>"
            for link_type, count in payload["type_counts"].items()
        )
        or "<span class='muted'>None</span>"
    )
    return _layout(
        "Evolution Browser",
        "".join(
            [
                "<h1>Evolution Browser</h1>",
                "<form method='get' action='/ops/evolution' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter evolution links' />",
                "<select name='status'>",
                "".join(
                    f"<option value='{escape(option)}' {'selected' if status == option else ''}>{escape(option)}</option>"
                    for option in ("all", "candidate", "accepted", "rejected")
                ),
                "</select>",
                "<select name='link_type'>",
                "<option value=''>all link types</option>",
                "".join(
                    f"<option value='{escape(option)}' {'selected' if selected_link_type == option else ''}>{escape(option)}</option>"
                    for option in _EVOLUTION_LINK_TYPES
                ),
                "</select>",
                "<button type='submit'>Search</button>",
                "</form>",
                f"<p class='muted'>{payload['count']} evolution records in the current view.</p>",
                f"<section class='card'><h2>Link Types</h2><div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>{type_counts}</div></section>",
                f"<section class='card'><h2>Accepted Links</h2>{_render_evolution_links(payload['accepted_links'], empty_text='No accepted evolution links yet.')}</section>",
                f"<section class='card'><h2>Rejected Links</h2>{_render_evolution_links(payload['rejected_links'], empty_text='No rejected evolution links yet.')}</section>",
                f"<section class='card'><h2>Candidate Links</h2>{_render_evolution_candidates(payload['candidate_items'], reviewable=True, requested_pack=requested_pack, next_path=next_path)}</section>",
            ]
        ),
        requested_pack=requested_pack,
    )



def _render_production_browser_page(payload: dict) -> str:
    query = payload.get("query", "")
    requested_pack = payload.get("requested_pack", "")
    surface_contract_card = _render_surface_contract_card(payload)
    operator_rail_card = _render_operator_rail(payload)
    lead_sections, remaining_sections = _split_lead_compiled_sections(
        payload.get("compiled_sections", [])
    )
    limit_note = (
        f" Showing the most recent {payload['limit']} production-chain entries in this browser window."
        if payload.get("is_limited")
        else ""
    )
    items = (
        "".join(
            "<li>"
            f'<a href="{escape(_note_href(item["path"], requested_pack))}">{escape(item["title"])}</a>'
            + f" <span class='pill'>{escape(item['stage_label'].replace('_', ' '))}</span>"
            + f" <span class='pill'>{escape(str(item['traceability'].get('chain_status') or ''))}</span>"
            + f" <span class='pill'>{item['traceability']['counts']['objects']} objects</span>"
            + f" <span class='pill'>{item['traceability']['counts']['atlas_pages']} atlas pages</span>"
            + f"<div class='muted'>Chain status: {escape(str(item['traceability'].get('chain_status') or ''))}</div>"
            + f"<div class='muted'>Missing stages: {escape(', '.join(str(value).replace('_', ' ') for value in item['traceability'].get('missing_stages', [])) or 'None')}</div>"
            + f"<div class='muted'>Chain summary: {escape(str(item['traceability'].get('chain_summary') or ''))}</div>"
            + f"<div class='muted'>Objects: {_render_object_links(item['traceability']['objects'], requested_pack=requested_pack)}</div>"
            + f"<div class='muted'>Atlas / MOC Reach: {_render_named_note_links(item['traceability']['atlas_pages'], requested_pack=requested_pack)}</div>"
            + "</li>"
            for item in payload["items"]
        )
        or "<li class='muted'>No production chains found.</li>"
    )
    weak_points = (
        "".join(
            "<li>"
            f'<span class="pill">{escape(item["stage_label"].replace("_", " "))}</span> '
            f'<a href="{escape(_note_href(item["note_path"], requested_pack))}">{escape(item["title"])}</a>'
            f"<div class='muted'>Missing: {escape(item['detail'])}</div>"
            "</li>"
            for item in payload["weak_points"]
        )
        or "<li class='muted'>No production-chain weak points surfaced.</li>"
    )
    section_nav = "".join(
        f'<a href="{escape(str(item["href"]))}">{escape(str(item["label"]))}</a>'
        for item in payload.get("section_nav", [])
    )
    return _layout(
        "Production Browser",
        "".join(
            [
                "<h1>Production Browser</h1>",
                "<form method='get' action='/ops/production'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter source notes, objects, or atlas' /> ",
                "<button type='submit'>Search</button>",
                "</form>",
                f"<p class='muted'>{payload['count']} production-chain entries. {payload['counts']['source_notes']} source notes.",
                f" Pack scope: {escape(requested_pack)}." if requested_pack else "",
                f"{escape(limit_note)}</p>",
                _render_compiled_sections(lead_sections),
                operator_rail_card,
                surface_contract_card,
                f"<nav class='subnav'>{section_nav}</nav>" if section_nav else "",
                _render_compiled_sections(remaining_sections),
                "<section class='card'><h2>Chain Model</h2><p class='muted'>This browser shows the current upstream/downstream chain from traceable notes into deep dives, evergreen objects, and Atlas placement.</p></section>",
                f"<section class='card'><h2>Weak Points</h2><ul class='list-tight'>{weak_points}</ul></section>",
                f"<section class='card'><ul class='list-tight'>{items}</ul></section>",
            ]
        ),
        requested_pack=requested_pack,
    )



def _render_production_summary_card(
    summary: dict[str, object],
    *,
    title: str = "Production Contribution",
    requested_pack: str = "",
) -> str:
    signal_items = (
        "".join(
            f"<li>{escape(str(signal['label']))}: {int(signal['count'])}</li>"
            for signal in summary["signals"]
        )
        or "<li class='muted'>No production-chain gaps surfaced for this scope.</li>"
    )
    count_items = "".join(
        f"<li>{escape(label)}: {int(summary['counts'][key])}</li>"
        for key, label in (
            ("source_notes", "Source notes"),
            ("atlas_pages", "Atlas / MOC pages"),
        )
    )
    return (
        "<section class='card'>"
        f"<h2>{escape(title)}</h2>"
        "<table class='kv'>"
        f"<tr><th>Objects in scope</th><td>{int(summary['object_count'])}</td></tr>"
        f"<tr><th>Top Source Notes</th><td>{_render_named_note_links(summary['top_source_notes'], requested_pack=requested_pack)}</td></tr>"
        f"<tr><th>Atlas / MOC Reach</th><td>{_render_named_note_links(summary['top_atlas_pages'], requested_pack=requested_pack)}</td></tr>"
        "</table>"
        f"<ul class='list-tight'>{count_items}{signal_items}</ul>"
        "</section>"
    )



def _render_pulse_page(*, requested_pack: str = "") -> str:
    fragment = _render_pulse_fragment()
    help_banner = _render_page_help(
        "Pulse",
        what=(
            "Live tail of pipeline log files (<code>pipeline.jsonl</code>,"
            " <code>reuse.jsonl</code>, <code>evidence.jsonl</code>,"
            " <code>open-questions.jsonl</code>).  Polls once per second."
        ),
        can=(
            "Watch in real time as the absorb/intake pipeline runs."
            "  No interactive controls; this is purely a tail."
        ),
        effect=("Read-only.  The poll only reads from disk — nothing else."),
    )
    body = (
        "<h1>Pulse</h1>"
        + help_banner
        + "<p class='muted'>Live tail of <code>60-Logs/*.jsonl</code> (pipeline, reuse, "
        "evidence, open-questions). Polls once per second.</p>" + fragment
    )
    return _layout("Pulse", body, requested_pack=requested_pack)



def _render_queue_overview_page(payload: dict) -> str:
    """Maintainer queue landing page.

    Surfaces the four pending-review queues (concept candidates,
    contradictions, signals, action queue) in one place so the
    operator can tell whether triage is done without visiting four
    pages.  Healthy state (productive signals, succeeded actions,
    evergreen total) is surfaced separately so "no action needed"
    is visible too.
    """
    requested_pack = str(payload.get("requested_pack") or "")
    queues = payload.get("queues") or []
    healthy = payload.get("healthy") or {}
    pending_total = int(payload.get("pending_total") or 0)

    intro = _render_page_help(
        "Maintainer queue",
        what=(
            "The four queues that need a human decision before the"
            " pipeline can move on: <strong>concept candidates</strong>,"
            " <strong>contradictions</strong>,"
            " <strong>signals waiting</strong>, and"
            " <strong>actions failed/blocked</strong>."
        ),
        can=(
            "Click any row to open that queue's detail page."
            "  Empty queues are listed under Healthy so you can confirm"
            " &ldquo;no action needed&rdquo; rather than wonder whether"
            " the pipeline is broken."
        ),
        effect=(
            "This page is just an aggregator — counts come from the"
            " same builders the four detail pages use, so the foyer"
            " never goes stale."
        ),
    )

    if pending_total == 0:
        pending_html = (
            "<section class='card'><h2>Pending review</h2>"
            "<p class='muted'>Nothing waiting in any queue.</p></section>"
        )
    else:
        rows: list[str] = []
        for queue in queues:
            count = int(queue.get("count") or 0)
            if count == 0:
                # Skip empty queues from the pending list — the
                # healthy-state card carries the "0 waiting" signal.
                continue
            label = str(queue.get("label") or queue.get("id") or "")
            href = str(queue.get("browse_path") or "")
            oldest_subject = str(queue.get("oldest_subject") or "")
            oldest_at = str(queue.get("oldest_at") or "")[:19]
            oldest_html = ""
            if oldest_subject:
                oldest_html = f" <span class='muted'>(oldest: {escape(oldest_subject)}"
                if oldest_at:
                    oldest_html += f" @ {escape(oldest_at)}"
                oldest_html += ")</span>"
            rows.append(
                f"<li><strong>{count}</strong> {escape(label)}"
                f"{oldest_html}"
                f" — <a href='{escape(href)}'>review →</a></li>"
            )
        pending_html = (
            "<section class='card'><h2>Pending review</h2>"
            f"<ul class='list-tight'>{''.join(rows)}</ul></section>"
        )

    healthy_html = (
        "<section class='card'><h2>Healthy (no action needed)</h2>"
        "<ul class='list-tight'>"
        f"<li>{int(healthy.get('productive_signals') or 0)} productive signals</li>"
        f"<li>{int(healthy.get('succeeded_actions') or 0)} succeeded actions</li>"
        f"<li>{int(healthy.get('evergreen_total') or 0)} evergreen objects in the truth store</li>"
        "</ul></section>"
    )

    body = "<h1>Maintainer Queue</h1>" + intro + pending_html + healthy_html
    return _layout("Queue", body, requested_pack=requested_pack)



def _render_signals_page(payload: dict) -> str:
    query = payload.get("query", "")
    selected_type = payload.get("signal_type", "")
    requested_pack = payload.get("requested_pack", "")
    next_path = "/ops/signals" + (
        f"?pack={quote(requested_pack, safe='')}" if requested_pack else ""
    )
    surface_contract_card = _render_surface_contract_card(payload)
    governance_contract_card = _render_governance_contract_card(payload)
    operator_rail_card = _render_operator_rail(payload)
    options = ["", *sorted(payload["signal_type_explanations"].keys())]
    option_html = "".join(
        f"<option value='{escape(option)}' {'selected' if option == selected_type else ''}>"
        f"{escape(option or 'all signal types')}</option>"
        for option in options
    )
    items = (
        "".join(
            "<li>"
            f'<span class="pill">{escape(item["signal_type"])}</span> '
            f'<a href="{escape(item["source_path"])}">{escape(item["title"])}</a>'
            f"<div class='muted'>{escape(item['detail'])}</div>"
            + (
                f"<div class='muted'>Impact: {escape(str(item['impact_summary']['impact_label']))}</div>"
                if item.get("impact_summary", {}).get("impact_label")
                else ""
            )
            + (
                f"<div class='muted'>{escape(str(item['impact_summary']['impact_detail']))}</div>"
                if item.get("impact_summary", {}).get("impact_detail")
                else ""
            )
            + (
                f"<div class='muted'>Inbound capture: {escape(str(item['capture_summary']['summary']))}</div>"
                if item.get("capture_summary", {}).get("summary")
                else ""
            )
            + _render_signal_context_contract(item)
            + (
                "<div class='muted'>Recommended Action: "
                + f'<a href="{escape(item["recommended_action"]["path"])}">{escape(item["recommended_action"]["label"])}</a>'
                + (
                    f" <span class='pill'>{escape(str(item['recommended_action']['queue_status']))}</span>"
                    if item["recommended_action"].get("queue_status")
                    else (
                        " <span class='pill'>executable</span>"
                        if item["recommended_action"].get("executable")
                        else " <span class='pill'>manual</span>"
                    )
                )
                + (
                    f"<div class='muted'>Resolver: {escape(str(item['recommended_action']['resolution_kind']))}</div>"
                    if item["recommended_action"].get("resolution_kind")
                    else ""
                )
                + (
                    f"<div class='muted'>Dispatch: {escape(str(item['recommended_action']['dispatch_mode']))}</div>"
                    if item["recommended_action"].get("dispatch_mode")
                    else ""
                )
                + (
                    f"<div class='muted'>Rule: {escape(str(item['recommended_action']['resolver_rule_name']))}</div>"
                    if item["recommended_action"].get("resolver_rule_name")
                    else ""
                )
                + (
                    f"<div class='muted'>Governance contract: {escape(str(item['recommended_action']['governance_provider_name']))} · {escape(str(item['recommended_action']['governance_provider_pack']))}</div>"
                    if item["recommended_action"].get("governance_provider_name")
                    or item["recommended_action"].get("governance_provider_pack")
                    else ""
                )
                + (
                    "<div class='muted'>Governance: safe</div>"
                    if item["recommended_action"].get("safe_to_run")
                    else ""
                )
                + "</div>"
                if item.get("recommended_action")
                else ""
            )
            + (
                "<form method='post' action='/ops/actions/enqueue' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
                + f"<input type='hidden' name='signal_id' value='{escape(item['signal_id'])}' />"
                + f"<input type='hidden' name='next' value='{escape(next_path)}' />"
                + "<button type='submit'>Queue action</button>"
                + "</form>"
                if item.get("recommended_action")
                and not item["recommended_action"].get("queue_status")
                else ""
            )
            + (
                "<div class='muted'>Downstream: "
                + ", ".join(
                    f'<a href="{escape(effect["path"])}">{escape(effect["label"])}</a>'
                    for effect in item["downstream_effects"]
                )
                + "</div>"
                if item["downstream_effects"]
                else ""
            )
            + "</li>"
            for item in payload["items"]
        )
        or "<li class='muted'>No active signals found.</li>"
    )
    explanations = "".join(
        f"<li><span class='pill'>{escape(signal_type)}</span> {escape(text)}</li>"
        for signal_type, text in payload["signal_type_explanations"].items()
    )
    return _layout(
        "Active Signals",
        "".join(
            [
                "<h1>Active Signals</h1>",
                _render_page_help(
                    "Signals",
                    what=(
                        "Detection-only observations the pipeline emits when it"
                        " notices something worth a human look — stale summaries,"
                        " open contradictions, missing provenance, etc."
                        "  Signals are passive: nothing happens until you queue"
                        " an action."
                    ),
                    can=(
                        "Filter by status (productive / waiting / failed/stalled)"
                        " or signal type.  <strong>Queue action</strong> sends"
                        " the recommended command to the action worker."
                        "  <strong>Dismiss</strong> tags the signal as not worth"
                        " acting on; both the row and any attached evidence"
                        " stay live."
                    ),
                    effect=(
                        "Queueing an action adds a row to /ops/queue/actions —"
                        " the worker runs it on its next cycle.  Until then the"
                        " truth store is unchanged.  Dismissing only updates the"
                        " signal ledger."
                    ),
                ),
                "<form method='get' action='/ops/queue/signals' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Search signals' />",
                f"<select name='type'>{option_html}</select>",
                "<button type='submit'>Filter</button>",
                "</form>",
                f"<p class='muted'>{payload['count']} active signals.",
                (
                    " "
                    + f"{payload.get('impact_counts', {}).get('productive', 0)} productive, "
                    + f"{payload.get('impact_counts', {}).get('waiting', 0)} waiting, "
                    + f"{payload.get('impact_counts', {}).get('failed', 0) + payload.get('impact_counts', {}).get('stalled', 0)} failed/stalled."
                ),
                f" Pack scope: {escape(requested_pack)}." if requested_pack else "",
                "</p>",
                operator_rail_card,
                surface_contract_card,
                governance_contract_card,
                f"<section class='card'><h2>Signal Types</h2><ul class='list-tight'>{explanations}</ul></section>",
                f"<section class='card'><ul class='list-tight'>{items}</ul></section>",
            ]
        ),
        requested_pack=requested_pack,
    )



def _render_stale_summaries_page(payload: dict) -> str:
    query = payload.get("query", "")
    requested_pack = payload.get("requested_pack", "")
    next_path = "/ops/summaries" + (
        f"?pack={quote(requested_pack, safe='')}" if requested_pack else ""
    )
    detection_notes = "".join(f"<li>{escape(note)}</li>" for note in payload["detection_notes"])
    items = (
        "".join(
            "<li>"
            f"<label><input type='checkbox' form='summary-batch-form' name='object_id' value='{escape(item['object_id'])}' /> batch</label> "
            f'<a href="{escape(item["object_path"])}">{escape(item["title"])}</a> '
            f"<span class='muted'>({escape(item['object_id'])})</span>"
            f"<div class='muted'>Summary: {escape(item['summary_text'])}</div>"
            f"<div class='muted'>Outgoing relations: {item['outgoing_relation_count']}</div>"
            + (
                f"<div class='muted'>Latest event date: {escape(item['latest_event_date'])}</div>"
                if item["latest_event_date"]
                else ""
            )
            + "<ul class='list-tight'>"
            + "".join(f"<li>{escape(reason)}</li>" for reason in item["reason_texts"])
            + "</ul>"
            + (
                "<details><summary>Review History</summary><ul class='list-tight'>"
                + "".join(
                    f"<li>{_ts(history['timestamp'])} <span class='pill'>{escape(str(history['event_type']))}</span>"
                    + (
                        f"<div class='muted'>Rebuilt: {escape(', '.join(str(v) for v in history['rebuilt_object_ids']))}</div>"
                        if history.get("rebuilt_object_ids")
                        else ""
                    )
                    + "</li>"
                    for history in item["review_history"]
                )
                + "</ul></details>"
                if item["review_history"]
                else ""
            )
            + "<form method='post' action='/ops/summaries/rebuild' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
            + f"<input type='hidden' name='object_id' value='{escape(item['object_id'])}' />"
            + f"<input type='hidden' name='next' value='{escape(next_path)}' />"
            + "<button type='submit'>Rebuild Summary</button>"
            + "</form>"
            + "</li>"
            for item in payload["items"]
        )
        or "<li class='muted'>No stale summaries detected.</li>"
    )
    return _layout(
        "Stale Summaries",
        "".join(
            [
                "<h1>Stale Summaries</h1>",
                "<form method='get' action='/ops/summaries'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter stale summaries' /> ",
                "<button type='submit'>Filter</button>",
                "</form>",
                f"<p class='muted'>{payload['count']} stale summary candidates.",
                f" Pack scope: {escape(requested_pack)}." if requested_pack else "",
                "</p>",
                f"{_render_review_context_card(payload['review_context'])}",
                f"{_render_review_history(payload['review_history'])}",
                f"<section class='card'><h2>Detection Notes</h2><ul class='list-tight'>{detection_notes}</ul></section>",
                "<section class='card'>",
                "<h2>Batch Rebuild</h2>",
                "<form id='summary-batch-form' method='post' action='/ops/summaries/rebuild' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
                f"<input type='hidden' name='next' value='{escape(next_path)}' />",
                "<button type='submit'>Rebuild Selected</button>",
                "</form>",
                "</section>",
                f"<section class='card'><ul class='list-tight'>{items}</ul></section>",
            ]
        ),
        requested_pack=requested_pack,
    )



def _render_unsupported_route_page(route_path: str, requested_pack: str = "") -> str:
    payload = _unsupported_route_payload(route_path, requested_pack)
    return _layout(
        "Route Unavailable",
        "".join(
            [
                "<h1>Route Unavailable</h1>",
                f"<p class='muted'>{escape(payload['error'])}</p>",
                "<section class='card'><h2>Why</h2><p class='muted'>This route currently belongs to the research-specific observation shell. Shared shell routes remain available, but research-only routes stay hidden until the current pack declares equivalent semantics.</p></section>",
            ]
        ),
        requested_pack=requested_pack,
    )



def _replace_wikilinks_with_markdown_links(
    vault_dir: Path, markdown: str, *, requested_pack: str = ""
) -> str:
    def replace_match(match: re.Match[str]) -> str:
        raw_inner = match.group(1)
        target_part, _, label_part = raw_inner.partition("|")
        label = label_part.strip() or target_part.split("#", 1)[0].strip()
        resolved = _lookup_wikilink_target(vault_dir, target_part, requested_pack=requested_pack)
        href = (
            resolved[0]
            if resolved
            else _search_href(target_part.split("#", 1)[0].strip() or label, requested_pack)
        )
        emoji = "🔍" if _is_search_href(href) else "🎯"
        safe_label = label.replace("[", "\\[").replace("]", "\\]")
        return f"[{emoji} {safe_label}]({href})"

    output_lines: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            output_lines.append(line)
            continue
        if in_fence:
            output_lines.append(line)
            continue
        output_lines.append(re.sub(r"\[\[([^\]]+)\]\]", replace_match, line))
    return "\n".join(output_lines)


__all__ = [
    '_linkify_related_knowledge_section',
    '_render_actions_page',
    '_render_candidates_page',
    '_render_contradictions_page',
    '_render_dashboard',
    '_render_evolution_browser_page',
    '_render_production_browser_page',
    '_render_production_summary_card',
    '_render_pulse_page',
    '_render_queue_overview_page',
    '_render_signals_page',
    '_render_stale_summaries_page',
    '_render_unsupported_route_page',
    '_replace_wikilinks_with_markdown_links'
]
