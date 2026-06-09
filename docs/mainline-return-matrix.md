# Mainline Return Matrix — Legacy Python OVP → Rust trunk

> Companion to [`docs/stage-m29-mainline-return-audit.md`](./stage-m29-mainline-return-audit.md).
> Compares **product capability**, not CLI names. "Covered" means a real human gets the same
> product value, by whatever command/storage the Rust trunk chooses.
> Snapshot: branch `codex/rust-migration` @ M28 (HEAD `a7fd390`), 2026-06-08.
>
> Supersedes the reader-trunk-era view of [`docs/legacy-alignment.md`](./legacy-alignment.md),
> which was written from the pre-pivot M7–M13 canonical-store roadmap and predates M14–M28.

## Status legend

- **covered** — product value present in Rust trunk (different commands/storage OK)
- **partial** — exists but thin / demoted / not the blessed path
- **redesigned** — Rust deliberately solves the same need a different way
- **missing-P0** — blocks a usable daily Rust workflow on the real vault
- **missing-P1** — real users notice within a week
- **dropped** — intentional non-goal
- **deferred** — wanted after mainline switch
- **needs-decision** — product call required before it can be classified

## Matrix

### 1. Daily capture / ingest
| Legacy capability | Rust today | Status |
|---|---|---|
| Pinboard fetch + landing (`pinboard-processor`, L1) | none | missing-P0 |
| Clippings / Reader web-clipper intake (L0) | none | missing-P0 |
| Raw Markdown inbox intake | `MarkdownInboxSource` / `InboxScanSource` (read a file) | partial |
| GitHub repo intake (`auto_github_processor`) | none | missing-P1 |
| Web-page / article intake (`auto_article_processor` intake path) | `interpret-article` reads one file; no inbox watch/normalize/lifecycle | partial |
| Global URL dedup across active staging set (`source_dedup`) | none | missing-P0 |

### 2. Source lifecycle & vault file movement
| Legacy capability | Rust today | Status |
|---|---|---|
| 5-stage staging set L0–L4 (`Clippings`→`01-Raw`→`02-Processing`→`03-Processed/YYYY-MM`) | none | missing-P0 |
| `VaultLayout` (~25 typed vault paths) | `vault_layout.rs` exists (value type) | partial |
| Image/attachment download + rewrite (`image_downloader`) | none | missing-P1 |
| File move/lock/archive primitives | `VaultFsPlanApplier` writes notes; no lifecycle move/lock | partial |
| Frontmatter repair / normalize (`repair_*`, `note_type_normalize`) | sinks own schema (write-correct-first) | redesigned / deferred |

### 3. Article / paper / clipping handling
| Legacy capability | Rust today | Status |
|---|---|---|
| Article interpret → vault note (深度解读) | `interpret-article` + `VaultFsPlanApplier` (offline, cassette) | covered |
| Paper deep-dive (arXiv, `auto_paper_processor`) | `PaperDoc` types + paper fixture; interpret path partial | partial |
| 13-section LLM article deep-dive | — | dropped (BL-029/BL-066; per legacy itself) |
| Grounded reader cards / pack (NEW) | `read-source`: Source→Units→Critic→Cards→Pack (M14a–M20, 20/20) | redesigned (net-new, ahead of legacy) |

### 4. Evergreen / canonical / absorb / crystal
| Legacy capability | Rust today | Status |
|---|---|---|
| Absorb L2→L3 lifecycle (`run_absorb_workflow`, `absorb_router`) | none on blessed path | missing-P0 |
| Evergreen minting (new note) | `EvergreenConceptWriter` (M12a, demoted) builds+tests | partial (demoted) |
| Same-slug enrich/reconcile | M12b reconcile (demoted) | partial (demoted) |
| ConceptRegistry identity authority | `concept_registry.rs` (181 lines, demoted) + `ConceptResolver` trait | partial (demoted) |
| Canonical store | `CanonicalFsStoreApplier` + `canonical.rs` (demoted but wired) | partial (demoted) |
| Promotion policy lanes (AUTO/ESCALATE/REJECT) | none | missing-P1 |
| Crystal materialization (legacy community/contradiction) | — | redesigned (see below) |
| **Durable Crystal truth layer (NEW)** | `crystal-lint` + `crystal-write` gate + append-only `ledger.jsonl` (M22–M28) | redesigned (net-new) |
| Semantic dedup of near-duplicate claims | none | deferred |

### 5. Query / ask / digest / working memory
| Legacy capability | Rust today | Status |
|---|---|---|
| Read query (list/get/search/backlinks/stats, `query_tool`) | `query` over fs read model (lexical) | partial |
| BM25 + embedding + RRF retrieval | `ovp-rag` lexical-only; **no embeddings** | partial / redesigned |
| Anchored inquiry `ovp-ask` + chats projection (M21) | none | missing-P1 |
| `/digest` daily synthesis (M20) | none | missing-P1 |
| `ovp-working-memory` daily budgeted context pack | none | missing-P1 |
| `ovp-prime` session context pack | none | deferred |

### 6. UI / dashboard / review surfaces
| Legacy capability | Rust today | Status |
|---|---|---|
| Local web UI server (Reader shell + Maintainer `/ops` shell, `ui_server`) | none (no server) | missing-P1 / needs-decision |
| FTS search surface (`page_fts`) | lexical `query search`, no FTS index | partial |
| Atlas / Topics / Map / Graph reader surfaces | none (graph viz intentionally out) | dropped/deferred |
| Ops dashboards (candidates, contradictions, signals, actions, pulse, audit) | none | needs-decision |
| Article-level review console (NEW) | M26 AB dashboard + **M28 Crystal Console** (static HTML over `.run`) | redesigned (net-new) |

### 7. Automation / autopilot / daemon / scheduled
| Legacy capability | Rust today | Status |
|---|---|---|
| `ovp-autopilot` daemon (watch=inbox, absorb→moc→knowledge_index) | none | missing-P1 |
| `ovp --full` / `--incremental` daily pipeline | `run-cycle` (one file, demoted cycle) + `auto-run` sweep (offline) | partial |
| Task dispatcher (QUEUE→GENERATED, M20) | none | deferred |
| Scheduled live-concept scan | none | dropped (Referent-adjacent) |

### 8. Quality / lint / truth / provenance
| Legacy capability | Rust today | Status |
|---|---|---|
| WIGS 5-layer lint (`lint_checker`) | `ovp-lint` (missing notes/stale index/broken links/orphans) | covered (read model scope) |
| Truth/provenance grounding | `read-source` `accepted_without_quote=0`; crystal citation→unit→quote→line gate | covered (net-new, stronger) |
| Evidence verify / replay (`evidence_verify`, `evidence_replay`) | crystal-lint re-runs validator | partial / redesigned |
| Contradiction detection / resolution | none | deferred |
| `ovp-doctor` cross-registry health | none | missing-P1 |

### 9. State stores / knowledge.db / indexes / audit
| Legacy capability | Rust today | Status |
|---|---|---|
| `knowledge.db` SQLite (pages_index, FTS5, page_links, embeddings, audit_events, truth_projections, ops_state) | **none — no SQLite anywhere** | missing-P0 / needs-decision |
| Schema migration discipline (additive/recompute/breaking, v9) | n/a | deferred |
| Canonical store persistence | `CanonicalFsStoreApplier` (fs records, demoted) | partial |
| Durable run ledger / transactions (`txn.py`, `60-Logs/transactions`) | `EventLog` **in-memory only** | missing-P0 |
| Audit-event log | `Event` in-memory; crystal ledger append-only on disk | partial |
| `ops_state` projection | none | missing-P1 |

### 10. Integration surfaces
| Legacy capability | Rust today | Status |
|---|---|---|
| MCP server (`mcp.py`) | none | needs-decision |
| GitHub enrichment | none | missing-P1 |
| Pinboard | none | missing-P0 (see §1) |
| Browser/Reader clipper | none | missing-P0 (see §1) |
| KnowledgeMem | `ovp-eval` comparator only (above trunk, gate-fenced) — reference-only | redesigned (eval, not product) |
| Export artifact (`ovp-export`) | none | deferred |

## Roll-up

| Status | Count (capability rows) |
|---|---|
| covered | 4 |
| partial | 14 |
| redesigned (net-new or different) | 8 |
| missing-P0 | 8 |
| missing-P1 | 11 |
| dropped | 3 |
| deferred | 8 |
| needs-decision | 5 |

**P0 set (blocks a usable daily Rust workflow on the real vault):** real intake (Pinboard/Clippings/inbox watch + normalize + URL dedup), source lifecycle file movement (staging set L0–L4), absorb→evergreen as a *blessed* path, a *persistent* read index + durable run ledger/audit (replace in-memory `EventLog`), and the `knowledge.db`-equivalent projection decision.
