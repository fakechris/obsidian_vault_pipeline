# Typed claim-strength observer (INV-646)

The existing Crystal strength judge and deterministic citation/provenance gates
continue to decide durable, caveated, and rejected claims. The typed-decision
observer reads the same `.ovp/decisions.json` profile catalog but accepts only
`strength_check.mode: off` (default) or `shadow`. An `enabled` setting produces
an `unsupported_enabled_mode` trace and does not change admission. The observer
runs after the existing judge has returned complete verdict coverage, before
`write_durable`, in batch and LLM-sweep `crystal-synth` and in
`crystal-review-session-apply`.

`claim_strength_shadow/v1` asks two separate questions per grounded claim:
strength class and whether the cited evidence is sufficient. Each cited quote
must appear within its accepted Unit. The typed request carries attribution,
modality, the exact cited quote, and an `EvidenceRef` whose revision hashes the
accepted Unit quote; its line coordinates refer to that accepted-quote view,
not to physical `reader.md` lines. Claim IDs never enter the model state. The
result maps positional answers back to IDs in Rust. It supplies no generated
rationale and never becomes a `ClaimStrengthVerdict`.

Each shadow trace records the existing verdict, supplier observation, mapped
candidate class/sufficiency, disagreement, and `requires_review: true` per
claim. It is written with private permissions as a unique JSON file in the
run work directory or `.ovp/crystal/decision-observations/` for review apply.
An online `experiment` is reported as `experiment_requires_source_partition`
and makes no decision call, because a batch may cite multiple articles. The
paired quality study must partition by connected source/article groups. Replay
misses, missing quotes, bad namespaces and provider failures are
recorded as fallback; the original Crystal route proceeds. Supplier changes
use the profile and `DecisionClientFactory` boundary from INV-643/644.

The existing fixture proves off/shadow ledger equality and invalid-evidence
fallback. A synthetic typed client proves positional ID mapping and
side-by-side differences. Neither is human ground truth. Source/article-grouped
human labels, defect-class recall, wrong-supported rate, abstention rate,
Chinese/English buckets, calibration, p95 and actual cost are still required
before any adoption recommendation. `evolve validate` checks both single-surface
candidate specs. `evolve ab` cannot run the integration-test path because the
current runner reports `unsupported component or non-retrieval guardrail`
for the runtime candidate and supports only runtime surfaces for the prompt
candidate; no evolution acceptance or
prompt-version promotion is claimed. Any enabled admission change requires a
separate gate candidate and review.
