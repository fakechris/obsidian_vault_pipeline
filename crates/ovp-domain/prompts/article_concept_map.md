# Article concept-map prompt — v2

You are a careful technical reader. Given an article, produce ONE JSON object
with two parts: (1) the existing article-level synthesis (for the primary note),
and (2) a **concept map** — the source-grounded concepts the article actually
develops, each carrying its OWN definition, evidence, and claims.

## Output requirements

Return a **single JSON object** (no prose, no markdown fences) matching:

```json
{
  "title": "<concise title for the interpretation>",
  "tags": ["<3-7 substantive topic tags>"],
  "dimensions": {
    "one_liner": "<one-sentence summary of the ARTICLE as a whole; ≥20 chars>",
    "explanation": { "what": "<2-4 sentences>", "why": "<2-4 sentences>", "how": "<2-4 sentences>" },
    "details": ["<≥3 specific, verifiable points from the article>"],
    "structure": null,
    "actions": ["<one or more concrete suggestions>"]
  },
  "concepts": [
    {
      "slug": "<lowercase-hyphenated-ascii or preserved CJK>",
      "title": "<Human Title>",
      "aliases": ["<alternative slug a reader might use>"],
      "kind": "concept | principle | procedure | taxonomy | system | claim",
      "definition": "<one sentence defining THIS concept specifically>",
      "evidence": ["<short quote or close paraphrase from the article BODY>"],
      "claims": ["<a source-backed claim that belongs to THIS concept>"],
      "related": ["<slug of a related concept>"],
      "merge_with": ["<slug this concept should merge into, if it is a synonym>"],
      "reject_reason": "<why this should NOT be its own note, or null>",
      "promote": true
    }
  ]
}
```

## Concept rules (the part that matters)

- **Grounded.** Every concept must be developed in the article *body* (not the
  title, frontmatter, or author bio). Put a real `evidence` quote on each.
- **Slug = the article's own name, made filename-safe.** Derive each `slug` from
  the noun phrase the **article itself uses** for the concept — a section
  heading, a repeated term-of-art — not a meta-label you invent. Do not add an
  interpretive qualifier the article does not use, and do not drop one it does.
  Then reduce that phrase to a filename-safe handle: lowercase ASCII words joined
  by single hyphens, no spaces / slashes / punctuation (`` : * ? " < > | / \ ``);
  for a CJK term keep the CJK characters with no spaces or separators. The
  article's exact wording lives in `title` and `definition`; the `slug` is only
  the handle.
- **Keep proper nouns; never genericize a named system.** If the article
  attributes a mechanism, layer, or multi-stage process to a **named** product,
  company, or coined term that recurs in the body (a proper noun), the `slug`
  MUST be that proper noun — never a generic descriptor for it (do not turn a
  named system into `pipeline-architecture` / `data-pipeline`). The named thing
  is the single owner of its own stages/sub-parts: record those stages as its
  `claims`, and put the article's attribution (who built it, where it sits) in
  its `definition`.
- **Concept-specific definition, in the article's own mechanism words.**
  `definition` must define THAT concept by its **own** distinguishing mechanism,
  using the article's operative wording for it (the concrete mechanism, named
  quantity, or imperative the article uses for THIS concept) — not the article
  thesis (`dimensions.one_liner`) and not a higher-altitude re-abstraction into
  generic domain jargon. Two different concepts must have two different
  definitions. **Never** define a concept by contrast to a neighbor, and never
  pull a sibling concept's signature words or named outputs into this one's
  definition — if a phrase is what makes the sibling the sibling, it belongs only
  in the sibling's note.
- **Owned claims, with the article's concrete anchors.** Each `claims` entry must
  be about that concept, not a generic article fact, and must not attach
  author/employer/client/product metadata. When the article pins this concept
  with a concrete anchor — a specific count, a named quantity, a bright-line
  imperative, a vivid phrase — keep at least one of those **exact** article
  phrases on this concept (in its `definition` or a `claim`) rather than softening
  it to generic vocabulary; a sharp number must not become "a small set".
- **Background classification is not a concept.** A general taxonomy the article
  cites as background — stock categories borrowed from another source, a field's
  standard buckets, a competitor list — is NOT a developed concept. Do **not**
  mint each member as its own note. Capture it as at most ONE taxonomy note (or
  leave it to the primary note); mint a member separately only if the article
  independently **develops** that member with its own definition, evidence, and
  argument.
- **Fewer, correct, distinct.** Prefer 6–12 sharp concepts over many noisy ones.
  Collapse synonyms (set `merge_with`) instead of minting duplicates. Reject
  umbrella/grab-bag labels (set `promote: false` + a `reject_reason`).
- **Weak evidence → not a note.** If a point is real but only mentioned in
  passing, set `promote: false` (it stays in the primary note synthesis only).
- **No marketing/frontmatter numbers as definitions.** A figure that appears only
  in the intro/marketing blurb and is not supported by the body must not become a
  concept's definition or claim.
- The `dimensions` block is the article-level synthesis for the primary note and
  may stay broad; the concept map is where precision lives.

### Anti-patterns to avoid (general, not article-specific)

- An "opposite-of" concept must be defined as itself, not as its opposite (e.g. a
  "benchmark maxxing" concept must describe chasing benchmark scores, not the
  "floor raising" philosophy it is contrasted with).
- A concept named for an inspection/technique must carry evidence specific to it
  (e.g. a "trajectory inspection" concept needs trajectory / tool-call /
  retrieved-context evidence, not a generic claim).
- Do not mint several near-identical concepts for one idea (e.g. one unit concept,
  plus "knowledge unit", plus "retrieval" as three notes) — merge them.
- A taxonomy whose members are not independently developed should be ONE taxonomy
  concept, not one note per member.
- A figure that the article body does not support must not be promoted as a fact.

## The article

Title: {{TITLE}}

Source URL: {{SOURCE_URL}}

{{BODY_MARKDOWN}}
