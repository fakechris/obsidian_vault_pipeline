# Changelog

All notable changes to OVP2 are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- needs-content captures no longer retry forever: a capture whose enrichment
  fetch failed 3 times (`MAX_ENRICH_ATTEMPTS`) or for 72h since the first
  failure is closed as `IntakeAction::ContentUnavailable` — terminal and
  hash-keyed like `ovp/skip`, so editing the file re-enters intake. Attempts
  are counted in the new append-only `.ovp/enrich-attempts.jsonl`; the daily
  summary and run report (`intake.content_unavailable`) show how many are
  closed; `ovp2 doctor` lists them (`content-unavailable`, INFO) with the last
  error; `ovp2 daily --retry-unavailable` reopens the set for one run.
  **Compatibility:** `content_unavailable` is a new intake-ledger variant and
  `read_jsonl` fails the whole ledger on one unknown line — install the new
  sidecar BEFORE any run that can write it; an older binary reading such a
  ledger errors on every intake/daily/index run.
- Pinboard `extended` notes are no longer written as the note body. A long
  note used to pass the reader's size gate and be cited as the source's own
  words; a short one was flagged needs-content and then silently destroyed
  when enrichment replaced the body. The note now lives in the reserved
  frontmatter key `annotation:` (alias `note:`), the body is left empty so
  every bookmark takes the needs-content → fetch path, and the annotation is
  carried through `SourceDoc` → index `SourceRow.annotation` → `/api/source`
  → the portal source page (and the SQLite shadow's search text). It is never
  part of a reader prompt and is scrubbed from the public/static model.
  Shadow schema bumped to v6 — `ovp2 index` rebuilds it (#481).
  Keeping it out of the extraction prompt alone was not enough: several tools
  hand a model raw file content, where the reader's own words would read as
  the author's and could be quoted back as evidence. The entry is now cut at
  every model-facing boundary — the agent's `read_source_body` and
  `search_source_chunks`, the shared `read_source_doc` behind source-grounded
  chat / source summaries / the session glossary / the MCP `ovp://source`
  resource, and MCP's `ovp_read_note`. The MCP index model drops the field
  outright, since that whole surface is model-facing. Every other frontmatter
  key and all body text are untouched, and the portal still shows the note.
  Known gap, tracked separately: the clipping parser accepts only `---\n`
  fences, so a CRLF or unterminated note parses with its frontmatter as BODY
  — which mis-parses `title`, `source` and `tags` the same way, and predates
  this key.
  The Pinboard renderer folds every line break YAML recognizes (bare CR, NEL,
  U+2028, U+2029) into `\n`: left as-is they terminated the block scalar and
  made the whole note `Unparseable`, which dropped that bookmark from
  enrichment permanently. Whitespace inside a line is preserved, so a Markdown
  hard break survives.
- claims_zh tail: a claims backlog left by crystal-synth now drains in one
  run instead of being throttled to the daily enqueue budget
  (`auto_max_per_run`, default 30). The tail budget is a separate config key
  `auto_claim_zh_max_per_run` (default 0 = unlimited); provider outages stay
  contained by the batch's consecutive-failure breaker, and a
  deterministically failing ("poison") claim is quarantined after 3
  consecutive failed tail runs so it can no longer bleed one paid call per
  run.

### Added
- Custom frontmatter keys reach the index and are filterable. A capture
  source's own keys (a clipper property, a bot's capture id) already survived
  intake (`fs::rename`) and enrichment (the block is re-emitted verbatim) —
  they simply had nowhere to land, because `ClippingFrontmatter` is a fixed
  struct and dropped them at parse. `ovp2 pinboard`'s own `clipped_from` was
  the in-tree casualty. They now flow to `SourceRow.meta` and are queryable
  via `ovp2 find --meta key=value` (repeatable, ANDed, exact on both halves)
  and the MCP `find` tool's `meta` object (#483).
  Admission is narrow on purpose: the `x_`/`capture_` prefixes, plus a fixed
  allow-list for `clipped_from`. The prefix convention is what lets any
  external producer claim a key without an OVP change per tool; anything else
  is dropped, because an open pass-through would make the index a mirror of
  arbitrary user YAML and turn every key into a query surface and a
  compatibility obligation. Scalars only — a list or map has no faithful
  flat-string form. `SourceDoc` stays typed (invariant #3): the map travels
  beside it from `read_source_with_meta`, never inside it. Shadow schema
  bumped to v7 (new `source_meta` table) — `ovp2 index` rebuilds it.
- `ovp2 claim <key> [--json]` — the evidence closure of one durable claim on
  the CLI. The closure moved to `ovp_memory::closure` and is now the single
  implementation behind the CLI verb, the MCP `claim` tool, and the
  `ovp://claim/<key>` resource (#484).
- claims_zh observability: tail runs append a record
  (`translated`/`skipped`/`errors`/`error_keys`/`quarantined`/`remaining`) to
  `.ovp/crystal/claims_zh_runs.jsonl` — pure no-op runs leave no record, the
  early-return failure gates (config/ledger/client build) record
  `outcome: "skipped"` + `reason`, and writes are single-write +
  `sync_data`'d with torn-line-tolerant reads; both the tail summary and
  `ovp2 source-work claims-zh` now print the remaining untranslated backlog.
- `ovp2 schedule <install|uninstall|status>` — productized OS scheduler for
  the daily loop (launchd user agent on macOS, systemd user timer on Linux).
  `install` writes the unit file(s) + a chmod-600 env-file template
  (`<vault>/.ovp/daily.env`) and loads the job; `status` reports
  loaded/enabled state, schedule, env file, last log lines, and warns when
  the last daily run is more than 2 days old; `uninstall` removes the job
  and keeps logs + env file. No daemon (M32 §9) — the OS owns the clock.
- Enrich leaves a trace (#486): every body rewrite by the web-fetch or
  GitHub enrich phase appends a `source_enriched` event to
  `60-Logs/pipeline.jsonl` (`kind`, `url`, `old_body_chars`, `old_sha256`,
  `new_sha256`, `title` in `reason`). Before this the rewrite was invisible
  except as a content-hash change between two intake sweeps.
- `ovp2 daily` ends with one machine-readable stdout line,
  `daily-result: {json}` — run id, date, vault-relative report / index /
  evidence / console paths (`null` on `--dry-run`) and the run's counts — so
  callers stop guessing the report path from the date-keyed run id.

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
