from __future__ import annotations

from ..base import (
    AssemblyAudienceSpec,
    AssemblyFreshnessPolicy,
    AssemblyInputSpec,
    AssemblyOutputSpec,
    AssemblyRecipeSpec,
)


def build_assembly_recipes(pack_name: str = "research-tech") -> list[AssemblyRecipeSpec]:
    return [
        AssemblyRecipeSpec(
            name="operator_briefing",
            pack=pack_name,
            recipe_kind="operator_briefing",
            description="Operator-facing briefing snapshot over current signals and priorities.",
            source_contract_kind="observation_surface",
            source_contract_name="briefing",
            inputs=[
                AssemblyInputSpec(
                    source_kind="signals",
                    description="Current signal ledger and briefing prioritization state",
                )
            ],
            audience=AssemblyAudienceSpec(audience="operator", interaction_mode="triage"),
            freshness_policy=AssemblyFreshnessPolicy(
                cache_mode="derived_cache",
                invalidation_signals=["signals", "actions", "review_queue"],
            ),
            output=AssemblyOutputSpec(output_mode="json", publish_target="ui_payload"),
        ),
        AssemblyRecipeSpec(
            name="topic_overview",
            pack=pack_name,
            recipe_kind="topic_overview",
            description="Compiled topic overview built from canonical evergreen knowledge.",
            source_contract_kind="wiki_view",
            source_contract_name="overview/topic",
            inputs=[
                AssemblyInputSpec(
                    source_kind="evergreen_object",
                    description="Canonical evergreen objects and relations",
                )
            ],
            output=AssemblyOutputSpec(output_mode="markdown", publish_target="compiled_markdown"),
        ),
        AssemblyRecipeSpec(
            name="object_brief",
            pack=pack_name,
            recipe_kind="object_brief",
            description="Readable object page for one canonical object.",
            source_contract_kind="wiki_view",
            source_contract_name="object/page",
            inputs=[
                AssemblyInputSpec(
                    source_kind="object_id",
                    description="Canonical object identifier",
                )
            ],
            output=AssemblyOutputSpec(output_mode="markdown", publish_target="compiled_markdown"),
        ),
        AssemblyRecipeSpec(
            name="event_dossier",
            pack=pack_name,
            recipe_kind="event_dossier",
            description="Compiled event dossier for time-bounded research topics and events.",
            source_contract_kind="wiki_view",
            source_contract_name="event/dossier",
            inputs=[
                AssemblyInputSpec(
                    source_kind="event_scope",
                    description="Event-linked objects, claims, and evidence",
                )
            ],
            output=AssemblyOutputSpec(output_mode="markdown", publish_target="compiled_markdown"),
        ),
        AssemblyRecipeSpec(
            name="contradiction_view",
            pack=pack_name,
            recipe_kind="contradiction_view",
            description="Compiled contradiction review view over open truth conflicts.",
            source_contract_kind="wiki_view",
            source_contract_name="truth/contradictions",
            inputs=[
                AssemblyInputSpec(
                    source_kind="contradiction_rows",
                    description="Open contradiction rows and their ranked evidence",
                )
            ],
            output=AssemblyOutputSpec(output_mode="markdown", publish_target="compiled_markdown"),
        ),
    ]
