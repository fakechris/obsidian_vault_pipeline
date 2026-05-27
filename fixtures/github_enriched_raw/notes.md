# github_enriched_raw — jordanbaird/Ice

## Why this fixture

Captures a case the other three fixtures don't: **source → enriched raw → STOP**. No interpretation file was produced. This is real legacy behavior, not an oversight in fixture capture.

If the new system silently produces an interpretation for every input, it's making a different product than the legacy system was — that may be the right call, but it should be a **deliberate** decision documented somewhere, not an accidental side effect of "we ran the interpreter on everything because it was easier."

## Source

- Raw: `50-Inbox/03-Processed/2026-05/2026-05-04_jordanbaird_Ice.md`
- Source URL: `https://github.com/jordanbaird/Ice`
- Stars: 27,758
- Enrichment tier: `deepwiki`
- Deepwiki sections in raw: 31
- Raw file size: ~216 KB

## Pairing

| Role | Path in legacy vault |
|---|---|
| Raw input | `50-Inbox/03-Processed/2026-05/2026-05-04_jordanbaird_Ice.md` |
| Interpretation | **none** |

A search across the entire vault (`find ovp-vault -name '*Ice*' -o -name '*jordanbaird*'`) returned only the raw + a pinboard archive copy. No `_深度解读.md` exists.

## Contract: MUST preserve

- `SourceKind::GithubRepo` as a first-class variant of `SourceBody`.
- Raw github frontmatter fields are typed, not optional flags on a generic struct: `github_owner`, `github_repo`, `github_stars`, `deepwiki_section_count`, `source_tier`.
- The pipeline must be able to **route an input to a terminal state with no interpretation** and emit an event explaining why. Whatever the routing decision is (`StopAtRaw`, `NotImplemented`, `LowValue`), it must be observable in the event log.

## Contract: SHOULD preserve

- The deepwiki section structure within the raw body — these sections are pre-extracted by an upstream enricher and contain useful structured content (Architecture, Modules, etc.). Re-extracting them in the new system would be wasteful.
- `github_stars` as a number, not a string.

## Contract: MAY break

- The decision to **not interpret** github repos. The new system MAY add a `GithubRepoInterpreter` that produces a project-overview note. This is **net-new behavior**, not a regression — make it explicit in the new system's manifest by including a routed `github_interpreter` node.

## Open questions (the important ones)

1. **Was "no interpretation" deliberate or accidental in the legacy system?** Best guess from looking at code organization and the existence of `auto_github_processor.py` in the legacy `ovp_pipeline`: the github processor was implemented for ingestion + enrichment, but the interpretation step was lower priority and never reached most repos. So this is closer to "accidental" / "backlog" than "designed".
2. **What would a github interpretation look like?** Suggested shape:
   - 一句话定义 (this repo's purpose, 1 sentence)
   - 核心模块 (top-level architecture from deepwiki sections)
   - 关键代码模式 (recurring patterns worth learning from)
   - 适用场景 (when to use this vs alternatives)
   - 不适用场景 (anti-patterns / when to avoid)
   - 相关项目 (concept_candidates pointing at related repos/concepts)

   This is shorter than article/paper interpretations because most repos are tools, not knowledge contributions.

## Recommendation for the new system

Implement github interpretation. The legacy gap is a real product gap, not a desired feature. Mark this fixture as **MAY-IMPROVE** — the new system should produce more than the legacy system did here, but be honest about what it's adding.
