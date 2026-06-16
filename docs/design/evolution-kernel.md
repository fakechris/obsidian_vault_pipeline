# OVP Evolution Kernel

## Purpose

The Evolution Kernel provides a lightweight governance layer for all
prompt, parser, runtime, gate, and model changes in OVP. It ensures every
behavioral change is:

- **Attributable** — linked to a specific component and change surface
- **Hypothesis-driven** — predicted metric impact stated before the change
- **A/B validated** — paired comparison on the same source corpus
- **Rollbackable** — recorded with revert instructions
- **Accumulated** — lessons and decisions written to a durable ledger

## Why Not Copy TurnkeyAI's Full Framework

TurnkeyAI is a multi-agent system with hundreds of optimizable surfaces
requiring MIPROv2/GEPA/SkillOpt routing. OVP is a linear Rust pipeline with
~8 prompt namespaces and deterministic quality gates. The full optimizer
routing layer adds complexity without proportional value.

OVP already has strong primitives:

| Existing Capability | How It Maps |
|---|---|
| Cassette record/replay | Natural A/B replay substrate |
| `ValidationReport.quote_found_rate` | Hard deterministic gate |
| `crystal-lint` provenance scoring | Deterministic acceptance |
| `daily-runs.jsonl` ledger | Operational attempt history |
| Prompt version IDs (`unit_extract/v5`) | Namespace isolation |

## Change Surfaces

Every evolution candidate targets exactly one surface:

| Surface | Examples |
|---|---|
| `prompt` | unit_extraction.md, card_synthesis.md template edits |
| `parser` | JSON repair logic, unit validator rules |
| `runtime` | Web fetcher, timeout, retry policy, proxy config |
| `gate` | crystal-lint thresholds, ValidationReport floor |
| `model` | Provider or model string change |

Mixing surfaces in one candidate is rejected unless `ablation_required` is
set (indicating a deliberate combined change with ablation testing).

## Quality Buckets

| Bucket | Key Metrics | Source |
|---|---|---|
| `extraction_fidelity` | quote_found_rate, accepted_without_quote | ValidationReport |
| `coverage` | accepted_units count | run-status.json |
| `card_quality` | cards_kept / cards_dropped_uncited | CardReport |
| `crystal_provenance` | durable / caveated ratio | crystal-lint |
| `operational_reliability` | success_rate, timeout_rate | daily-runs.jsonl |
| `cost_efficiency` | input_tokens + output_tokens | cassette Usage |

## Component Registry

File: `evolution/components.json`

Every optimizable artifact is registered with its surface, file path,
target quality buckets, and regression fixture set. The CLI validates the
registry on load and rejects unknown component references in candidates.

## Candidate Lifecycle

```text
1. Write candidate spec       →  evolution/candidates/<id>.json
2. Validate spec              →  ovp-next evolve validate <spec>
3. Run paired A/B             →  ovp-next evolve ab --candidate <spec>
4. Review scorecard           →  hard gates + guardrails + primary metrics
5. Accept / Reject            →  human decision
6. Record in ledger           →  .ovp/evolution-ledger.jsonl
7. Bump version constant      →  code commit with git SHA
```

## Paired A/B Mechanism

The A/B runner leverages OVP's cassette infrastructure:

- **Control arm**: Current prompt version with `ReplayOnly` cassettes
- **Candidate arm**: New prompt version with `Record` mode (live LLM)
- **Comparison**: Per-source ValidationReport / CardReport metrics

Both arms process the same fixture source set. The scorecard aggregates
per-source deltas into an accept/reject decision.

## Scorecard Decision Logic

Hard gates (must all pass):
- `accepted_without_quote == 0` in candidate
- No parse errors introduced
- No new `silent_failure` states

Primary gates (target improvement):
- Target bucket metrics improve vs control

Guardrails (no regression):
- Non-target buckets stay within threshold
- Token usage regression bounded (default 1.3x)
- Latency regression bounded

## Root-Cause Cards

When `daily` produces failures or quality degradation, the system generates
a structured root-cause card attributing the issue to a specific surface.
This prevents prompt-patching runtime bugs.

Diagnosis rules (deterministic, no LLM):
- parse_error + json_repaired → suspected surface: parser
- timeout / network error → suspected surface: runtime
- quote_found_rate drop → suspected surface: prompt or model
- content_moderation block → suspected surface: runtime (provider)

## Evolution Ledger

Append-only JSONL at `.ovp/evolution-ledger.jsonl`. Each entry records:
- Candidate ID and git SHA
- Decision (accept/reject/ablation_needed)
- Scorecard summary
- Rollback instructions
- Lessons learned

## Relationship to Existing Systems

- `daily-runs.jsonl`: Operational attempt ledger (unchanged)
- `evolution-ledger.jsonl`: Strategic change decision ledger (new)
- Cassettes: Replay substrate for A/B (unchanged, new usage pattern)
- `ovp-review`: Fixture contract comparison (complementary)
- `ovp-eval`: Cross-system comparison (orthogonal)

## Future Extensions (Phase 3+)

- Console integration: evolution history in HTML dashboard
- Weekly digest report: aggregate quality trends
- LLM-assisted diagnosis: auto-generate root-cause cards from cassettes
- Prompt suggestion: LLM proposes improvements (never auto-patches)
