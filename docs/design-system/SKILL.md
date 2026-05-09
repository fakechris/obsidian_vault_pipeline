# OVP Design System — Skill

A guide for building things that look and feel like **Obsidian Vault Pipeline** (OVP) — *an auditable knowledge state runtime for Obsidian: Capture → Compile → Reuse.*

If you are a designer or agent making artifacts for OVP — slides, prototypes, a new screen, marketing copy, a feature spec — start here.

---

## What you're designing for

A Python package + local web UI (`ovp-ui`, default `127.0.0.1:8787`) that compiles a personal Obsidian vault into a typed, programmable knowledge atlas. There are two shells:

- **Reader shell** (`/`, `/topics`, `/atlas`, `/search`, `/map`, `/object`, `/note`) — for the human reader. Vocabulary is curated: **Topic**, **Open Question**, **Concept**, **Deep Dive**.
- **Maintainer shell** (`/ops`, `/ops/queue`, `/ops/today`, `/ops/runs`, `/ops/timeline`, `/ops/events`, `/ops/pulse`, `/ops/objects`, `/ops/clusters`) — for the operator. Internal storage names are exposed: **community_crystal**, **contradiction_crystal**, **registry**, **projection**.

Cross-link between the two shells (top-right of the nav) is the only navigation between them. Treat them as separate apps that share tokens.

---

## Files in this design system

| Path | What it is | Use it when |
|---|---|---|
| `README.md` | Brand context, voice, visual foundations, caveats | Always read first |
| `colors_and_type.css` | Tokens — color, type, spacing, radius, shadow | Import in every artifact |
| `assets/wordmark.svg`, `monogram.svg` | Logo lockups (proposals — not from source) | Slides, hero, app icon |
| `preview/colors.html` | Palette cards | Reference / review |
| `preview/type.html` | Type ramp | Reference / review |
| `preview/components.html` | Shell, card, pill, subnav, page-help, stats grid | Copy-paste patterns |
| `preview/brand.html` | Wordmark, voice, ASCII diagrams | Reference for tone |
| `ui_kits/ovp/index.html` | Click-thru of all 4 product screens | Start here for any new screen |
| `ui_kits/ovp/ovp-ui.css` | Self-contained UI kit stylesheet | Import for product mockups |
| `ui_kits/ovp/graph.html` | 3D cluster Atlas — communities, hulls, timeline | Visualizing graph / cluster / contradiction data |
| `ui_kits/ovp/graph-data.js`, `graph-app.js` | Mock vault graph + render logic | Reference / fork for new graph views |

For new product screens, copy `ui_kits/ovp/index.html` and edit. The screen-tabs at the top show the existing patterns — match them before inventing new ones.

---

## Hard rules

These are non-negotiable. Break them only with the project author's permission.

1. **Six-term vocabulary.** *Source · Candidate · Canonical State · Projection · Access Surface · Governance.* These words always carry the same meaning. Do not rename them. Do not invent synonyms.
2. **Reader vs. Maintainer (BL-051).** Reader-facing surfaces say **Topic** and **Open Question**. They never expose `community_crystal` or `contradiction_crystal`. Maintainer surfaces may use storage names freely.
3. **`knowledge.db` is a Projection, not Authority.** Never describe it as the source of truth. It is rebuildable; it does not own canonical identity.
4. **No emoji in product chrome.** The only allowed emoji are 🎯 and 🔍 prefixed to rendered wikilinks (resolved / search-fallback). Section emoji (📝 🏗️ 💡 🔗) appear only inside user-authored note bodies, per the `CLAUDE.md` template.
5. **Dark mode is a substitution, not a source default.** The source repo hard-codes `color-scheme: light`; this design system extends a *deep tech* dark scheme (`--bg #0a0e16`, `--accent #3b82f6`, `--accent-2 #06b6d4`) for the Atlas/graph surface and as an opt-in `data-theme="dark"` for the rest. Don't introduce a third scheme. Flag dark-mode artifacts as proposals.
6. **No gradients, no shadows on cards, no glass.** One shadow exists in the system — `0 12px 36px rgba(31, 26, 23, 0.06)` — and only on the outer `.shell`. Cards lift on hover only with a color shift, never elevation.
7. **Borders are `1px solid var(--border)`.** Always. The single exception is `.warning`, which uses 1px solid `#d48a2f`. No left-accent stripes, no double borders, no inset borders.
8. **Status renders as a `.pill`** containing the literal status word — `ready`, `running`, `stale`, `blocked`. Always terracotta on peach. No semantic green/red.
9. **ASCII diagrams over visual diagrams.** When the architecture needs to be drawn, draw it in monospace boxes (`┌─ ─┐ │ └─ ─┘`). The brand's tone is "this is a system you can audit, not a system to admire."
10. **Backticks for every CLI command, file path, table name, type, code symbol.** Never quotes, never italics. `ovp --full`, `knowledge.db`, `community_crystal`.

---

## Voice cheat sheet

- **Title Case** for nav and section headings ("Featured Topics", "Open Questions").
- **Sentence case** for body and help.
- **Strongly noun-driven sentences.** Short, concrete, load-bearing on the six vocabulary words.
- **No exclamation marks.**
- **First person plural is rare.** Describe the system in third person.
- **Paired claims, semicolon-joined.** *"absorb is part of daily automation; refine is powerful and opt-in by default."*
- **Define a term by listing what it is and what it can't do.** *"`knowledge.db` is a Projection. It stores X, Y, Z. It does not own canonical identity resolution."*
- **Open by saying what it isn't.** *"OVP is not a loose collection of scripts, and it is not only RAG over Markdown."*
- **Bilingual where it matters.** Top-level docs ship in English + 简体中文 with parallel structure — not as translations.

---

## Components, in priority order

When mocking up a new screen, reach for these in order. If none of them fit, ask before inventing:

1. **`.shell`** — outer rounded container. Every page lives in one.
2. **`.nav` + `.subnav`** — chip-rail navigation. Top is shell-level (Library / Topics / Atlas), bottom is page-level scope (All / AI Research / Tools).
3. **`.card`** + `.card.warning` — atomic content unit. Stack vertically. Use `.card.flush` + `.card-head` + `.card-body` when you need a header strip.
4. **`.pill`** — status, count badge, tag. Never a button.
5. **`.page-help`** — collapsible "What is this page?" disclosure with a `<dl>` of provenance metadata. Used on every Reader page.
6. **`.grid.stats`** — KPI tiles, `repeat(auto-fit, minmax(190px, 1fr))`.
7. **`.grid.two-col`** — main content + side rail, 2.1fr / 280px.
8. **`table.kv`** — frontmatter / provenance pattern. Two columns, muted left, value right.
9. **`.timeline`** — pipeline event log. Mono timestamp · dot · description · pill.
10. **`pre.ascii`** — the architecture-diagram block.

---

## Atlas / graph surface

The Atlas (`graph.html`) is the one place the system permits dark-by-default and large-scale visual density. Conventions to keep across any graph view:

- **Color = community, shape = node type, size = quality + backlinks.** Don't double-encode.
- **Edge color carries semantics.** Muted = `ref`, accent = `cite` (with directional particles), warn-amber = `contradict` (the only thicker stroke).
- **Disclosure is the central interaction.** Clusters start collapsed as super-nodes; expand on double-click (default), hover, or zoom — switchable in the Tweaks panel. New graph views must offer at least one disclosure mode.
- **Hulls** are translucent spheres at community centroids; recompute every ~80ms from the live simulation. Toggleable.
- **Timeline** scrubs `absorbed_at` and animates vault growth from the first note forward; reuse this pattern for any time-based graph view.
- **Theme persistence.** Read/write `localStorage['ovp-theme']` so a single toggle controls every shell.

---

## When you need something not in the system

- **Icons** — the product is icon-free. If you must add one, pull from Lucide CDN at stroke-width 1.5, color `var(--muted)` (or `var(--accent)` if actionable). Flag it as a substitution.
- **Photography** — desaturated, behind a 60% terracotta overlay so type stays legible. Use sparingly; the brand is text-first.
- **Charts** — match palette. Lines in `--accent`, axes in `--border`, labels in `--muted`. No fills, no 3D, no gradients.
- **Animation** — `120ms ease-out` opacity fades and color shifts only. Nothing slides, nothing bounces.

---

## Caveats — confirm with the project author before shipping

- **Wordmark and monogram in `assets/`** are proposals; the source repo has no logo.
- **Inter + JetBrains Mono** are font substitutions for the source repo's `ui-sans-serif, system-ui` and `monospace` defaults.
- **Lucide** is a substitution for the icon system the source repo doesn't have.
- **Marketing surfaces** (landing page, docs site) are undefined — the repo only has the local UI. Anything you build there is unprecedented; flag it.
- **Dark mode** is undefined. Don't ship one without explicit approval.
