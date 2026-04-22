from __future__ import annotations

from ..base import SemanticRelationContractSpec, SemanticRelationTypeSpec


def build_semantic_relation_contracts(
    pack_name: str = "research-tech",
) -> list[SemanticRelationContractSpec]:
    object_kinds = ("concept", "entity", "tool", "company", "paper", "project")
    return [
        SemanticRelationContractSpec(
            name="research_semantic_relations",
            pack=pack_name,
            description=(
                "Review-gated semantic relation candidates between canonical research "
                "objects. This contract declares vocabulary and evidence requirements; "
                "it does not authorize direct writes into canonical truth."
            ),
            source_contract_kind="artifact_spec",
            source_contract_name="semantic_relation_candidate",
            review_queue_name="semantic-relations",
            write_policy="review_required",
            relation_types=[
                SemanticRelationTypeSpec(
                    name="supports",
                    description="The source object provides evidence or reasoning for the target.",
                    source_object_kinds=object_kinds,
                    target_object_kinds=object_kinds,
                ),
                SemanticRelationTypeSpec(
                    name="challenges",
                    description="The source object disputes, weakens, or creates tension with the target.",
                    source_object_kinds=object_kinds,
                    target_object_kinds=object_kinds,
                ),
                SemanticRelationTypeSpec(
                    name="extends",
                    description="The source object builds on or generalizes the target.",
                    source_object_kinds=object_kinds,
                    target_object_kinds=object_kinds,
                ),
                SemanticRelationTypeSpec(
                    name="replaces",
                    description="The source object supersedes the target for current use.",
                    source_object_kinds=object_kinds,
                    target_object_kinds=object_kinds,
                ),
                SemanticRelationTypeSpec(
                    name="uses",
                    description="The source object depends on the target as a method, tool, or substrate.",
                    source_object_kinds=object_kinds,
                    target_object_kinds=object_kinds,
                ),
            ],
        )
    ]
