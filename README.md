# OVP Next

Clean-core Rust rewrite of the Obsidian Vault Pipeline. **Status: v0.1 (core validation only).**

This repo intentionally has zero dependency on the legacy Python `ovp_pipeline` package — no import, no subprocess, no embedded runtime. The old system is a frozen oracle for fixtures and contracts, not a runtime dependency.

## v0.1 scope

Build the smallest core that proves the type design:

- `Record` / `RecordBody` envelope
- `Filter` traits + `FilterDecision`
- `WritePlan` / `WriteOp`
- `Event` log
- TOML pipeline manifest
- In-memory `GraphRunner`
- One end-to-end fake test

No LLM, no Markdown, no vault writes. After the integration test passes there is a 48-hour calibration checkpoint (`docs/calibration-r1.md`) before continuing.

See `docs/architecture.md` and `docs/invariants.md`.
