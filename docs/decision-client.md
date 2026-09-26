# Typed decision boundary (INV-643)

`ovp_llm::decision::DecisionClient` is a synchronous, provider-neutral effect
boundary alongside the generative `ModelClient`. This delivery adds the library
and tests only. No Ask, Crystal, scheduler, CLI or server path constructs it;
production behavior and prompts are unchanged. No JEV quality or cost improvement
has been measured by these synthetic contract tests.

## Domain and provider responsibilities

Domain code supplies a versioned `DecisionRequest` with named state, questions and
versioned `EvidenceRef`s. Questions express a boolean condition, a choice among
stable option ids, or a score over ordered, described levels. Include a no-match
choice when appropriate. Instructions must contain the full question: question
ids are bookkeeping, and TypeSafe does not use them as instructions.

The adapter maps these questions onto its wire protocol. The pure TypeSafe adapter
is always built; HTTP requires the **`decision-live`** Cargo feature. It calls the
profile's full evaluation endpoint and requires a pinned `jev-x.y.z` model. The
returned model must match. Moving aliases are deliberately rejected.

Profiles configure `id`, `provider`, `endpoint`, `model`, and `credential_ref`.
The latter is an environment-variable name; it never contains the secret. A host
can resolve its own secret store and use `with_api_key` instead. Credentials never
enter serialized requests, cassettes or diagnostics. Use distinct profile ids for
different accounts/tenants. Rotation of a credential reference does not invalidate
the cache. Changing provider, endpoint, pinned model or profile id does.

Implement another `DecisionClient` to change suppliers. It advertises supported
question types and batching. A provider without probabilities may return discrete
boolean/choice answers or rubric scores; absent probabilities, usage and
confidence remain absent. `Calibration::ProviderClaimed` records a vendor claim,
not calibration on OVP data. The contract test includes a second label-only
implementation consumed through the same trait.

## Interpretation and failure behavior

- TypeSafe boolean answers retain `probability_true` and leave `value` absent.
  The adapter does not impose a 0.5 acceptance threshold.
- Choice distributions compete across the supplied options. Score positions are
  probability-weighted zero-based rubric levels, not measured numeric quantities.
- Confidence retains its provider-specific semantics; never reuse a threshold
  across providers, models or question versions without evaluation.
- An explicit `Abstain` is a completed uncertain judgment, not a transport error.
  Callers decide whether to review, retain baseline behavior or escalate.
- Missing/extra/duplicate answers, mismatched types/models, illegal options,
  incomplete distributions, nonfinite values and invalid score legends fail the
  whole request. Partial success is not returned.
- HTTP uses one total deadline, bounded retries (429/5xx/transport only), bounded
  response size and no redirects. Numeric Retry-After is respected within that
  deadline; an HTTP-date header is conservatively returned as an HTTP failure.
  No implicit provider switch occurs. Remote plaintext HTTP is rejected; local
  loopback mock servers require explicit opt-in.

The same-request questions are independent. Questions that require earlier
answers or new evidence need a later request. Neither an answer nor confidence
authorizes a tool action or knowledge admission; deterministic quote gates remain
outside this library.

## Record and replay

`CachedDecisionClient::record(Box<dyn DecisionClient>, directory)` validates and
records a successful result once. `CachedDecisionClient::replay(profile,
capabilities, directory)` owns **no live client** and needs no key. Missing or
corrupt cassettes fail explicitly; corruption is not replaced by a new live call.
`FixtureDecisionClient` supports in-memory synthetic replies.

The SHA-256 key binds the wire-contract version, provider identity, question
namespace, exact question definitions, state and evidence revisions/coordinates.
JSON keys are sorted recursively, independent of serde_json feature unification.
The receipt retains the actual model, question namespace, evidence references,
request digest and uncertainty provenance. Inputs can be reconstructed only from
the matching versioned evidence and question contract; the cassette deliberately
does not duplicate raw source state. A paired evaluation must retain those frozen
inputs in its manifest/fixture separately.

Cassettes are written atomically with mode 0600 on Unix, using a temporary file
in the same directory. Existing recordings are never overwritten. A concurrent
recording race returns `CacheConflict` to the loser rather than a successful but
unreplayable result. Supply `.run/<milestone>/` or the operator vault's `.ovp/`
as the directory; never `/tmp` or a tracked live-data directory.

`evaluation_usage` and `evaluation_ms` describe the original evaluation.
`origin` and `network_attempts` describe the current call: fixture/cache/replay
attempts are zero. Do not charge the historical tokens again on replay. Retry
attempts may have unknown billable usage; usage in the successful response is
not proof that failed attempts were free. This library does not invent costs or
prices. A surrounding experiment must measure current wall time and report
failed/unknown usage separately.

## Verification and integration boundary

```sh
cargo test -p ovp-llm
cargo test -p ovp-llm --features decision-live
cargo clippy -p ovp-llm --all-targets --features decision-live -- -D warnings
```

Set `TMPDIR` to a gitignored repository `.run/.../tmp` directory before running
tests that create temporary files. Tests never require a paid API call.

The preregistered `decision-boundary-v1` candidate is a **runtime library**
candidate. `evolve validate` checks its shape. The existing `evolve ab` runner
supports only retrieval query-policy comparisons and cannot evaluate this
boundary: invoking it for this candidate fails with an unsupported component or
non-retrieval guardrail error. That is a recorded limitation, not an accepted model experiment. There is
no prompt namespace bump or automatic production promotion in this delivery.

INV-644 owns capability-scoped `off / shadow / enabled`, experiment assignment,
provider profiles wired to operational configuration, and executable decision
A/B support. INV-645 and INV-646 own the actual retrieval and strength consumers.
These features are not claimed as implemented by the library alone.

Protocol sources checked 2026-09-22:
[official API](https://docs.typesafe.ai/api),
[models](https://docs.typesafe.ai/models),
[confidence](https://docs.typesafe.ai/confidence).

The follow-up INV-644 implementation is documented in
[decision-experiments.md](decision-experiments.md): reusable capability routing,
strict operational settings, stable assignment and an executable typed-decision
paired runner. Production retrieval/strength consumers remain INV-645/646.
