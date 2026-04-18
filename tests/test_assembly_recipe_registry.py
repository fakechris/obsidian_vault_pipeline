from __future__ import annotations

import pytest


def test_assembly_recipe_registry_raises_value_error_for_unknown_pack():
    from openclaw_pipeline.assembly_recipe_registry import resolve_assembly_recipe_spec

    with pytest.raises(ValueError):
        resolve_assembly_recipe_spec(
            pack_name="missing-pack",
            recipe_name="topic_overview",
        )
