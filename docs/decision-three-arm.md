# Preregistered three-arm result validation

`ovp_evolve::decision_three_arm::validate_and_report` validates frozen inputs and
returns a descriptive report. It does not call providers or automatically accept
an experiment. Store plans, results and reports under `.run/<experiment>/`;
private vault excerpts and labels must not become repository fixtures.

Use one frozen plan per development or holdout run. Freeze it before running any
arm, including the exact input SHA-256, source revision, case IDs, split, labels,
and arm specifications. A minimal anonymous plan has this shape:

The legacy strength sample calls its 30-item development partition `gold`; that
is a historical split name, not a claim of human ground truth. The observation
example explicitly maps `gold` to `development`, records each original split,
and never selects `holdout`. This mapping does not upgrade label provenance.

```json
{
  "experiment_id": "anonymous-strength-holdout-v1",
  "arms": {
    "a": {"provider":"incumbent", "model":"pinned-v1", "prompt_format":"generative_judge", "prompt_version":"judge/v1", "question_version":"strength/v1"},
    "b": {"provider":"incumbent", "model":"pinned-v1", "prompt_format":"typed_no_rationale", "prompt_version":"typed/v1", "question_version":"strength/v1"},
    "c": {"provider":"candidate", "model":"pinned-v2", "prompt_format":"typed_no_rationale", "prompt_version":"typed/v1", "question_version":"strength/v1"}
  },
  "cases": [{
    "case_id":"anonymous-001",
    "input_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "source_revision":"frozen-source-v1",
    "split":"holdout",
    "label":{"kind":"unknown", "provenance":"independent labeling pending"}
  }]
}
```

The digest binds the exact common source input, not each arm's different prompt.
A result repeats `arm`, `case_id`, `input_digest`, `source_revision`, `split`, and
`spec` (the complete registered arm specification). Its `outcome` is an `answer`
with `value`, `abstain`, `failure` with `reason`, or `replay_miss`. It also records
`input_tokens`, `output_tokens`, and `latency_ms`; absent observations are null,
not synthetic zeroes. A silver label has `kind: silver`, `value`, and a durable
`provenance` reference to independent blind labeling evidence. Unknown or disputed
labels remain `unknown` and are excluded from the silver agreement denominator.

A and B must have the same provider and pinned model. B and C must have identical
question and prompt versions. All three arms cover exactly the frozen cases.
Duplicate, missing, unexpected, or mismatched records invalidate the experiment;
execution failures and replay misses also invalidate it. Expected cases remain
in the report, with missing/duplicate/binding-invalid records counted as failures.
Silver agreement uses all silver cases as its denominator, including abstentions
and failures. It is agreement with model-generated labels, never human accuracy.
Abstentions are reported separately and are not negative predictions.

Token totals include only reported observations, accompanied by missing counts.
Latency percentiles use nearest-rank on observed milliseconds; missing counts are
explicit. Cost is not inferred from token counts or invented when unavailable.
Invalid reports are diagnostic artifacts, not evidence for promotion.

A to B measures the combination of typed formatting and removing rationale. B to
C compares suppliers under the shared typed question contract; it does not
isolate architecture, training data or model size. A separate rationale ablation
is required to attribute A to B's change to formatting alone. Report development
and holdout separately; never tune on holdout or treat fixture passes as live
quality evidence. The validator checks declared bindings, not whether a provider
actually obeyed the prompt; retain receipts and replay artifacts for auditing.

## Explicit observation example

The example freezes accepted-unit bindings offline before any provider call:

```sh
cargo run -p ovp-evolve --example strength_three_arm -- freeze ITEMS_JSON READER_DIR NEW_FREEZE_DIR
cargo run -p ovp-evolve --features decision-live --example strength_three_arm -- run NEW_FREEZE_DIR/bundle.json VAULT_DIR PROFILES_JSON NEW_RUN_DIR 2
```

`PROFILES_JSON` contains `incumbent` and `candidate` DecisionProfile objects.
The incumbent uses `provider: chat`; the candidate uses `provider: typesafe`.
Profiles contain credential variable names, never token values. The incumbent
endpoint/model must agree with vault provider configuration. A reuses the actual
`crystal_synth::strength_request` generative judge unchanged except for the
explicit pinned model; B and C share `decision_strength::request`. A and B use
8192 output tokens and no temperature override. A has no retry/cache wrapper;
B and C use explicit live execution and isolated artifact directories.

The freeze command fails on missing or ambiguous accepted-unit matches, writing
private diagnostic evidence without inventing IDs. Every frozen record retains
the original item and split. Run only selects `gold`/`development`; holdout is
never sent. All output directories must be new. Artifacts are written with 0600
permissions, directories with 0700 on Unix. Successful replies preserve full
ModelReply or DecisionReply data, including rationale for A and probability
observations for C. Failure text has known credentials redacted. Provider errors
remain observations in coverage. No implicit Boolean threshold is applied.

This example produces observations, not labels or a quality verdict. To call the
library report, explicitly parse A's strength class and B/C's selected class
into `Outcome::Answer` (or `Abstain`/`Failure`), retaining original receipts;
construct one `ThreeArmPlan` with `Label::Unknown` until blind label evidence is
available and populate each `CaseResult` from the stored bindings/usage/timing.
Then call `validate_and_report(&plan, &results)` and save its report separately.
Do not coerce C's probability-only sufficiency answer to true/false. Transport
success alone is not a valid strength answer or quality success.
