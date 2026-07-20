# Obsidian Vault Pipeline (OVP2)

A local-first knowledge runtime for Obsidian vaults. OVP2 turns the articles,
clippings, and bookmarks you capture into a grounded, auditable knowledge base
— and every statement it keeps can be traced back to verbatim source lines.

[简体中文](README.zh-CN.md)

## What this is

OVP2 organizes a vault into three layers:

| Layer | 中文 | Contents |
|---|---|---|
| Source | 原文 | The captured material itself: web clippings, Pinboard bookmarks, manual drops. Never rewritten. |
| Memory | 记忆 | Per-source grounded **Units** (verbatim quotes with line numbers) and readable **Cards** built only from those Units. |
| Knowledge | 结晶 | Cross-source **Claims**, each classified **durable** or **caveated**, each citation resolving to a Unit, a quote, and a source line. |

The truth layer is the product. A claim that cannot cite verbatim evidence
does not persist: mechanical gates check every citation against the accepted
Units before anything is written, and all durable state lives in append-only
ledgers inside the vault (`.ovp/`). Everything else — the search index, the
web portal, the theme views — is a projection that can be deleted and rebuilt
from those ledgers at any time.

## From OVP to OVP2

OVP2 is a ground-up Rust rewrite of the Python OVP (six-stage pipeline,
`knowledge.db`, Evergreen/MOC notes, `ovp`/`ovp-autopilot`/`ovp-ui`). The
rewrite changed the direction, not just the language: eager concept-map and
canonical-ontology extraction failed validation on real data, and the system
was rebuilt around a grounded reader trunk and a gated crystal truth layer.
The full decision story, a command mapping, and migration notes for existing
vaults are in [`docs/ovp-to-ovp2.md`](docs/ovp-to-ovp2.md).

## Install

Prebuilt binaries for macOS (arm64/x64) and Linux (x64); no Rust toolchain
required. Current release: **v0.23.0**. Both channels are verified end-to-end.

```sh
curl --proto '=https' --tlsv1.2 -LsSf \
  https://github.com/fakechris/obsidian_vault_pipeline/releases/latest/download/ovp-cli-installer.sh | sh
```

or via Homebrew:

```sh
brew install fakechris/ovp2/ovp2
```

Check the install with `ovp2 --version`. Details, the release process, and a
proxy note for `brew` are in [`docs/install.md`](docs/install.md).

## Quick start

1. **Configure the LLM** for live runs — in your shell profile or a private
   `.env` you `source` (never in the repo or the vault):

   ```sh
   export ANTHROPIC_API_KEY=sk-ant-...
   export OVP_LLM_TIMEOUT_SECS=480   # required for live runs; the 180s default mis-kills slow responses
   # optional: ANTHROPIC_BASE_URL, OVP_LLM_MODEL, OVP_LLM_MAX_TOKENS, OVP_LLM_NO_PROXY=1
   ```

2. **Run the daily loop** against your vault (`--dry-run` first shows the plan
   without writing anything):

   ```sh
   ovp2 daily --vault-root ~/Documents/my-vault --client live
   ```

   One run sweeps captures into a normalized queue (URL + content dedup),
   reads each new source through the grounded reader trunk, writes reader
   packs into the vault, records every attempt in append-only ledgers, and
   rebuilds the read model.

3. **Put it on a schedule** — never think about the heartbeat again:

   ```sh
   ovp2 schedule install --vault-root ~/Documents/my-vault
   ```

   Installs an OS-level job (launchd on macOS, systemd user timer on Linux)
   that runs `ovp2 daily` every day at 09:00 (`--time HH:MM` to change).
   Credentials go in the generated `<vault>/.ovp/daily.env` (chmod 600) —
   `install` prints what to fill in. Check with `ovp2 schedule status`,
   remove with `ovp2 schedule uninstall`.

4. **Open the portal**:

   ```sh
   ovp2 serve --vault-root ~/Documents/my-vault
   ```

   then open the printed URL (default `http://127.0.0.1:3141`).

5. **Optional — Pinboard capture**:

   ```sh
   ovp2 pinboard-sync --vault-root ~/Documents/my-vault --live --max 200
   ```

   Needs `PINBOARD_TOKEN` (`username:TOKEN`; never stored, never logged). The
   Pinboard API returns your entire bookmark history, so a first sync is
   guarded: without `--since`/`--max`, a run that would create more than 500
   new notes aborts before writing anything. `--max 200` takes the newest 200
   and lets older bookmarks drain on later runs.

## The portal

`ovp2 serve` hosts a single-page portal over the vault's read model, with six
destinations:

| Page | Answers |
|---|---|
| Today | What came in, what was read, what crystallized, what needs attention |
| Library | Every captured source by collection, month, and status, with a three-layer source detail view (memory / original / claims) |
| Search | One box over sources, cards, units, claims, and themes (`⌘K` anywhere) |
| Knowledge | Themes and Claims, durable/caveated status, evidence drill-down, scoped graph views |
| Ask | Cited Q&A over the vault's evidence; citations are verified against the index |
| System | Runs, blocked sources, `doctor` results, settings, concept reference |

Two equal-weight themes — light "Atelier" (warm parchment + terracotta) and
dark "Vault" (near-black + deep blue + cyan) — and an EN-default interface
with a full 简体中文 translation, both switchable in the UI.

## Core commands

Every CLI verb is labeled PRODUCT / DIAGNOSTIC / DEMOTED in `--help`. The
product surface:

| Command | Does |
|---|---|
| `ovp2 daily` | The blessed daily loop: capture sweep → grounded reader trunk per new source → lifecycle → ledgers + report → read model + console refresh |
| `ovp2 schedule` | Install/uninstall/inspect the OS-level daily schedule (launchd / systemd user timer) — `install` once and the loop runs itself |
| `ovp2 serve` | Start the localhost portal server: `.ovp/console/` pages + JSON API (`/api/find`, `/api/search`, `/api/ask`, …) |
| `ovp2 ask` | Retrieval-augmented Q&A over product state; prints a cited answer with deterministic citation verification |
| `ovp2 pinboard-sync` | Materialize Pinboard bookmarks as inbox notes, URL-deduped, with the first-sync flood guard |
| `ovp2 crystal-synth` | Turnkey Crystal synthesis: reader packs → cross-source claims → grounded filter → strength gate → durable write |
| `ovp2 crystal-review-session` | Prepare a bounded human review session over caveated claims (sheet + decisions template) |
| `ovp2 index` | Rebuild the read model (`.ovp/index/index.json`); always a full deterministic rebuild |
| `ovp2 find` | Query the read model: sources, packs, claims, runs, cards, units — by term, kind, status, date |
| `ovp2 doctor` | Health checks over vault state; `--fix` applies safe repairs, never deletes |
| `ovp2 digest` | Daily digest (`.ovp/digests/<date>.md`); ephemeral reuse surface, never enters a ledger |
| `ovp2 project` | Projection Lanes: view claims by lane, or write durable claims as vault notes (`--write` / `--rebuild`) |
| `ovp2 mcp` | MCP stdio server exposing find/search/status/doctor tools to MCP-compatible editors |

## Privacy & trust

OVP2 is local-first. Everything it knows lives as plain files inside your
vault (`.ovp/` ledgers and projections plus the notes themselves); there is no
cloud component, no account, and **no telemetry**. The only things that ever
leave your machine, each under your explicit configuration:

- **LLM calls** — during `daily`, `ask`, and `crystal-synth` (and the portal's
  Ask page), article/bookmark text is sent to the LLM provider **you**
  configure via environment keys (`ANTHROPIC_API_KEY`, optional
  `ANTHROPIC_BASE_URL`). No key, no calls: the default run is offline/replay.
- **Pinboard sync** — `pinboard-sync --live` talks to pinboard.in with your
  `PINBOARD_TOKEN` (never stored, never logged).
- **Web/GitHub enrichment** — enrichment fetches the URLs you bookmarked
  (plus GitHub API metadata for repo links) to capture their content. Set
  `XQUIK_API_KEY` to resolve X/Twitter status bookmarks through Xquik when
  live web enrichment is enabled. Xquik is an independent third-party service,
  not affiliated with or endorsed by X Corp.
- **`compare-run` (diagnostic, manual)** — the external comparator sends the
  source path/URL and queries to the Nowledge Mem HTTP service you point it
  at. It never runs as part of `daily`; skip the command and nothing is sent.

Nothing else is transmitted.

## Documentation

| Doc | Contents |
|---|---|
| [`docs/ovp-to-ovp2.md`](docs/ovp-to-ovp2.md) | The OVP → OVP2 story: what changed, why, and how to migrate ([中文](docs/ovp-to-ovp2.zh-CN.md)) |
| [`docs/install.md`](docs/install.md) | Install channels and the maintainer release process |
| [`docs/operator-runbook.md`](docs/operator-runbook.md) | Day-to-day operation on a real vault: daily loop, failures, review sessions, recovery |
| [`docs/architecture.md`](docs/architecture.md) | Crate map, dataflow, invariants, portal and evolution kernel |
| [`docs/product-state-layout.md`](docs/product-state-layout.md) | Where product state lives; authoritative vs derived |
| [`docs/invariants.md`](docs/invariants.md) | The architecture invariants, CI-gated where possible |

## Status

The workspace is 22 Rust crates; 780 tests pass (1 ignored) plus binary-level
end-to-end coverage. The daily loop, portal, Crystal synthesis, and review
flow run on a real vault today. Release v0.23.0 continues the repository's
release lineage (v0.22.0 was the last Python-era release); v2.0.0 is reserved
for the merge-to-main / Python-retirement milestone. In flight: real-vault
dogfooding and semantic theme projection. Historical stage records live in
`docs/stage-*.md`; release history is in [`CHANGELOG.md`](CHANGELOG.md).

## License

Dual-licensed under either of

- MIT license ([LICENSE-MIT](LICENSE-MIT))
- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE))

at your option. Unless you explicitly state otherwise, any contribution
intentionally submitted for inclusion in this work by you, as defined in the
Apache-2.0 license, shall be dual licensed as above, without any additional
terms or conditions.

Exception: the vendored IBM Plex web fonts
(`console-ui/src/design/fonts/`) remain under the SIL Open Font License 1.1 —
see [`console-ui/src/design/fonts/LICENSE.txt`](console-ui/src/design/fonts/LICENSE.txt).
