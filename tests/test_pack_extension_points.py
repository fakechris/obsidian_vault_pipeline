from __future__ import annotations

import pytest


def test_default_pack_exposes_new_derived_extension_points():
    from openclaw_pipeline.packs.loader import load_default_pack

    pack = load_default_pack()

    assert pack.extraction_profiles()
    assert pack.operation_profiles()
    assert pack.wiki_views()


def test_base_pack_lookup_methods_raise_for_unknown_extension_names():
    from openclaw_pipeline.packs.loader import load_default_pack

    pack = load_default_pack()

    with pytest.raises(ValueError, match="Unknown extraction profile"):
        pack.extraction_profile("missing")

    with pytest.raises(ValueError, match="Unknown operation profile"):
        pack.operation_profile("missing")

    with pytest.raises(ValueError, match="Unknown wiki view"):
        pack.wiki_view("missing")
