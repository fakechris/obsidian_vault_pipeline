# Changelog

All notable changes to OVP2 are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `ovp2 schedule <install|uninstall|status>` — productized OS scheduler for
  the daily loop (launchd user agent on macOS, systemd user timer on Linux).
  `install` writes the unit file(s) + a chmod-600 env-file template
  (`<vault>/.ovp/daily.env`) and loads the job; `status` reports
  loaded/enabled state, schedule, env file, last log lines, and warns when
  the last daily run is more than 2 days old; `uninstall` removes the job
  and keeps logs + env file. No daemon (M32 §9) — the OS owns the clock.

## [2.0.1] - 2026-07-10

### Fixed
- Release builds: Linux artifacts build on `ubuntu-24.04` (ort's prebuilt ONNX
  Runtime needs glibc >= 2.38); the Intel-mac (`x86_64-apple-darwin`) target is
  dropped from the prebuilt matrix (ort ships no ONNX prebuilts for it —
  operator decision: unsupported; build from source without `embed`).

## [2.0.0] - 2026-07-10

**OVP2 replaces OVP.** This release marks the merge of the Rust rewrite to
`main` and the retirement of the Python pipeline. See
[docs/ovp-to-ovp2.md](docs/ovp-to-ovp2.md) for the full story, key decisions,
and migration guide. The final Python line is preserved frozen on the
`legacy/python-main` branch (tag `legacy-python-final`).

### Added
- Semantic theme system: local multilingual embeddings (fastembed, pinned
  paraphrase-multilingual-MiniLM) + deterministic Louvain communities +
  c-TF-IDF keywords + optional LLM bilingual naming — replaces the hardcoded
  keyword buckets; themes are a rebuildable projection (`.ovp/crystal/themes.json`).
- `ovp2 crystal-themes` command; `embed` cargo feature (shipped in prebuilt
  binaries); one-time model download with offline degradation to Unclassified.
- `ovp2 doctor` legacy-artifact check (Python-era files reported as INFO with
  migration guidance).
- Dual license (MIT OR Apache-2.0), privacy & trust documentation, issue
  templates, downgrade/rollback instructions.

### Changed
- Crystal-synth batching groups by semantic community (date-ordered fallback);
  the 8 hardcoded keyword buckets are deleted.
- Review-session defer triggers key on stable community identity, never
  display labels.
- MSRV raised to 1.88 (embed dependency tree).

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
