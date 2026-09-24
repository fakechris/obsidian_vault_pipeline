# Decision experiments (INV-644)

The provider-neutral routing API is `ovp_llm::decision::runtime::evaluate`.
`ovp_app::decisions::load_settings` reads strict JSON operational configuration;
`BuiltinDecisionFactory` assembles TypeSafe live/record/replay or synthetic fixture
replay. A host can supply another `DecisionClientFactory` without changing routing
or the paired runner. Unknown fields, duplicate JSON keys, unsupported suppliers,
unknown profiles and invalid namespaces fail explicitly.

[Example settings](../manifests/decision-settings.example.json) declare two
independent capabilities, both **off**. They are a configuration template for the
INV-645/646 consumers; this delivery does not automatically read this file from
Ask or Crystal, nor alter their production behavior. A consumer loads settings
from its operator configuration path, calls `evaluate`, persists the observation
to its experiment trace, and applies only `observation.applied` when present.
When absent, it keeps the existing baseline. Re-read settings between operations
to make a switch back to off take effect immediately.

| Setting | Meaning |
| --- | --- |
| `mode: off` (default) | No candidate construction, credential resolution or calls. |
| `mode: shadow` | Evaluate candidate-assigned units; return an observation with no applied result. |
| `mode: enabled` | Apply a complete, non-abstaining candidate result for candidate-assigned units. |
| `execution` | `replay` (default), `record`, or `live`, independent of mode. |
| `profile` | Named supplier, endpoint, pinned model, and credential reference. |
| `question_namespace` | Exact version expected on the domain request. |
| `experiment` | Optional ID, seed, and candidate share in basis points (0–10,000). |

An experiment uses a versioned SHA-256 assignment of length-delimited experiment
ID, seed, capability and stable caller-supplied session/query key. Control units
never construct the candidate. Repeated units remain in their arm; changing the
share preserves their bucket. Use a stable unit key across a session, not a new
UUID for every call. The trace records configuration, provider identity, bucket,
arm and a digest of the unit key. It does not log the raw key. A missing experiment
means all units are candidates when the mode is active.

No key, provider failures and invalid replies produce `fallback` observations
with no candidate success or applied result. Explicit abstention retains the
baseline and is recorded separately. A partial abstention prevents application
of the entire batch. These observations do not authorize knowledge admission or
external actions; domain gates and confidence interpretation stay with consumers.

## Executed paired evaluation

From the repository root, after building the CLI:

```sh
cargo build -p ovp-cli
./target/debug/ovp2 evolve validate \
  --candidate evolution/candidates/decision-experiment-runtime-v1.json
./target/debug/ovp2 evolve ab \
  --candidate evolution/candidates/decision-experiment-runtime-v1.json \
  --out .run/decision-experiment/runtime-smoke-001
```

`eval_plan.decision_run` selects the new runner. Existing
`eval_plan.paired_run` retrieval plans retain their behavior; specifying both
is invalid. `experiment_id`, explicit control/candidate profiles, execution
modes, versioned questions, Boolean thresholds, quality targets and budgets are
preregistered in the plan. The runner never randomly assigns an offline arm.
It executes both arms against the same frozen requests in separate directories
and client instances.

The committed fixture is deliberately **synthetic**, with a constructed control
accuracy of 50% and candidate accuracy of 100%. It is a runtime smoke contract,
not a live JEV evaluation or evidence that JEV improves OVP. Cassettes contain
hand-authored answers, not vault captures. Both arms run without credentials or
network access. The smoke run returns `needs_human_review`, never auto-promotes
a model or changes prompt versions.

Each fixture binds source text to SHA-256 revisions and validates evidence line
coordinates, question coverage, gold label types and split. Gold labels are
separate from provider request state. `gold/development` is for development;
`holdout/acceptance` cannot be selected as a tuning plan. This records and enforces
the declared use; it cannot prove a human has never previously inspected a
holdout. Keep holdout curation and access separate operationally.

The manifest preserves candidate and registry digests, code SHA/diff identity,
executable digest, complete plan, input file digests, arm result digests, and
per-question comparisons. Each arm retains a frozen input copy and separate
writable cache. Replay misses and malformed answers make the run **invalid**,
with a nonzero CLI exit. Incorrect but valid predictions are measured as quality
regressions. Abstentions count in the full question denominator. All declared
buckets are reported, including a small-sample flag. No statistical significance
is claimed from small buckets.

Boolean probability thresholds must be explicitly supplied per question in each
arm; there is no implicit 0.5. A discrete Boolean answer needs no threshold.
Choice is scored by option ID, and Score by a preregistered rubric tolerance.
Provider distributions retain their native meaning and do not share calibrated
thresholds across models or question versions.

Metrics include correct/abstained questions, fallback/error counts, observed
network attempts, unknown usage, and current-call p50/p95 latency. Historical
cache/replay tokens are separated from known live-response tokens, with missing
historical usage counted explicitly. A retry can have unreported billable usage;
unknown live usage invalidates budget verification. No currency prices or costs
are invented. A replay latency describes replay, not provider latency.

Budgets bound request count and check elapsed time before/after calls and observed
live tokens after calls. Once exceeded, no further calls occur in that arm. A
single in-flight request can exceed the remaining arm budget; the built-in HTTP
client has its own total timeout. These are measured experiment limits, not a
provider-enforced prepaid dollar cap. Unknown/failed usage is not treated as free.

This runner currently admits the **runtime decision boundary** surface. Model,
prompt, admission-policy promotion and their acceptance gates remain separate
candidates. Read-only decision evaluation records `accepted_without_quote: null`
and `admission_gate_scope: not_exercised`. Completed passing runs request human
review; failed quality targets reject. Invalid runs retain error evidence and a
`needs_human_review` ledger entry without claiming acceptance.

All artifacts go into a new `.run/...` or `.ovp/...` directory; an existing output
is never overwritten. Candidate promotion is separate from adding this runner.
To use live/record execution, build with `--features decision-live` and explicitly
supply the credential environment variable named by the profile. Changing
execution to live alone does not enable any production capability.
