# article_mixed_lang — AI Readiness Gap

## Why this fixture

Stresses three things that article_clean doesn't:

1. **Source rewrite**: raw `source` is a Twitter post (`x.com/dotey/status/...`), interp `source` is the underlying Daniel Miessler article (`danielmiessler.com/...`). The legacy pipeline **followed the Twitter thread's reference** to the original article and used THAT as canonical. The new system must either replicate this or document why not.
2. **Title rewrite**: raw `title` is the Chinese-Twitter title `"大多数公司根本没有为 AI 做好准备"`. Interp `title` is a completely different Chinese rephrasing: `"AI Readiness Gap：组织清晰度决定 AI 应用成败"` — interp added a concept tag (`AI Readiness Gap`) and a subtitle. **This is reframing, not translation.**
3. **Canonical/candidate split**: interp has 2 `canonical_concepts` (`ai-agent`, `competitive-advantage`) and 4 `concept_candidates`. Tests that the absorb step has a real two-tier model, not just "extract everything".
4. **UTF-8 throughout**: tags mix English + Chinese, body is mostly Chinese with English brand names. Tests that the new pipeline handles UTF-8 cleanly end-to-end (slug generation, frontmatter quoting, file naming).

## Pairing

| Role | Path in legacy vault |
|---|---|
| Raw input | `50-Inbox/03-Processed/2026-05/2026-05-05_dotey_-_大多数公司根本没有为_AI_做好准备.md` |
| Interpretation | `20-Areas/AI-Research/Topics/2026-05/2026-05-05_大多数公司根本没有为 AI 做好准备_深度解读.md` |

Pairing key: filename slug matches across raw + interp, even though `source` URLs differ.

## Contract: MUST preserve

- Two-tier extraction: `canonical_concepts` (resolved → existing evergreen) vs `concept_candidates` (proposed, not yet promoted).
- Both lists are slug strings, not free-form names.
- Source canonicalization: the system should follow Twitter-thread references to the underlying article URL when possible (or at minimum, flag the divergence in events for review).
- UTF-8 in `title`, `tags`, body, and **filenames**.

## Contract: SHOULD preserve

- The reframed title pattern (`<English-concept-handle>：<Chinese-subtitle>`) is a stylistic legacy choice. The new system MAY use a different style (e.g. always-Chinese, or always source-language), as long as it's consistent.
- Twitter-clipping-specific raw fields (`author_handle`, `followers`, `mutuals_top`, etc.) — interesting metadata but the legacy pipeline drops most of them. The new system MAY keep them as a typed `SourceProvenance` block.

## Contract: MAY break

- `original_note_type: analysis` (vs `ai` in article_clean). Legacy routing artifact.
- Whether the rewritten title appears in the interpretation's H1 or just frontmatter.

## Open question

Why did the legacy pipeline resolve a Twitter clipping to the underlying article? Two possibilities:
- (a) Deliberate: the Twitter thread embedded a longer article, and the interpreter chose to use that.
- (b) The Twitter raw was reprocessed against the original article URL post-clipping.

The new system should make this **explicit** — a `SourceResolution` step that emits an event when the canonical URL changes, so it's auditable.
