# article_clean — Agent-native Product Management

## Why this fixture

Cleanest representative case. Single English source, no images, structured 6-dimension interpretation, no canonical concepts yet (everything is candidate). This is the **happy-path baseline** — if the new system can't reproduce this, nothing else will work.

## Pairing

| Role | Path in legacy vault |
|---|---|
| Raw input | `50-Inbox/03-Processed/2026-05/2026-05-04_A_Guide_to_Agent-native_Product_Management.md` |
| Interpretation | `20-Areas/AI-Research/Topics/2026-05/2026-05-04_A Guide to Agent-native Product Management_深度解读.md` |

Pairing key: **`source` URL** in both frontmatters resolves to the same canonical document.

## Contract: MUST preserve

These fields are load-bearing. If the new system drops or mutates them, downstream lookup breaks.

- `title` — must equal source's `title` (article kind preserves original title).
- `source` — exact URL, no normalization beyond stripping `?source=post_button` style trackers.
- `date` — interpretation creation date (not source `published`).
- `type: article` — drives MOC routing.
- `area: ai` — drives directory choice (AI-Research vs Tools vs Investing).
- `canonical_concepts` + `concept_candidates` — the absorb-state of extracted evergreens. Type is "list of slug strings". Empty list is meaningful (means "extraction ran, found nothing canonical yet").
- Section headers: `## 详细解释` with `### 是什么？` / `### 为什么重要？` / `### 如何运作？` subsections (or equivalents); `## 行动建议`. The new system can rename them, but the **6 dimensions** must still be present (definition / explanation / details / structure / actionable / linking).

## Contract: SHOULD preserve

These are valuable but the new system MAY change them with a documented rationale.

- `tags` — semantic tag set. Exact tags will differ run-to-run with LLM rewrites; what matters is non-empty + relevant.
- `concept_candidates` slug spelling — `agent-native-product-management` style. Tolerable to rename if the new identity resolver decides on a different canonical form, but if the slug changes, the evergreen pages it points to must be renamed in lock-step.
- The interp's body Markdown structure (definition → explanation → details → ...).
- `author` — sometimes `"原文未说明"` (placeholder for unknown). New system can use a typed `Option<Author>` instead.

## Contract: MAY break

- `pipeline_run_id` — legacy run ID. New system's run ID format will differ; that's fine.
- `link_resolution_status` / `link_resolution_version` — legacy derived-index flags. New system has its own derived-index model.
- `original_note_type` — legacy routing artifact. New system models routing differently.
- `status: completed` — legacy lifecycle flag. Can be replaced or dropped.
- Body indentation, exact whitespace, exact word choice in prose.

## Open questions / known anomalies

- Source title has the trailing tracker `?source=post_button` stripped in the interp's `source` field. New system must apply the same canonicalization, or the absorb step will create duplicates.
- Source raw `author` is `"[[Marcus Moretti]]"` (wiki-linked). Interp `author` is `"原文未说明"`. The legacy pipeline appears to have **dropped the author** — likely because the wiki-link form didn't parse cleanly. The new system should preserve `Marcus Moretti` as a plain string. This is a **bug fix**, not a contract break.
