from __future__ import annotations

import json


def test_run_operations_command_writes_frontmatter_review_items(temp_vault):
    from openclaw_pipeline.commands.run_operations import main
    from openclaw_pipeline.runtime import VaultLayout

    note = temp_vault / "10-Knowledge" / "Evergreen" / "Broken.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        """---
note_id: broken
type: evergreen
date: 2026-04-10
---

# Broken
""",
        encoding="utf-8",
    )

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--profile",
            "vault/frontmatter_audit",
        ]
    )

    layout = VaultLayout.from_vault(temp_vault)
    artifacts = sorted(layout.review_queue_dir.rglob("*.json"))

    assert result == 0
    assert artifacts
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["queue_name"] == "frontmatter"
    assert payload["issue_type"] == "missing-title"
