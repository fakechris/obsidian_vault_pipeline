# github_enriched_raw — jordanbaird/Ice

## Why this fixture

Captures a case the other three fixtures don't: **source → enriched raw → STOP**. No interpretation file was produced. This is real legacy behavior, not an oversight in fixture capture.

The contract here is **single-outcome**: for v0.1, "terminal raw, no interpretation" is the only legal outcome. The new system's github-handling path must route the record to a terminal state with an observable event, and produce no `_深度解读.md` and no WriteOp targeting `20-Areas/`.

Whether the new system *eventually* grows a GitHub interpreter is a **post-v0.1 product decision**, separate from this contract. See "Out of scope for v0.1" at the bottom.

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
| Interpretation | **none** (terminal state) |

A vault-wide search returned only the raw + a pinboard archive copy. No `_深度解读.md` exists or is expected.

## Contract: MUST preserve

- `SourceKind::GithubRepo` as a first-class variant of `SourceBody`.
- Raw github frontmatter is typed, not optional flags on a generic struct: `github_owner`, `github_repo`, `github_stars: u32`, `deepwiki_section_count: u32`, `source_tier`.
- Routing must emit an explicit terminal event (`SourceRoutedToTerminalRaw` or similar) when a github record reaches the end of its v0.1 pipeline. Silent "no interpretation produced" is not acceptable.
- The pipeline emits **zero WriteOps targeting `20-Areas/`** for this input — only Inbox-side writes are allowed.

## Contract: SHOULD preserve

- The deepwiki section structure within the raw body is pre-extracted by an upstream enricher. The new system should preserve it (not re-extract).
- `github_stars` as a number.

## Contract: MAY break

- Legacy raw's `tags: [menu]` is incidental noise from the clipping side. New system may use a richer tag scheme.
- Exact directory placement of the raw (e.g. `50-Inbox/03-Processed/` vs a new layout).

## Out of scope for v0.1 (post-v0.1 product question)

Whether to **build** a GitHub interpreter is a product decision, not a contract clause. If the new system grows one later, it would produce a structured project-overview note (suggested shape: 一句话定义 / 核心模块 / 关键代码模式 / 适用场景 / 不适用场景 / 相关项目). That decision is deferred — it doesn't belong in this fixture's MUST/SHOULD/MAY contract.

The reason the legacy system has no github interpretation is best-guessed as backlog (not deliberate design); see `fixtures/SURVEY.md` open questions.
