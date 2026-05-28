# OVP Next

Clean-core Rust rewrite of the Obsidian Vault Pipeline. **Status: post-Stage D — article path real on disk; live LLM + paper coverage next.**

This repo intentionally has zero dependency on the legacy Python `ovp_pipeline` package — no import, no subprocess, no embedded runtime. The old system is a frozen oracle for fixtures and contracts, not a runtime dependency.

## What works today

Five crates, ~5000 LOC, 97 tests. Two acceptance fixtures (`article_clean`, `article_mixed_lang`) run through the full pipeline offline against committed cassettes; the resulting `WritePlan` is applied to a tempdir vault and the round-trip fields match.

```
ovp-next interpret-article \
  --input fixtures/article_clean/input.md \
  --out .run/article \
  --cache-dir crates/ovp-domain/tests/cassettes

ovp-next apply-plan \
  --plan .run/article/plans/demo-article.json \
  --vault-root .run/vault
```

→ `.run/vault/20-Areas/AI-Research/Topics/<YYYY-MM>/<YYYY-MM-DD>_<title>_深度解读.md` lands on disk.

## Crates

| Crate | Role |
|---|---|
| `ovp-core` | Sync kernel: `Record<B>`, `Filter` traits, `GraphRunner`, `WritePlan`, `Event`, `PlanApplier` trait. Knows nothing about Obsidian / LLM / HTTP. |
| `ovp-domain` | v1 article path: `DomainBody`, `SourceDoc` / `PromptRequest` / `ModelResponse` / `InterpretedDoc`, six transforms. |
| `ovp-llm` | `ModelClient` trait + Fixture / Cached / NeverCalls impls. `AnthropicBlockingClient` lands in C9 behind `--features anthropic`. |
| `ovp-stores` | `PlanApplier` impls. Today: `VaultFsPlanApplier`. |
| `ovp-cli` | Wiring + subcommands: `interpret-article`, `apply-plan`, `graph`. |

## Docs

- `docs/architecture.md` — current authoritative architecture + system primitives + crate responsibilities + deprecated vocabulary.
- `docs/invariants.md` — the 12 invariants; CI-gated where possible.
- `docs/stage-c.md`, `docs/stage-d-plan-applier.md` — historical stage docs.
- `docs/calibration-r1.md`, `docs/calibration-r2.md` — historical calibration verdicts.
- `fixtures/` — frozen contracts captured from the legacy system.

## Next

1. Codex review of Stage D + the consolidated architecture doc.
2. C9 + C10 — live `AnthropicBlockingClient` + real cassette capture.
3. v1.2 (paper) — introduces source-kind routing.
4. Canonical store — a sibling `PlanApplier` impl.

See `docs/architecture.md` "What comes next" for rationale.
