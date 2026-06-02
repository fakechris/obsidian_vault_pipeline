# Copy-only probe (M14a.4 Step 1)

This is a **COPY task, not a writing task.** Below are numbered text spans.

For EACH span id listed, copy ONE contiguous substring of that span's text,
**verbatim** — character-for-character.

Hard rules:
- Copy an exact contiguous run of characters that appears in the span. Do NOT
  summarize, paraphrase, translate, or re-order.
- Do NOT change punctuation. Keep Chinese punctuation (；、：。！？「」) exactly
  as shown. Do NOT turn it into commas or natural sentences.
- Do NOT merge multiple list items or clauses into one quote. Pick ONE
  contiguous fragment from within a single span.
- Keep it short (a clause or sentence). If the span is long, copy any one
  contiguous short fragment of it.
- Never include the `[id]` marker in the quote.

Return a single JSON object (no prose, no fences):

```json
{ "copies": [ { "span_id": "p001.s001", "quote": "<verbatim substring of that span>" } ] }
```

## Spans

{{SPANS}}
