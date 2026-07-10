# Changelog

All notable changes to OVP2 are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Semantic themes (LLM-grouped theme projection over Crystal claims) — in
  progress.
- `ovp2 doctor` now reports Python-era OVP artifacts left in a vault
  (`60-Logs/knowledge.db`, legacy logs, old `.ovp/*.yaml` configs) as
  informational findings with migration guidance.

### Changed

- Relicensed from MIT to dual **MIT OR Apache-2.0** (`LICENSE-MIT`,
  `LICENSE-APACHE`). Vendored IBM Plex fonts remain under the SIL OFL 1.1.

## [0.23.0] - 2026-07-10

First prebuilt `ovp2` release — the Rust rewrite becomes installable without a
Rust toolchain.

### Added

- **Install channels**: prebuilt binaries for macOS (arm64/x64) and Linux
  (x64) via a curl shell installer and a Homebrew tap
  (`brew install fakechris/ovp2/ovp2`), built with cargo-dist; live features
  (`anthropic`, `pinboard-live`, `web-fetch-live`, `github-live`) compiled in,
  runtime behavior still opt-in.
- **Portal v2 (B1–B5)**: the six-destination single-page portal over the read
  model — Today, Library (three-layer source detail), Search (`⌘K`),
  Knowledge (themes, claims, evidence drill-down, scoped graphs), Ask (cited
  Q&A), System — with light/dark themes and full EN/中文 UI.
- **Pinboard first-sync flood guard**: without `--since`/`--max`, a sync that
  would create more than 500 new notes aborts before writing anything.
- **Web/GitHub enrichment verified live**: bare bookmarks are enriched by
  fetching the bookmarked URL (and GitHub metadata for repo links) before the
  reader trunk runs.
- **Full-corpus crystallize**: `ovp2 crystal-synth` run across the real-vault
  corpus end to end — reader packs → cross-source claims → grounded filter →
  strength gate → durable append-only write.

### Notes

- Version lineage: v0.23.0 continues the repository's release numbering;
  v0.22.0 was the last Python-era release, and v2.0.0 is reserved for the
  merge-to-main / Python-retirement milestone.

## Python-era releases (≤ 0.22.0)

History for the Python OVP (v0.1.0 … v0.22.0) lives on the frozen
[`legacy/python-main`](https://github.com/fakechris/obsidian_vault_pipeline/tree/legacy/python-main)
branch and the corresponding `v0.x` git tags; the tag `legacy-python-final`
marks the final Python state. That line is frozen — no further fixes or
releases.
