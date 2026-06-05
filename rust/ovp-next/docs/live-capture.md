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

## Live provider config (Anthropic-compatible providers)

The live client defaults to the real Anthropic Messages endpoint with a
`claude-*` model. To target an Anthropic-**compatible** provider (e.g. MiniMax),
set these environment variables alongside the key. They are parsed and
**validated at startup** — a var that is present but invalid is a hard error,
never silently ignored.

| Variable | Meaning |
|---|---|
| `ANTHROPIC_API_KEY` | API key (required for `--client live`). |
| `ANTHROPIC_BASE_URL` | The **full Messages endpoint URL**, not a root URL — e.g. `https://api.minimaxi.com/anthropic/v1/messages`. Absent → `https://api.anthropic.com/v1/messages`. |
| `OVP_LLM_MODEL` | Provider model name. A `claude-*` model won't work on a non-Anthropic provider, so set this (e.g. `MiniMax-M2`). Absent → the domain default. |
| `OVP_LLM_MAX_TOKENS` | Positive integer wire `max_tokens` override. **Reasoning/thinking models** (e.g. MiniMax-M2) spend their budget on `thinking` blocks; the domain default (4096) can be exhausted before any `text` block is emitted, which fails as `no_text_content_blocks`. Raise it (e.g. `24000`). |
| `OVP_LLM_NO_PROXY` | Boolean (`1`/`0`/`true`/`false`). Bypass the ambient `HTTP(S)_PROXY` for the live HTTP client **only** — useful when the directly-reachable provider can't be tunneled through an authenticated proxy. Does not mutate the process environment. |

Example (MiniMax, via a sourced `.env.live` — never commit secret env files):

```sh
# .env.live  (gitignored)
ANTHROPIC_API_KEY=...
ANTHROPIC_BASE_URL=https://api.minimaxi.com/anthropic/v1/messages
OVP_LLM_MODEL=MiniMax-M2
OVP_LLM_MAX_TOKENS=24000
OVP_LLM_NO_PROXY=1

set -a && . ./.env.live && set +a
cargo run -p ovp-cli --features anthropic -- run-cycle --client live ...
```

## Failure & retry behavior

Live extraction failures are **loud**, not silent empty successes:

- A failed LLM call (transport error, provider error, or a `no_text_content_blocks`
  decode) makes the record `Error` in the run. `run-cycle` / `interpret-article` /
  `review-run` then exit **non-zero** — a `0 concept / 0 claim` empty note is
  never reported as success. (A secondary guard also fails any non-dry-run that
  saw input but produced zero write ops.)
- **Transient** faults (transport timeout/connect/reset, HTTP 429, HTTP 5xx) are
  retried a small, bounded number of times with a short backoff before failing.
  Non-transient faults (4xx, decode/parse, `no_api_key`, invalid request) fail
  immediately — no wasted calls. Cache semantics are preserved: a cache **hit**
  never retries, and only a finally-successful live call records a cassette; a
  failed call writes no cassette.

## Invariant notes

- `AnthropicBlockingClient` uses `reqwest::blocking` — synchronous. No async
  runtime is introduced; invariant #6 (no async in `ovp-core`, async never
  leaks into the pipeline) holds.
- The request/response mapping (`anthropic_request_body`,
  `parse_anthropic_reply`) is pure and tested in the default offline gauntlet
  with no `reqwest` and no network. `RetryingModelClient` and `LiveClientConfig`
  parsing are likewise pure and unit-tested without the network.
