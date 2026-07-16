# Tag classification (tag_classify/v1)

You assign content tags to knowledge sources from a CLOSED vocabulary.

Rules:
1. For each source, pick 1–5 tags. Every picked tag MUST be copied verbatim
   from the Vocabulary list — never invent, translate, re-spell, pluralize,
   or reformat a vocabulary tag.
2. Pick the most SPECIFIC applicable tags. Add a generic tag (like `ai`)
   only when nothing more specific fits.
3. If an important topic recurs in this batch and truly has no vocabulary
   tag, you may propose it in the batch-level `new_tags` list (lowercase,
   hyphenated, at most the number stated in the batch header). Do NOT use a
   proposed tag in any source's `tags` — proposals are reviewed separately.
4. A tag must describe the CONTENT, never the capture channel or format.
5. Reply with JSON only, no prose:
   `{"sources": [{"id": 0, "tags": ["..."]}, …], "new_tags": ["..."]}`
   Every input source id must appear exactly once.

## Batch
