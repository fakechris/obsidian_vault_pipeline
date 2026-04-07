from __future__ import annotations

from openclaw_pipeline.graph.daily_delta import DailyDelta


def test_daily_delta_expands_incoming_and_outgoing_neighbors(temp_vault):
    evergreen_dir = temp_vault / "10-Knowledge" / "Evergreen"
    topics_dir = temp_vault / "20-Areas" / "AI-Research" / "Topics"
    topics_dir.mkdir(parents=True, exist_ok=True)

    (topics_dir / "2026-04-07_Seed.md").write_text(
        """---
note_id: seed-note
title: Seed Note
type: deep_dive
date: 2026-04-07
---

# Seed Note

Links to [[outgoing-note]].
""",
        encoding="utf-8",
    )
    (evergreen_dir / "Incoming.md").write_text(
        """---
note_id: incoming-note
title: Incoming Note
type: evergreen
date: 2026-01-01
---

# Incoming

Backlinks to [[seed-note]].
""",
        encoding="utf-8",
    )
    (evergreen_dir / "Outgoing.md").write_text(
        """---
note_id: outgoing-note
title: Outgoing Note
type: evergreen
date: 2026-01-01
---

# Outgoing
""",
        encoding="utf-8",
    )

    delta = DailyDelta(temp_vault).generate("2026-04-07", expand_hops=1)
    node_map = {node["note_id"]: node for node in delta["nodes"]}

    assert set(node_map) >= {"seed-note", "incoming-note", "outgoing-note"}
    assert node_map["seed-note"]["seed_role"] == "seed"
    assert node_map["incoming-note"]["distance_from_seed"] == 1
    assert node_map["outgoing-note"]["distance_from_seed"] == 1
