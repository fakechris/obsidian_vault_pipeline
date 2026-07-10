# Fixture Survey (B1)

Read-only survey of `~/Documents/ovp-vault` to pick fixtures for the OVP Next contract. No legacy Python was run; only `.md` files inspected.

## Vault shape (relevant slices only)

- **Raw inputs** live in `50-Inbox/03-Processed/YYYY-MM/`. Always a `.md` with YAML frontmatter. Filename pattern is `YYYY-MM-DD_<slug>.md`.
- **Article interpretations** live in `20-Areas/AI-Research/Topics/YYYY-MM/`. Filename: `YYYY-MM-DD_<title>_深度解读.md`.
- **Tool/project interpretations** live in `20-Areas/Tools/Topics/YYYY-MM/`. Same filename pattern.
- **Paper interpretations** live in `20-Areas/AI-Research/Papers/`. Same pattern, no month subdirectories.
- **GitHub repo raws** stop at the raw layer in some cases — no `_深度解读.md` produced. The raw itself contains frontmatter like `source_type: github-project`, `github_stars`, `deepwiki_section_count`.

## Frontmatter divergence between source kinds

| Field | article raw | paper raw | github raw |
|---|---|---|---|
| `source` | URL | `arxiv.org/abs/<id>` | `github.com/<owner>/<repo>` |
| `source_type` | (absent) | `arxiv-paper` | `github-project` |
| `source_tier` | (absent) | `arxiv-api` | `deepwiki` |
| `arxiv_id` | — | present | — |
| `github_owner` / `_repo` / `_stars` | — | — | present |
| `source_published_at` | sometimes | present | — |
| `source_fetched_at` | sometimes | present | present |
| Authors | `author:` (string) | `source_authors:` (list) | — |

The new system's `SourceDoc` needs to model these as **a tagged-union by `source_type`**, not a flat struct with optional fields.

## Interpretation shape divergence

| Aspect | article interp | paper interp |
|---|---|---|
| Frontmatter style | flat YAML | YAML wrapped in code fence (legacy quirk) |
| Title field | reframed / translated title | usually keeps paper title |
| Structure | "一句话定义 / 详细解释 / 重要细节 / ..." (6 dimensions) | "元信息 / 摘要 / 核心贡献 / ..." (9 sections, table-heavy) |
| `canonical_concepts` | list of resolved evergreen slugs | list of resolved evergreen slugs |
| `concept_candidates` | list of pending evergreen slugs | list of pending evergreen slugs |
| `original_note_type` | `ai`, `analysis`, `tools`, ... | (often absent — paper has its own routing) |
| `confidence` | (absent) | `5/5` style |
| `category` | (absent) | e.g. `systems-paper` |

→ The interpretation contract is **per-source-kind**. One interpreter per kind, sharing only the envelope frontmatter (title, source, date, tags).

## Chosen fixtures

1. **`article_clean`** — `A Guide to Agent-native Product Management`
   - Source URL: `https://every.to/guides/ai-product-management-guide`
   - Why: cleanest representative case. Single resolved title, structured 6-dim interp, English-only, ~13 evergreens extracted, 0 canonical_concepts (everything is candidate).

2. **`article_mixed_lang`** — `大多数公司根本没有为 AI 做好准备` (English source, Chinese interp title)
   - Source URL: `https://danielmiessler.com/blog/most-companies-arent-ready-for-ai`
   - Why: stresses the title-mutation case (interp title is NOT a translation of source title — it's a reframing), and exercises the **canonical vs candidate** split (2 canonical, 4 candidates). Tests UTF-8 throughout.

3. **`paper_arxiv`** — `Deep GraphRAG: A Balanced Approach to Hierarchical Retrieval`
   - Source: arXiv 2601.11144
   - Why: clear paper-shape contract — different frontmatter on both raw + interp, multi-author, structured paper sections.

4. **`github_enriched_raw`** — `jordanbaird/Ice`
   - Source: `https://github.com/jordanbaird/Ice`
   - Why: captures the **"raw without interpretation"** case. The system stops at enriched raw for some github inputs. The new code needs to know this is a legal terminal state, not a missing step.

## What was NOT captured

- **Pinboard import** → not yet in scope; v0.1 only needs to read processed inputs, not poll external sources.
- **Image attachments** → none of the picked cases include images. Add a 5th fixture later if image handling becomes a question.
- **MOC update** → the legacy MOC files are derived state; not part of the source-to-interpretation contract.
- **Evergreen pages** → captured indirectly via `canonical_concepts` slugs. The actual evergreen .md files belong to a separate contract (post-C).

## Open questions

- Is github "raw-only" a deliberate routing decision, or a backlog state? If routing, the new system needs a `RoutingDecision::StopAtRaw` outcome. If backlog, the github interpreter just hasn't been built.
- Paper interp frontmatter wrapped in code fence — quirk to preserve, or a parser glitch in the old code that should be fixed in the new one? **Recommendation: fix in the new one, treat as MAY-BREAK.**
