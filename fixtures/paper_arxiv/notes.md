# paper_arxiv — Deep GraphRAG

## Why this fixture

Demonstrates that papers are a **fundamentally different document kind** from articles, not just a tag variation:

- **Raw frontmatter** has paper-specific fields (`arxiv_id`, `source_authors` as list, `source_published_at`, `arxiv_categories`).
- **Interpretation structure** is 10 numbered sections (`元信息` / `一句话核心贡献` / `研究背景与动机` / `方法详解` / `实验设计` / `核心洞察` / `方法复现指南` / `局限性与未来工作` / `关联研究` / `个人思考`) plus a trailing references block. It is **table-heavy** — vs articles' 6-dimension prose structure.
- **Interpretation frontmatter is sparse**: only 5 fields. No `canonical_concepts`, no `concept_candidates`, no `area`, no `pipeline_run_id`. Papers appear to skip the absorb step entirely.

The new system must model papers as a separate `SourceKind::Paper` with its own interpreter, not a parameterization of the article interpreter.

## Pairing

| Role | Path in legacy vault |
|---|---|
| Raw input | `50-Inbox/03-Processed/2026-05/2026-05-04_arxiv_2601.11144_Deep-GraphRAG-A-Balanced-Approach-to-Hierarchical-Retrieval.md` |
| Interpretation | `70-Archive/2026-05-06_wave3-12/AI-Research/Papers/2026-05-04_2601.11144_260111144 Deep GraphRAG A Balanced Approach to Hie_深度解读.md` |

Pairing key: `arxiv_id` in raw matches the `arXiv:` field in interp.

Note: the interp was found in **archive**, not in the live `20-Areas/AI-Research/Papers/`. This particular case was archived. Other papers (e.g. `2026-05-23_2605.21997 The Log is the Agent`) remain in the live tree. The new system should not treat archived-vs-live as a contract distinction.

## Contract: MUST preserve

- `SourceKind::Paper` as a first-class variant of `SourceBody`. Routing must dispatch papers to a paper-specific interpreter.
- `arxiv_id` round-trips from raw → interp.
- `source_authors` must remain a typed `Vec<Author>`, not flattened to a comma-separated string in the public API. (The legacy interp does flatten it in the rendered Markdown table; that's a rendering detail, not the data model.)
- The 10-section paper structure is the interpreter's output contract. Section names can be renamed (e.g. English vs Chinese) but the set must be present.
- The interp's `date` field is the **paper's publication date**, not the interpretation creation date. (Conflicting convention with articles — articles use interpretation date. New system should pick one and stick to it. **Recommendation: separate `source_date` and `interpreted_at` fields.**)

## Contract: SHOULD preserve

- Tags as a 4-7 keyword list of substantive concepts (not the casual `clippings`/`twitter` tags article raws carry).
- Mermaid / ASCII diagrams in the interp when the paper has architecture diagrams.

## Contract: MAY break

- The fact that papers skip the absorb step. **The new system may run absorb on papers too**, producing `canonical_concepts` and `concept_candidates` — this would be a deliberate improvement, not a regression.
- Archive-vs-live placement. The new system has its own archive policy.
- The non-standard `arXiv:` field name (vs lowercase) — change to `arxiv_id` to match the raw side.

## Open questions

- Why do papers skip absorb? Possibilities:
  - (a) Paper concepts are too dense / too technical to extract evergreens from automatically.
  - (b) Legacy backlog — absorb for papers was never built.
  - (c) Deliberate: papers are read as units, not decomposed into atomic concepts.
- **Recommendation:** treat as (b), enable absorb for papers in the new system, but require human review before the candidates promote to canonical.
