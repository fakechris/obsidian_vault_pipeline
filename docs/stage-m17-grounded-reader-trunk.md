# Stage M17 — Grounded Reader Trunk (epic result + retrospective)

> **Result: SHIP as the trunk reader surface (ready_as_rust_trunk = with_caveats).**
> An end-to-end `read-source` command turns an article into a human-usable reader
> pack — readable cards with title+takeaway+body and provenance collapsed-but-intact
> — over the validated M14a.8 truth layer, with NO Referent/ontology on the main
> path. Validated on 12 held-out articles: **100% usable without raw JSON, 100%
> provenance accessible-clean (0 judged noisy), 100% collapse-reduces-density,
> 11/12 "good" + 1/12 "ok", 0 poor, 0 truth-layer failures.**

## What was built (committed `a38fa76`; 507 workspace tests pass, clippy clean)

Main path, all Rust-native (the trunk surface):
```
Source → Grounded Units (v5) → Critic Repair (v1) → Reader Cards (card_synth/v3) → Reader Pack
   truth layer ──────────────────────────────────┘        view layer ────────────────┘
```
- `crates/ovp-domain/src/reader/cards.rs` — Rust port of the frozen `card_synth/v3`
  prompt (asset `prompts/card_synthesis.md`); deterministic citation gate (a card
  citing no real accepted Unit is dropped, not rendered; prefix-tolerant).
- `crates/ovp-domain/src/reader/pack.rs` — the reader pack writer: a self-contained
  **collapsible HTML workbench** (`<details>` evidence, inline CSS, ~13–22 KB, no
  deps) + a **flat Markdown** view + `source-support.md` (card → unit → quote/span)
  + `cards.json` + `run-status.json`. Provenance is collapsed VISUALLY but never
  absent from artifacts. Deterministic (byte-identical re-render). Decoupled from
  `SourceExtraction` (`GroundingStatus`) so it renders from a live run OR committed
  artifacts.
- `ovp-cli read-source` — end-to-end (extract → repair → synth → pack), **fail-loud**
  on truth-layer errors (parse / 0 units / `accepted_without_quote>0` / 0 cards);
  `--client replay|live`; `--render-only` (`--units-json`/`--cards-json`) to render
  packs from existing artifacts without a model call. No canonical / evergreen / RAG
  / Referent wiring.

## Example (live end-to-end, s05 — "Building a C compiler with parallel Claudes")

`read-source --client live` produced: 34 grounded units → critic (5 trims / 7 adds)
→ 12 cards, `accepted_without_quote=0`, 0 cards dropped. A card from `reader.md`:

> ## 1. The loop harness enables sustained autonomous progress…  _definition_
> **The harness runs Claude in a container, continuously pulling the next task…**
> This allows sustained autonomous progress without human input, though in one
> observed instance Claude accidentally killed itself with pkill -9 bash.
> <details><summary>Evidence — 2 quote(s)</summary>
> - "To elicit sustained, autonomous progress, I built a harness that sticks Claude in a simple loop" `[u-001-019b5009 · line 17]`
> - "…Claude pkill -9 bash on accident, thus killing itself and ending the loop. Whoops!" `[u-028-32ce254a · line 33]`
> </details>

Note the modality fidelity ("though in one observed instance") carried from v3, and
the verbatim quotes + unit ids + source lines one click away.

## Validation (Block 3 — 12 held-out packs, split-blind usability judges)

| signal | result |
|---|---|
| usable without raw JSON | **12/12 (100%)** |
| provenance accessible-clean (vs noisy/buried) | **12/12 (100%)**, 0 noisy |
| collapse reduces density (vs inline citations) | **12/12 (100%)** |
| overall usability | **11 good · 1 ok · 0 poor** |
| failure classes | none ×8 · ui_polish ×1 · referent_needed ×3 |
| truth-layer / card-architecture failures | **0** |

## Failure classes (honest)

- **referent_needed ×3** (s04, s06, s08 — entity-dense sources): a reader wanting
  "everything about entity X" has no object index. This is exactly the narrow,
  entity-density-gated **object-index helper** M15 already scoped (and the s04
  identity mis-binding routed to `BL-113`). NOT a reason to revive the Referent
  main path — only 3/12 sources, all entity-dense; the median source needs nothing.
- **ui_polish ×1** (s03) — RE-CLASSIFIED after inspection: this is NOT a reader-pack
  bug. The source `message_agent`/`list_teammates`/`~/.termhive/shared_content`
  render as `messageagent`/`listteammates` because **M14a's source→span rendering
  (`render_plain`) strips `_` as a Markdown emphasis marker**, so the underscore is
  gone from the *unit quote* itself, faithfully carried into the card. It is an
  upstream truth-layer normalization artifact, not the view layer. (A naive
  MD-escape attempt was made and reverted — it broke the cards' legitimate backtick
  code-spans and did not address the upstream cause.) Optional future fix: preserve
  code-identifier underscores in `render_plain` — a *truth-layer* change, out of M17
  scope.
- **none ×8**: clean.

## The four product questions

1. **Usable without raw JSON?** Yes — 100% of packs; title+takeaway+body convey the
   source; JSON never needed to grasp the thesis.
2. **Provenance advantage preserved?** Yes, and it is the moat: every card → cited
   Unit → verbatim quote + source line, collapsed into a footer; the citation gate
   drops uncited cards; every pack rendered `accepted_without_quote=0`,
   `cards_dropped_uncited=0`. OVP remains the only arm with claim→source provenance.
3. **Readability gap vs KMEM reduced?** At the felt/product level, plausibly yes —
   the three render-layer levers M16.1 prescribed (title+takeaway first, body next,
   evidence collapsed) are implemented and 100% of judges said the collapse reads
   less dense than inline citations. **Not re-measured** as a fresh blind KMEM
   pairwise on the v3+collapse surface (caveat), so this is reasoned from
   collapse-helps=100% + the M16.1 diagnosis, not a new measured win over the 7-5.
4. **Failures = UI or Referent?** UI-polish (1, upstream) + the narrow object-index
   helper (3). **Zero** implicate Units, grounding, or the card-view design.

## Integration decision

- **Referent/Resolver stays PAUSED** (true) — not reintroduced as main path.
- **Next work = mixed:** (a) ship this reader trunk; (b) the entity-density-gated
  optional **object-index view** over existing unit-arg surfaces (covers s04/s06/s08;
  s04 identity → `BL-113`); (c) optionally fix upstream `render_plain` underscore
  stripping for code identifiers; (d) browser/human UX pass on the HTML (the live
  click-to-expand was inferred from the MD proxy, not human-tested); (e) eventual
  legacy-repo migration is the broader follow-on, out of M17 scope.
- **Ready as the Rust trunk surface: yes, with caveats** — it is view-only over the
  validated truth layer, fail-loud, deterministic, and clean; the residual gaps are
  a scoped optional helper and one upstream-normalization cosmetic, neither a blocker.

## Final acceptance checklist

1. Usable reader artifact ✓ (reader.html + reader.md, 12/12 usable).
2. End-to-end command ✓ (`read-source`, proven live + render-only).
3. Evidence/provenance intact ✓ (collapsed, never absent; `accepted_without_quote=0`).
4. Referent/Resolver not main path ✓ (paused).
5. Validation report ✓ (this doc).
6. Tests/checks pass ✓ (507 workspace tests, clippy clean, deterministic).
7. Forbidden-path audit ✓ (only source/prompts/docs/scripts committed; `.run/m17/`,
   cassettes, KMEM dumps gitignored, not committed).

## Honest caveats

- N=12, single usability judge per pack, no-ties — the unanimous aggregates
  (100/100/100) and the stable failure taxonomy are the load-bearing signals.
- Judges read `reader.md`; the live HTML `<details>` interaction was inferred, not
  human-tested in a browser.
- "Narrows the felt readability gap" is reasoned, not a fresh measured KMEM win.
- KMEM full-content re-fetch flattered KMEM throughout M15/M16, so OVP's standing is
  conservative.

## Artifacts
- Committed: `crates/ovp-domain/src/reader/{mod,cards,pack}.rs`,
  `prompts/card_synthesis.md`, `ovp-cli read-source`, this doc. Raw packs + judging
  under `.run/m17/` (gitignored).
