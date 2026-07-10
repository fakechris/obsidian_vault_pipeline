# Concept Map Benchmark

The committed reference for OVP's **concept-map extraction** quality. The
**source article is the ground truth** — these expected maps are curated *from
the articles*, informed (not dictated) by external tools. They are the anchor for
the M13 concept-map rebuild: prompt / parser / resolver / writer changes must be
justified against this benchmark.

## Cases

| fixture | article |
|---|---|
| `rag_wrong/` | "You're doing RAG wrong" (Blockify / IdeaBlocks) |
| `eval_ai_agents/` | "How to Eval AI Agents — The 2026 Guide" |
| `agent_memory_zh/` | "AI Agent 是如何记住东西" (agent memory systems, zh) |

Each `<case>/`:
- `input_path.txt` — absolute path to the source article (the article lives in
  the operator vault, not committed here).
- `expected/concept_map.yaml` — the curated expected map.

## `concept_map.yaml` shape

- `must_have[]` — the concepts a correct run should mint. Each: `id`, `title`,
  `aliases` (acceptable alternative slugs), `concept_type`
  (concept|procedure|claim|taxonomy|system|principle), `expected_meaning` (a
  **concept-specific** definition, never the article thesis), `required_evidence`
  (article quotes/locations), `acceptable_claims` (claims that legitimately
  belong to this concept), `may_merge_with`, `must_not_confuse_with`. Plus
  **content guards** (the hardening that resists fake-green): per concept,
  `definition_must_include_any` / `definition_must_not_include_any` (output-language
  phrases — a correct definition uses this concept's own wording and not a
  confused sibling's signature), `claims_must_include_any` /
  `claims_must_not_include_any`, and `evidence_must_include_any`.
- `must_not_mint[]` — slugs that must NOT become evergreen notes
  (umbrella labels, synonyms that should merge, article/author metadata,
  body-unsupported claims) with a `reason`.
- `forbidden_phrases_anywhere[]` — case-level phrases that must not appear in ANY
  note's definition/claims (author/client metadata; body-unsupported marketing
  numbers, e.g. `40x` for rag-wrong, which is frontmatter-only).
- `coverage` — what the primary note should cover, what should become evergreen,
  and what may stay primary-note-only.
- `known_disagreements_with_nowledge` — where the article (ground truth) says to
  mint/skip something differently than an external reference would.

Curation rule: small and sharp (6-12 concepts), not exhaustive. Prefer fewer
correct concepts over more noisy ones.

## Runner

`scripts/concept_map_bench.py` scores an OVP output directory against these
fixtures with concrete textual checks (no LLM judge, offline):

```
# produce OVP output first, e.g.:
ovp-next run-cycle --manifest manifests/article_evergreen.pipeline.toml \
  --input <article> --vault-root <out>/<case>/ovp/vault --canonical-root <out>/<case>/ovp/canonical ...
# then score it:
python3 scripts/concept_map_bench.py --ovp-root <out>            # all cases
python3 scripts/concept_map_bench.py --ovp-root <out> --case rag_wrong
```

Checks (fact-based, not a vanity score):
1. **must-have coverage** by id/alias (a match via a `must_not_mint` slug is
   reported as `covered_by_forbidden_alias`, NOT clean coverage);
2. **must-not-mint** rejection;
3. **shared-definition** detection (one article one-liner reused across notes);
4. **claim ownership** (a note owns ≥1 claim not shared verbatim elsewhere);
5. **definition correctness** (`definition_must_include_any` /
   `definition_must_not_include_any`);
6. **claim correctness** (`claims_must_include_any` / `claims_must_not_include_any`);
7. **evidence grounding** (`evidence_must_include_any`);
8. **confusion guard** — a concept's definition must not carry a
   `must_not_confuse_with` concept's signature;
9. **forbidden phrases** (`forbidden_phrases_anywhere`).

Exit code is non-zero unless every case passes.

It is **offline but not CI-gated**: scoring needs an OVP output, which needs the
article's model cassette, and live cassettes are not committed. Produce the
output locally (replay or `--client live`) then run the scorer.

## Status

**Red on current `main` (0/3).** The baseline failure map and the
pipeline-challenge analysis live in `.run/m13/` (uncommitted). The benchmark is
expected to go green as the M13 concept-map rebuild lands. Requires `pyyaml`.
