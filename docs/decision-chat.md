# Same-model typed decision control

The opt-in `chat` supplier adapts an existing `ModelClient` to `DecisionClient`.
It batches Boolean, Choice and discrete Score questions into one answer-only
request. Explicit abstention is supported. It neither generates reasons nor
manufactures probabilities, confidence or calibration. Malformed, duplicate,
missing, extra, truncated and wrong-model answers fail the entire batch.

`decision_chat/v2` fixes the adapter protocol, 8192 maximum output tokens and
the provider-default temperature, matching the existing strength judge's
request settings. The domain question namespace remains separate. Both versions
enter the decision cassette key; rotating credentials does not change that key.
Changing adapter instructions or generation parameters requires a new protocol
version and a separate evolution candidate.

The initial local v1 trial used a different generation budget/temperature and
must not be pooled with v2 or treated as a matched A/B comparison.

An experiment profile contains the actual Anthropic-compatible Messages endpoint
and model, for example (not a production recommendation):

```json
{
  "id": "incumbent-control-v1",
  "provider": "chat",
  "endpoint": "https://provider.example/v1/messages",
  "model": "explicit-model-version",
  "credential_ref": "ANTHROPIC_API_KEY"
}
```

`ProvidersDecisionFactory::new(vault)` resolves credentials from the existing
`.ovp/providers.toml` `[env]` table, with process environment taking precedence.
Configured model and endpoint must match the experiment profile exactly;
an explicit token budget must match the protocol budget. No global environment
mutation, implicit supplier change or new credential file is involved.
`BuiltinDecisionFactory` also supports `chat` using process environment (the CLI
already loads providers.toml at startup when given `--vault-root`). Live/record
requires `decision-live`; replay has no transport or credential dependency.

Live evaluation performs one bounded HTTP request with redirects disabled.
Record wraps the validated reply in the existing private, atomic decision cache.
Replay never constructs a live client, even if providers.toml is missing or
broken. Usage in a replay receipt describes the historical evaluation; network
attempts for replay/cache are zero. Provider response model must match exactly;
a provider exposing only a moving alias still lacks independently verified model
revision provenance and must be reported as such.

The interactive `SearchDecisionFactory` deliberately rejects this experimental
supplier: its latency has not qualified for Ask. Existing defaults remain off;
this change does not replace the Crystal judge or alter admission.

See [three-arm validation](decision-three-arm.md) for independent input binding,
complete coverage, unknown labels and descriptive reporting. A to B measures
typed formatting **plus removal of reasons** together. Isolating the reason
effect alone requires an additional same-model typed-with-reasons arm.
