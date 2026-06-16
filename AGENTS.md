# AGENTS.md - OVP Next Rust Trunk

This branch is the Rust OVP Next trunk. The repository root is the Rust workspace root.

## Source Of Truth

- Build and architecture decisions must start from the Rust crates under `crates/`.
- `Cargo.toml`, `Cargo.lock`, `crates/`, `fixtures/`, `manifests/`, `scripts/`, and Rust-focused `docs/` are the current implementation surface.
- The legacy Python implementation has been removed from this branch's tracked working tree.
- Do not infer current architecture from Python-era files, old vault templates, or legacy product docs.
- Do not reintroduce a Python pipeline, Python package metadata, or Python CLI wrappers unless the user explicitly requests a migration helper script.

## Repository Shape

Expected root layout:

```text
Cargo.toml
Cargo.lock
crates/
docs/
fixtures/
manifests/
scripts/
README.md
.gitignore
AGENTS.md
```

There should be no nested `rust/ovp-next/` project and no root `src/ovp_pipeline/`, `pyproject.toml`, `requirements.txt`, or `MANIFEST.in`.

## Development Commands

Run from the repository root:

```bash
cargo metadata --no-deps --format-version 1
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
bash scripts/check_architecture.sh
```

Use `rg` for searches. Prefer `git mv` and `git rm` for structural edits so migrations remain auditable.

## Architecture Boundaries

- `ovp-core` owns the sync kernel primitives.
- `ovp-domain` owns domain types, transforms, prompts, fixtures, and contract parsing.
- `ovp-app` owns manifest-based graph assembly.
- `ovp-run` owns the operational run cycle.
- `ovp-query`, `ovp-lint`, `ovp-rag`, `ovp-auto`, `ovp-eval`, and `ovp-review` are read, health, automation, comparison, and review layers around the run cycle.
- `ovp-cli` is a thin argument-parsing layer that delegates into the crates above.

Derived state must stay rebuildable from Rust code and committed fixtures/contracts. Historical legacy alignment docs can be used as behavioral context, not as runtime authority.

## Data Hygiene

Do not commit local scratch, live outputs, credentials, or raw evaluation data:

- `.run/`
- `.env*`
- local tool state such as `.agents/`, `.gstack/`, `.supervisor/`, `.worktrees/`
- untracked `docs/eval/`, `docs/superpowers/`, or legacy report scratch
- raw/live cassette captures unless the user explicitly asks to curate and commit them

Committed replay cassettes under crate tests are acceptable only when they are intentional frozen fixtures.

## OVP Evolution Rules

When modifying LLM prompt templates or pipeline behavior:

1. Write a Candidate Spec (`evolution/candidates/<id>.json`) BEFORE editing
2. Identify the target Change Surface: prompt | parser | runtime | gate | model
3. State a falsifiable hypothesis with predicted metric delta
4. Run `ovp-next evolve validate --candidate <spec>` to confirm spec validity
5. Run `ovp-next evolve ab --candidate <spec>` to generate paired comparison (Phase 2+)
6. Accept only if hard gates pass and guardrails hold
7. Record in Evolution Ledger with git SHA and rollback plan
8. Bump the prompt namespace version constant after acceptance

Rules:
- One surface per candidate (no mixing prompt + parser changes)
- Hard gate: accepted_without_quote must remain 0
- Cassette replay is the source of truth for regression
- Prompt versions are monotonically increasing; never reuse a version number
- The `ovp-evolve` crate owns all evolution governance logic
- `evolution/components.json` is the component registry (validated at load time)
- `.ovp/evolution-ledger.jsonl` is the append-only decision record
