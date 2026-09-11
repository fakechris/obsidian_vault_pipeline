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

There should be no nested `rust/ovp2/` project and no root `src/ovp_pipeline/`, `pyproject.toml`, `requirements.txt`, or `MANIFEST.in`.

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

NEVER write intermediate or run artifacts (live-run outputs, cassettes, stores, work dirs) to `/tmp` — it is wiped on reboot and the evidence is lost. Put them under the gitignored `.run/<milestone>/` in this repo (or the vault's `.ovp/` when they belong to the operator vault) so they survive and stay auditable.

## OVP Evolution Rules

When modifying LLM prompt templates or pipeline behavior:

1. Write a Candidate Spec (`evolution/candidates/<id>.json`) BEFORE editing
2. Identify the target Change Surface: prompt | parser | runtime | gate | model
3. State a falsifiable hypothesis with predicted metric delta
4. Run `ovp2 evolve validate --candidate <spec>` to confirm spec validity
5. Run `ovp2 evolve ab --candidate <spec>` to generate paired comparison (Phase 2+)
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

## Involute Work-Graph Protocol

This repository is bound to the **Involute Work-Graph Kernel** for task tracking, milestone delivery, and auditable agent collaboration.

### 1. Project Binding

- **Repository**: `fakechris/obsidian_vault_pipeline`
- **Team Key**: `INV`
- **Root Project Identifier**: `INV-44`
- **Root Project UUID**: `80a49a7a-8c38-4760-b563-d804fb07cd30`
- **Web UI**: [http://100.114.30.43:4201/](http://100.114.30.43:4201/)
- **Candidate Review Queue**: [http://100.114.30.43:4201/candidates?project=fakechris/obsidian_vault_pipeline](http://100.114.30.43:4201/candidates?project=fakechris/obsidian_vault_pipeline)
- **Work Graph Observation**: [http://100.114.30.43:4201/graph](http://100.114.30.43:4201/graph)

### 2. MCP Connection Configuration

| Agent Environment | MCP Endpoint URL | Notes |
|---|---|---|
| **Remote / Developer Machine** (Mac, Cursor, Codex, Claude Code) | `http://100.114.30.43:4200/mcp` (or `/mcp/readonly`) | Connect via Tailscale network |
| **Local on Box** (executing inside VPS host) | `http://127.0.0.1:4200/mcp` (or `/mcp/readonly`) | Loopback connection on host |

- **Auth Header**: `Authorization: Bearer <AGENT_TOKEN>` (`inv_agent_...` minted via Settings → Agents).
- **Security Rule**: Tokens stay in agent secret store; **NEVER commit tokens or write them to repository files**.

### 3. Agent Operational Rules

1. **Search Before Propose**: Always run `work_search` to prevent duplicate nodes.
2. **Propose, Never Unilaterally Commit**: Agents propose candidates via `work_propose`; humans commit via Web UI or CLI.
3. **Claim-Driven Execution**: Run `work_claim` to lease a task before writing code.
4. **Report Runs with Evidence**: Record progress via `run_report`. On completion, attach durable evidence (commit SHA, test exit code, PR URL) via `evidence_attach`.
5. **In Review, Never Done**: Agents transition tasks to `In Review`. Moving work to `Done` is strictly reserved for human review or verified `CLEAR` auto-accept gate.

