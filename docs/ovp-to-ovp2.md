# From OVP to OVP2 — what changed, why, and how to migrate

[简体中文](ovp-to-ovp2.zh-CN.md)

OVP2 (this repository, the `ovp2` binary) is a ground-up Rust rewrite of the
Python Obsidian Vault Pipeline. This document is the decision record: what the
old system was, why the rebuild happened, which decisions define OVP2, what
users do differently, and what an existing OVP vault needs to know.

## 1. What OVP was

The Python OVP (`obsidian-vault-pipeline` on PyPI, last release v0.22.0) was a
knowledge state runtime built around an Obsidian vault:

- A **six-stage pipeline** — Ingest → Interpret → Absorb → Refine → Normalize
  → Derive — orchestrated by `ovp --full` / `ovp --incremental`, with a
  real-time watcher daemon (`ovp-autopilot`).
- A **`knowledge.db` SQLite projection** (pages index, FTS, links, embeddings,
  audit events, truth projections) read by `ovp-query`, `ovp-truth`,
  `ovp-doctor`, and the UI.
- An **Evergreen / MOC / canonical-concept ambition**: absorb extracted
  concept candidates, promoted them into canonical Evergreen notes, maintained
  Atlas MOCs, an entity layer, a concept registry, and later Louvain + LLM
  "crystal" synthesis.
- A large CLI surface (~90 entry points: `ovp-absorb`-era commands, `refine`,
  `ovp-build-crystals`, `ovp-ask`, `ovp-export`, `ovp-packs`, …) and a
  **server-rendered web UI** (`ovp-ui`, typically at `127.0.0.1:8787`) with a
  reader-first Knowledge Library at `/` and an operator dashboard at `/ops`.

## 2. Why we rebuilt

The honest arc, in three moves:

**The concept-map direction failed validation.** The rewrite began as a
port of the Python architecture: eager concept extraction, canonical slugs, a
Referent/entity layer, MOC rebuilds, RAG over minted notes. When the v2
concept-map path was finally run against real models and real articles (M13),
it scored **0 of 3 concept maps judged real** — after roughly eight milestones
of infrastructure. The conclusion was not "iterate the prompt" but "wrong
root": eagerly minting canonical concepts from single documents produces
confident, unverifiable abstractions. The canonical / Referent / RAG layers
were demoted (they still build and test, marked DEMOTED in `--help`), and
nothing on the product path depends on them.

**The pivot: grounded reader trunk, then the crystal truth layer.** The
replacement root is verbatim grounding. Each source runs through the **reader
trunk**: Source → grounded **Units** (each carrying a verbatim quote and line
numbers from the original; `accepted_without_quote = 0` is a hard gate) →
critic repair → readable **Cards** → a Reader Pack in the vault. On top of
that sits the **Crystal truth layer**: cross-source **Claims** that must pass
mechanical citation gates (every citation → an accepted Unit → a verbatim
quote → source lines), an LLM strength verdict that routes each claim to
**durable** or **caveated**, an append-only ledger, and idempotent writes. The
operating rule: *if a claim cannot cite verbatim evidence, it does not
persist.* Validated head-to-head against a commercial memory system on real
articles, the grounded layer won coverage and provenance (M21, M26: 17
better / 3 tie / 0 worse; core coverage 87% vs 58%).

**Rust, for product reasons.** One prebuilt static binary (`ovp2`) installs
via curl or Homebrew with no Python environment at the user's machine; the
offline build has zero network dependencies and the test gauntlet is
deterministic; and the rewrite enforced one clean core instead of the script
sprawl the Python codebase had accumulated.

## 3. Key decisions

| Decision | Why |
|---|---|
| **Truth layer as the moat** | Graph databases, embeddings, and RAG are commodity fashion; verbatim-grounded, line-cited, human-auditable claims are not. Every OVP2 surface (portal, `ask`, `find`, digests) reads from evidence that can be checked by clicking through to source lines. |
| **Fail-loud gates over silent repair** | A claim with a defective citation exits non-zero instead of being quietly patched or dropped. Silent repair converts data corruption into trust corruption; the gates (`crystal-lint`, strength verdicts, citation verification in `ask`) make every rejection visible and attributable. |
| **Append-only ledgers + rebuildable projections** | Authoritative state is a set of append-only JSONL ledgers plus files in the vault. The index, console, portal data, and theme views are projections: deleting them loses nothing and a full rebuild is the entire migration story. If a projection cannot be rebuilt, that is an architecture bug. |
| **No SQLite** | Python OVP's `knowledge.db` was a second source of truth that drifted, needed backups (`ovp-backup-db`), and hid state from the vault. OVP2 decided ledgers + files, with `ovp2 index` producing a plain-JSON read model — revisited only if daily query pain proves the need. |
| **Evolution kernel governance** | Prompts, parsers, gates, and runtime surfaces change only through validated candidates: a registered component, a hypothesis, a paired A/B on cassettes, and an append-only evolution ledger entry (`ovp2 evolve`). This prevents prompt-patching runtime bugs and makes behavior changes attributable and rollbackable. |
| **Product portal IA, not a pipeline console** | Users see Today / Library / Search / Knowledge / Ask; plumbing (runs, flow, audit, candidates) lives under System. Each page answers one user question; a page that answers none does not exist. The Python-era `/ops`-first dashboard is inverted. |
| **Dual-theme design system** | The portal ships the operator's OVP Design System: light "Atelier" (warm parchment + terracotta) and dark "Vault" (near-black + deep blue + cyan) as equal-weight themes, IBM Plex type, quiet-utility rules (1px borders, no gradients, text-first). Graph colors come from the same tokens, so visualizations never fork the visual language. |
| **i18n: EN default + 简体中文** | One interface, fully translated — not bilingual side-by-side text. Language and theme use the same mechanism (`localStorage`, switchable in the UI). User-facing vocabulary is product words (source, memory, claim, theme); internal words (pack, cassette, unit) stay in System and the CLI. |
| **Prebuilt distribution + release lineage** | Users install a binary, not a toolchain: cargo-dist builds the curl installer and Homebrew formula per tag. v0.23.0 deliberately continues the repository's release numbering (v0.22.0 was the last Python-era release); **v2.0.0 is reserved for the merge-to-main / Python-retirement milestone**. |
| **Pinboard first-sync flood guard** | A live incident (2026-07-09): the first `pinboard-sync --live` materialized the operator's entire history — 50,714 bookmark notes, 198 MB — because `posts/all` returns everything. Now, without `--since`/`--max`, any run that would create >500 new notes aborts before writing; `--yes-all` is the explicit override. |
| **Enrichment makes bare bookmarks first-class** | A bookmark with no body is not readable evidence. Web fetch and GitHub README enrichment (feature-gated `web-fetch-live` / `github-live`, compiled into prebuilt binaries) fill `needs-content` sources so they can enter the reader trunk instead of rotting in the inbox. |
| **Semantic themes over keyword buckets** | Theme grouping is moving from hardcoded keyword buckets to an embedding + Louvain community projection — like every other view, a rebuildable projection over the truth layer, never authoritative. In flight. |

## 4. What users do differently

Command mapping (old Python entry points → `ovp2`):

| Python OVP | OVP2 |
|---|---|
| `ovp --full` / `ovp --incremental` (six-stage run) | `ovp2 daily --vault-root <vault> --client live` |
| `ovp-autopilot` (watcher daemon) | No daemon by design — schedule `ovp2 daily` via cron/launchd |
| `pinboard-processor.py` / pinboard stage | `ovp2 pinboard-sync --vault-root <vault> --live` |
| `ovp-ui --vault-dir <vault> --port 8787` | `ovp2 serve --vault-root <vault>` (portal at `127.0.0.1:3141`) |
| `ovp-query` / `ovp-truth` (reads over `knowledge.db`) | `ovp2 find --vault-root <vault> [term] [--kind --status --date --json]` |
| `ovp-ask` | `ovp2 ask --vault-root <vault> "question"` (with citation verification) |
| `/digest` daily synthesis | `ovp2 digest --vault-root <vault>` |
| `ovp-build-crystals` | `ovp2 crystal-synth --vault-root <vault>` (gated, durable/caveated routing) |
| Evergreen promotion / absorb review | `ovp2 crystal-review-session` + `crystal-review-session-apply` (human decisions never bypass the gate) |
| `ovp-doctor` / `ovp-lint` | `ovp2 doctor --vault-root <vault> [--fix]` |
| `ovp-export` | Portal + `ovp2 find --json`; durable claims as notes via `ovp2 project --write` |
| `ovp-backup-db` | Not needed — no database; ledgers are plain files inside the vault |
| MCP surface | `ovp2 mcp --vault-root <vault>` (stdio JSON-RPC) |

Surface mapping:

| Old surface | New surface |
|---|---|
| `ovp-ui` reader / maintainer shells (server-rendered) | The portal SPA: Today / Library / Search / Knowledge / Ask / System |
| `knowledge.db` | `.ovp/index/` JSON projections (`index.json`, `evidence.json`), rebuilt by `ovp2 index` |
| Evergreen notes + Atlas MOCs | Crystal Claims (durable/caveated) + optional vault notes via `ovp2 project --write` (`10-Knowledge/Crystal/`, machine-owned, `<!-- crystal-managed -->`) |
| Packs / profiles (`--pack`, `--profile`) | Retired — one blessed product path; variation is governed by the evolution kernel instead |

Note the flag change: the Python CLIs took `--vault-dir`; every `ovp2` command
takes `--vault-root`.

## 5. Migrating an existing OVP vault

**What carries over unchanged.** The vault itself: your notes, `Clippings/`,
`50-Inbox/` captures, processed sources, attachments. OVP2 uses the same
PARA-family layout (`50-Inbox/01-Raw`, `03-Processed`, `40-Resources`,
`60-Logs`) and never rewrites captured content.

**What is rebuilt.** All projections. OVP2 starts with empty ledgers in
`.ovp/`; sources you want in the truth layer run through the reader trunk
(`ovp2 daily` picks up anything swept into `01-Raw`). The read model, console,
and portal data are then deterministic rebuilds — there is no import step
because there is nothing to import into: projections are always regenerated
from ledgers + packs.

**What is retired.** `knowledge.db` (and its backups), the canonical concept
store and registry, auto-minted Evergreen stubs, and the pack system. Leave
them in place or archive them; OVP2 reads none of them. Legacy Evergreen notes
remain ordinary vault markdown — readable, linkable, just no longer
machine-maintained.

## 6. Current status and roadmap

v0.23.0 ships the daily loop, the portal, Crystal synthesis, and the review
flow on a real vault. In flight: continued real-vault dogfooding, the semantic
theme projection, and the review-loop refinements. The merge to `main` — which
is the formal Python retirement — is gated on the Level-3 sign-off checklist
in `docs/stage-m32-python-retirement-and-product-definition.md`; until then
the Rust trunk lives on `codex/rust-migration`.

Related reading: `docs/architecture.md` (how the system is built),
`docs/operator-runbook.md` (how to run it), `docs/stage-m15-results.md` and
`docs/stage-m17-grounded-reader-trunk.md` (the pivot evidence),
`docs/mainline-return-matrix.md` (the capability audit against legacy).
