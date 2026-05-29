# Live LLM + cassette capture (C9/C10)

The default build is **replay-only**: `cargo test` and `ovp-next interpret-article`
read committed cassettes and never touch the network. This doc covers the
*live* path used to capture new cassettes against the real Anthropic API.

## When you need it

- Recording a fresh cassette for a new fixture.
- Re-recording after a prompt asset / `schema_version` bump (the cassette
  namespace changes, so old cassettes no longer match).
- Sanity-checking a prompt against the real model.

## Prerequisites

- `ANTHROPIC_API_KEY` set in the environment.
- Build with the `anthropic` feature (pulls in `reqwest`; the default
  build does not).

## The capture command

```sh
ANTHROPIC_API_KEY=sk-... \
cargo run -p ovp-cli --features anthropic -- interpret-article \
  --input fixtures/article_clean/input.md \
  --client live \
  --cache-dir crates/ovp-domain/tests/cassettes \
  --out .run/article \
  --area ai \
  --date 2026-05-04
```

What happens:

1. `--client live` wires `CachedModelClient(AnthropicBlockingClient, Record)`.
2. On a cache **miss** for the request, it calls the live API and writes the
   reply to `crates/ovp-domain/tests/cassettes/article_interpret/v1/<sha256>.json`.
3. On a cache **hit**, it replays — so re-running the command does not spend
   another API call.

The cassette filename is the SHA-256 of the provider-neutral `ModelRequest`,
namespaced by `article_interpret/v1` (the prompt id + schema version). Commit
the resulting `.json` so the offline gauntlet can replay it.

## Verifying a captured cassette

After capture, the default offline path must reproduce the same result:

```sh
cargo run -p ovp-cli -- interpret-article \
  --input fixtures/article_clean/input.md \
  --cache-dir crates/ovp-domain/tests/cassettes \
  --out .run/article
# (no --features anthropic, no key — replay-only)
```

and the acceptance test must stay green:

```sh
cargo test -p ovp-domain --test article_clean
```

## Behavior without the feature or without a key

- Default build, `--client live`: errors immediately with a rebuild hint.
  No network, no partial state.
- `--features anthropic` build, `--client live`, no `ANTHROPIC_API_KEY`:
  errors at `AnthropicBlockingClient::from_env()` with `no_api_key`. No
  network call is attempted.

Both are exercised in the test/dev loop, so the live path is validated up to
the execution boundary even in environments with no key.

## Invariant notes

- `AnthropicBlockingClient` uses `reqwest::blocking` — synchronous. No async
  runtime is introduced; invariant #6 (no async in `ovp-core`, async never
  leaks into the pipeline) holds.
- The request/response mapping (`anthropic_request_body`,
  `parse_anthropic_reply`) is pure and tested in the default offline gauntlet
  with no `reqwest` and no network.
