from openclaw_pipeline.lint_checker import KnowledgeLinter


def test_lint_resolves_links_via_note_id_not_only_filename(temp_vault):
    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "Linked.md"
    deep_dive = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04-07_Seed_深度解读.md"

    evergreen.write_text(
        """---
note_id: linked-note
title: Linked Note
type: evergreen
date: 2026-01-01
aliases: ["Linked"]
---

# Linked Note
""",
        encoding="utf-8",
    )
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
note_id: seed-note
title: Seed Note
type: deep_dive
date: 2026-04-07
---

# Seed Note

Links to [[linked-note]].
""",
        encoding="utf-8",
    )

    linter = KnowledgeLinter(temp_vault)
    linter.scan()
    linter.check_broken_links()
    linter.check_missing_concepts()

    broken = [issue for issue in linter.issues if issue.type == linter.BROKEN_LINK]
    missing = [issue for issue in linter.issues if issue.type == linter.MISSING_CONCEPT]

    assert broken == []
    assert missing == []
