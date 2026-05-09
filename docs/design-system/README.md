# OVP Design System

Design system for **Obsidian Vault Pipeline (OVP)** — *an auditable knowledge state runtime for Obsidian: Capture → Compile → Reuse.*

OVP is a Python package + local web UI (`ovp-ui`) that turns a personal Obsidian vault into a typed, programmable knowledge atlas. It ingests Pinboard saves, Obsidian Clipper exports, raw markdown, papers, and GitHub repos; compiles them into deep dives, candidates, claims, evidence, contradictions and graph rows; and projects the result into reader-facing atlas pages, object pages, search, briefings, context packs, and an operator workbench.

This design system describes the OVP brand and provides the visual foundations + UI kit needed to make production interfaces, throwaway prototypes, and slides that look like they belong to OVP.

---

## Sources

This design system was reverse-engineered from a single source repository:

- **GitHub** — [`fakechris/obsidian_vault_pipeline`](https://github.com/fakechris/obsidian_vault_pipeline) (`main`, commit `c90427ac`).

Key files referenced:

| File | What was extracted |
|---|---|
| `README.md`, `README.zh-CN.md` | Product narrative, voice, bilingual tone |
| `ARCHITECTURE.md` | Six-term vocabulary (Source / Candidate / Canonical State / Projection / Access Surface / Governance) |
| `PRODUCT_SURFACES.md` | Reader vs Maintainer shell split, full route table |
| `CLAUDE.md` | Schema, naming conventions, six-dimension quality rubric |
| `src/ovp_pipeline/commands/_ui_renderers.py` | All CSS tokens, layout shell, card/pill/subnav patterns |
| `src/ovp_pipeline/commands/ui_server.py` | Route map (Reader: `/`, `/topics`, `/search`, `/map`, `/object`, `/note`, `/atlas`; Maintainer: `/ops`, `/ops/queue`, `/ops/today`, `/ops/runs`, `/ops/timeline`, `/ops/events`, `/ops/pulse`, `/ops/objects`, `/ops/clusters`) |

No Figma file, no marketing site, no logo SVG, no font files were provided. Where real assets are missing, the design system uses workmarks built from the brand's vocabulary and font substitutions flagged below.

---

## Index

This folder is the manifest. Open these in order:

| Path | What it is |
|---|---|
| `README.md` | This file. Brand context + content, visual, iconography fundamentals. |
| `SKILL.md` | Agent skill manifest — how a designer/agent should use this system. |
| `colors_and_type.css` | Single source of truth for tokens: color, type, spacing, radius, shadow. |
| `fonts/` | Webfont files. (Currently empty — see Type substitution flag below.) |
| `assets/` | Logos, wordmarks, illustrative assets. |
| `preview/` | Small HTML cards that populate the Design System review tab. |
| `ui_kits/ovp/` | High-fidelity recreations of the OVP local web UI. |
| └ `index.html` | Click-thru prototype: Reader home → Topic → Object → Maintainer ops. |
| └ `ReaderHome.jsx`, `TopicsPage.jsx`, `ObjectPage.jsx`, `OpsDashboard.jsx`, `Shell.jsx` | Component sources. |

---

## Content fundamentals

OVP's writing voice is **technical, precise, and architecturally opinionated** — like a senior engineer who's been bitten by too many leaky abstractions. It treats the system as a thing with a *trust boundary* and writes about it in those terms.

### Voice & tone

- **First person plural is rare.** The README and architecture docs almost never say "we" — they describe the system in the third person ("the runtime executes six pipeline stages"). When addressing the operator the docs use "you" sparingly, in CLI walkthroughs.
- **Strongly noun-driven.** Sentences are short, concrete, and load-bearing on a small set of capitalized vocabulary words: *Source*, *Candidate*, *Canonical State*, *Projection*, *Access Surface*, *Governance*. These words always carry the same meaning. The architecture doc explicitly says: "The first-page word budget is locked at six."
- **Casing.** Title Case for nav labels and section headings ("Featured Topics", "Open Questions"). Sentence case for body copy and help text. Backticks around every CLI command, file path, table name, and code symbol — never quotes.
- **No exclamation marks. No emoji in product chrome.** The two exceptions: wikilinks in rendered notes are prefixed with 🎯 (resolved) or 🔍 (unresolved fallback to search), and markdown templates in `CLAUDE.md` use 📝 / 🏗️ / 💡 / 🔗 as section markers in *user-authored* notes. The product UI itself is emoji-free.
- **Bilingual.** Top-level docs ship in both English and 简体中文 with parallel structure. The Chinese versions are not translations — they're written in the same crisp, opinionated register.

### Vocabulary layering rule (BL-051)

There is one strict rule about user-facing words:

> The Reader shell uses **Topic** for what internal storage calls a `community_crystal`. Contradiction crystals surface as **Open Question**. Internal names (DB tables, frontmatter `type:`, CLI verbs) keep "crystal" for schema stability. Reader-facing text must say **Topic**.

Maintainer-facing surfaces *may* use the storage names (the `/ops/clusters` page does). Reader-facing surfaces never expose them.

### Tone examples (lifted from the source repo)

- *"OVP is not a loose collection of scripts, and it is not only RAG over Markdown."* — opens by saying what it isn't.
- *"`knowledge.db` is a Projection. It stores: page FTS, structured links, mirrored raw sidecars, timeline / audit events, deterministic section embeddings, read-only query / serve surfaces. It is rebuildable and does not own canonical identity resolution."* — defining a term by listing its members and stating what it can't do.
- *"If a Projection cannot be rebuilt, the Projection layer carried truth that should have been in Canonical State — that's an architectural bug."* — moral certainty about boundaries.
- *"absorb is part of daily automation; refine is powerful and opt-in by default"* — paired claims, semicolon-joined.
- *"Inputs / Sources (external, immutable) → Candidates (system-proposed, awaiting review) → Canonical State (accepted, evidence-backed, long-term)"* — ASCII diagrams over visual diagrams. Always.

### Density

OVP docs are **dense.** A typical paragraph carries 3–5 substantive claims. Tables are preferred over prose for any list ≥ 4 items. Bullet lists are tight (no blank lines between items). Emphasis is rare; when used, it's **bold** for a defined term being introduced, never *italic for stress*.

---

## Visual foundations

OVP's visual identity is **warm parchment, terracotta accent, system sans-serif** — the digital equivalent of a leather-bound research notebook. The whole system is one shell rendered server-side from Python; there is no SPA, no marketing site, no dark mode, no animation framework.

### Color

A six-token palette, all warm. No blues, no greens, no purples in the chrome.

| Token | Hex | Role |
|---|---|---|
| `--bg` | `#f7f6f2` | Page background — warm off-white, faintly yellow |
| `--surface` | `#fffdfa` | Card/shell surface — slightly warmer than bg |
| `--border` | `#e7e1d8` | All borders + dividers — soft sand |
| `--text` | `#1f1a17` | Body text — near-black with brown undertone |
| `--muted` | `#71675d` | Secondary text, metadata, captions — warm gray |
| `--accent` | `#9f4f24` | Links, primary buttons, pills, focus — terracotta / burnt sienna |
| `--accent-soft` | `#f4dfd2` | Pill background, hover surface — peach tint of accent |

There is one warning color used on `.warning` cards: border `#d48a2f`, fill `#fff8ec` — amber, used sparingly. There are no semantic success/error colors in the chrome; status appears as a **terracotta `.pill`** containing the literal status word (`ready`, `blocked`, `stale`, `running`).

`color-scheme: light` is set explicitly; dark mode is not supported.

### Type

System sans-serif: `font-family: ui-sans-serif, system-ui, sans-serif`. The product picks whatever the OS provides — SF Pro on macOS, Segoe UI on Windows, Roboto on Android. There is no custom webfont in the codebase.

> **⚠️ Substitution flagged.** The repo ships no display or webfont files. For polished design artifacts (slides, hero pages, wordmarks) this design system uses **Inter** as the closest neutral system-sans match, paired with **JetBrains Mono** for code, both via Google Fonts. If you want a more on-brand pair, please drop TTF/WOFF files into `fonts/` and update `colors_and_type.css`.

Sizing scale (from the rendered CSS):

| Token | Size | Use |
|---|---|---|
| `--text-xs` | 0.85rem | Cross-link, fine print |
| `--text-sm` | 0.92rem | Help blocks, captions |
| `--text-base` | 1rem (16px) | Body |
| `--text-lg` | 1.15rem | Card headings |
| `--text-xl` | 1.4rem | Section headings (`h2`) |
| `--text-2xl` | 1.75rem | Page title (`h1`) |

`line-height: 1.5` for body, `1.2` for headings. `text-wrap: pretty` is applied to long-form prose. Code uses a 100% size block in `<pre>` with `#f4f4f5` fill.

### Spacing

A 4px-derived scale. Card padding is uniformly `1rem`; shell padding is `1.1rem 1.25rem`; main content padding is `1.5rem 1.5rem 3rem` (extra bottom). Grid gaps are `1rem` (cards) or `0.6rem` (subnav chips).

### Radii

Cumulative — every container has a radius:

| Token | Value | Where |
|---|---|---|
| `--radius-input` | 10px | Inputs, buttons |
| `--radius-pill` | 999px | Pills, subnav chips |
| `--radius-md` | 12px | Help block, image containers |
| `--radius-card` | 16px | Cards |
| `--radius-shell` | 20px | The outer page shell |

### Shadow

One single shadow, used only on the outer shell:

```css
box-shadow: 0 12px 36px rgba(31, 26, 23, 0.06);
```

That's it. Cards do not lift on hover; buttons do not have shadows; the workbench has no elevation system. The brand reads "thoughtful and quiet," not "playful and floating." The only visual hierarchy comes from border + background swap.

### Backgrounds & imagery

No full-bleed photography, no hand-drawn illustrations, no repeating patterns, no gradients (zero — *not even subtle ones in pills*). The page is a flat warm field with rounded surface containers floating on it. ASCII art is the only "illustration" used: the architecture doc, README, and CLAUDE.md all draw their flow diagrams in monospace boxes (`┌─ ─┐ │ └─ ─┘`).

When the brand needs imagery (a slide cover, a hero on a marketing page that doesn't exist yet), the design system reaches for: a single warm photograph of paper / leather / desk, **always desaturated**, set behind a 60% terracotta overlay so the underlying type stays legible.

### Animation

There is none in production. `ovp-ui` is server-rendered HTML with `<meta http-equiv="refresh">` for live pages — that's the entire animation system. When prototyping, keep motion to `120ms ease-out` opacity fades and hover color shifts; nothing slides, nothing bounces, nothing parallaxes. The visual register is *quiet utility*.

### Hover & press states

- **Links:** color stays `--accent`; `text-decoration: underline` appears on hover. No color change.
- **Subnav chips (`.subnav a`):** text shifts from `--muted` to `--accent`; border shifts from `--border` to `--accent-soft`. No fill change.
- **Buttons:** `opacity: 0.92` on hover. No color change, no shadow, no scale.
- **Press:** the codebase doesn't define `:active` states — they fall through to browser defaults. For prototypes, a 4% darkening of the button fill is sufficient.

### Borders

Always `1px solid var(--border)`. No double borders, no inset borders, no left-accent stripes. The `.warning` variant swaps to a 1px solid `#d48a2f` border. That is the only border-color variation in the system.

### Transparency & blur

None. No `backdrop-filter`, no semi-transparent overlays, no glass surfaces. Every surface is opaque.

### Cards

The atomic unit. Every card is:

- `border: 1px solid var(--border)`
- `background: var(--surface)`
- `border-radius: 16px`
- `padding: 1rem`
- `margin-bottom: 1rem`
- No shadow.

Cards stack vertically by default; `.grid.stats` lays them on a `repeat(auto-fit, minmax(190px, 1fr))` grid for metric tiles.

### Layout rules

- Single column, max-width `1180px`, centered with auto margins.
- `.two-col` splits to `minmax(0, 2.1fr) minmax(280px, 1fr)` (main content + side rail) above 780px; collapses to single column below.
- Nav is `flex; gap: 0.9rem; flex-wrap: wrap` — chips, not tabs.
- The shell + nav are sticky in spirit but not in CSS — the page scrolls as one unit.

### Pills

`display: inline-block; padding: 0.15rem 0.5rem; border-radius: 999px; background: var(--accent-soft); color: var(--accent)`. Used for status, count badges, severity tags. Never used for navigation or as a button replacement.

---

## Iconography

**OVP's product UI ships with zero icons.** No icon font, no SVG sprite, no Lucide / Heroicons / Feather dependency, no `<img>` icons in the chrome. The UI is text-first and that is intentional.

The few visual marks present are:

- **Unicode arrows** for cross-shell navigation: `→ Maintenance`, `← Back to Library`, `→ See all N featured topics`.
- **Emoji prefixes inside rendered notes** — the markdown renderer prepends 🎯 to wikilinks that resolved to a real vault target and 🔍 to wikilinks that fell back to a search query (`_replace_wikilinks_with_markdown_links` in `_ui_renderers.py`). This is the *only* emoji that appears in product chrome and only inside note bodies.
- **Section emoji in user-authored content** (📝 详细解释, 🏗️ 架构图, 💡 行动建议, 🔗 相关概念) per the templates in `CLAUDE.md`. These live in user notes, not in chrome.

### What this design system uses

For polished design artifacts (slides, ui kit chrome, marketing surfaces), this design system pairs OVP's text-first chrome with **Lucide** loaded from the unpkg CDN — stroke width 1.5, neutral, low-contrast. Icons are sized 14–18px and colored `var(--muted)` for tertiary affordances or `var(--accent)` when actionable.

> **⚠️ Substitution flagged.** Lucide is *not* used in the OVP source. It's introduced here so designers don't hand-roll inconsistent SVGs. If the product later ships an icon system, replace this.

The `assets/` folder contains:

- `wordmark.svg` — *new*, designed for this system based on the brand vocabulary.
- `monogram.svg` — *new*, an "ovp" lockup.

These are not from the source repo (the repo has no logo). Treat them as proposals; ask the OVP author to confirm or replace.

---

## Caveats / open questions

- **No font files** in the source repo. Inter + JetBrains Mono are flagged substitutions.
- **No logo or brand mark** in the source repo. The wordmark and monogram in `assets/` are proposals.
- **No marketing site or external product page** — the entire surface is the local UI at `127.0.0.1:8787`. The design system invents nothing new for marketing surfaces; if the project ever grows one, this system needs an extension.
- **Dark mode is undefined.** `color-scheme: light` is the source-of-truth default. A **dark "deep tech" palette is provided here as a substitution** so the Atlas/graph view has somewhere to live: `--bg #0a0e16`, `--surface #11161f`, `--text #e6edf5`, `--accent #3b82f6` (blue), `--accent-2 #06b6d4` (cyan). All eight community swatches re-tune to higher-chroma equivalents. Ask the OVP author to confirm or revise.
- **Iconography** is the single biggest hole. The product is icon-free and a real icon decision needs the project author's input.
- **Animation** beyond meta-refresh is undefined. Prototype with restraint.

---

## Atlas / 3D cluster graph

A 3D force-directed cluster graph is provided at `ui_kits/ovp/graph.html` as the visual home for the `community_crystal` and contradiction views the source repo describes but does not render visually. It uses [3d-force-graph](https://unpkg.com/3d-force-graph) on top of three.js and pulls mock data from `graph-data.js`. Conventions:

- **Color = community** (one of `--c-1…--c-8`). **Shape = node type** — sphere for evergreen, icosahedron for deep dive, cube for topic, octahedron-with-halo for open question, large faceted sphere for collapsed super-node. **Size = quality + backlinks**.
- **Edge color = relation kind**: muted for `ref`, accent for `cite` (with directional particles), warn-amber for `contradict` (the only thicker stroke).
- **Disclosure** is the central interaction: clusters start collapsed as super-nodes; double-click expands (default), or via hover, or via camera distance. Switchable in the Tweaks panel.
- **Hulls** are translucent spheres around each community centroid, recomputed every 80ms from the live simulation.
- **Timeline** scrubs `absorbed_at`; pressing Play animates the vault's growth from the first note forward.
- The graph respects the same `data-theme` attribute as the rest of the system; toggling LIGHT/DARK in either shell updates both via `localStorage['ovp-theme']`.
