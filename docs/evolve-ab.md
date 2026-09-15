# Executed retrieval comparisons

`ovp2 evolve ab` executes the existing offline `retrieval-eval` twice. It
compares the ordered source IDs returned by the real VaultTools, rather than
accepting a precomputed scorecard. The initial runner supports only the
`verbatim` / `terms` query-policy experiment, using the same executable.
It does not evaluate translation, answer truth, claim admission, live models,
or arbitrary changes to the knowledge pipeline.

From the repository root:

```bash
ovp2 evolve validate --candidate evolution/candidates/evolve-ab-runtime-v1.json
ovp2 evolve ab --candidate evolution/candidates/evolve-ab-runtime-v1.json --out .run/evolve-ab/demo-001
```

The output directory must be new. The supplied smoke fixture is synthetic and
tests the runner's decisions; it is not evidence of product quality.

The candidate's `eval_plan.paired_run` declares the fixture directory, query
modes, cutoff, expected question count, timeout and minimum mean recall delta
before execution. `paired_sources` counts the fixture's source identities.
The fixture contains `vault/` and `qrels/`; qrels must be unique gold
`ovp.retrieval_eval.qrel/v1` records with explicit class, language, no-answer
flag and source relevance. Claim-only qrels and other runner types fail
explicitly. Fixture symlinks, more than 10,000 files or more than 256 MiB are
unsupported. Curate a bounded fixture instead of pointing at a live vault.

Each arm receives its own byte-identical fixture copy. Its arguments, exit
status, elapsed time, logs, raw report and hashes are retained. The manifest
binds the candidate, registry, input files, executable and checkout SHA/diff.
Source ranks are interleaved in the same tool order as retrieval-eval, then
recall is recomputed from the frozen gold IDs. Per-question results preserve
class/language; a regression on any question or extra negative-source hits
rejects the candidate. Missing observations, unknown source IDs, failed tools,
timeouts and source mutation produce an invalid run, never a zero-filled pass.

An improvement meeting the registered target is `accept`; unchanged results
are `needs_human_review`; a missed target or regression is `reject`.
Completed decisions are appended to the run's isolated
`.ovp/evolution-ledger.jsonl`, with the manifest hash and rollback plan.
This records an experiment, not authorization to promote a production change.
Rejected/invalid runs return a nonzero CLI exit; a completed review-needed run
returns zero but explicitly does not claim improvement.

Token counts are structurally zero because this runner performs no LLM calls.
`accepted_without_quote=0` is marked `not_exercised_read_only_retrieval`;
it must not be used as evidence that the production admission gate passed.
Specs requesting unsupported quote-rate or token-regression guardrails are
rejected. Prompt text and prompt cache namespaces remain untouched.
