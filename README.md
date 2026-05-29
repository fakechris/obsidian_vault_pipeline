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
- `docs/legacy-alignment.md` — living gap matrix between this rewrite and the legacy Python OVP. Read before scoping any new stage.
- `docs/live-capture.md` — how to make live Anthropic calls + capture cassettes (`--features anthropic`, `--client live`).
- `docs/invariants.md` — the 12 invariants; CI-gated where possible.
- `docs/stage-c.md`, `docs/stage-d-plan-applier.md` — historical stage docs.
- `docs/calibration-r1.md`, `docs/calibration-r2.md` — historical calibration verdicts.
- `fixtures/` — frozen contracts captured from the legacy system.

## Next

Driven by the legacy alignment baseline (`docs/legacy-alignment.md`). Order:

1. C9 + C10 — live `AnthropicBlockingClient` + real cassette capture.
2. **L0/L1 intake + VaultLayout** *(new)* — first real Source filters; Inbox + Clippings + GitHub raws.
3. v1.2 — paper deep-dive transform (now slotted after intake).
4. **L3 absorb + ConceptRegistry** *(new)* — the highest-cognitive-load legacy step; surfaces the canonical write surface.
5. Canonical store — `CanonicalUpsert` becomes real with absorb as its producer.
6. **L4/L5 MOC + knowledge index + TxnFsApplier** *(new)* — closes the first end-to-end cycle (raw → Evergreen → MOC → knowledge.db).

See `docs/architecture.md` "What comes next" and `docs/legacy-alignment.md` for rationale.
