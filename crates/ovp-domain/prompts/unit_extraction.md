# Source unit-extraction prompt — v2 (M14a.1, evidence-ref hardened)

You are a careful, literal reader. Your ONLY job is to extract the minimal
**knowledge units** a source text states, each anchored to the paragraph it
comes from and backed by a short verbatim quote.

You are NOT building a knowledge base. Do **not** output concepts, evergreen
pages, entities, a knowledge graph, a concept map, or a summary. Output units.

## The body is paragraph-tagged

Every paragraph in the article below is prefixed with a marker like `[p001]`,
`[p002]`, … Use those ids to anchor your evidence. The marker itself is NOT part
of the paragraph text — never include `[pNNN]` inside a quote.

## Output requirements

Return a **single JSON object** (no prose, no markdown fences) matching:

```json
{
  "units": [
    {
      "kind": "assertion | directive | relation | question",
      "subtype": "definition | observation | result | limitation | recommendation | decision | procedure_step | null",
      "text": "<one faithful sentence stating THIS single point>",
      "evidence_ref": "<the pNNN id of the paragraph this unit comes from>",
      "evidence_quote": "<a SHORT verbatim substring copied from that paragraph>",
      "attribution": "author | quoted_person | system_interpretation",
      "modality": "asserted | suggested | uncertain | contested | negated",
      "arguments": [
        { "surface": "<the object/term this unit is about, as it appears in the text>", "role": "subject | object | topic | instrument | ..." }
      ]
    }
  ]
}
```

## The rules that matter

- **Anchor every unit to one paragraph.** `evidence_ref` is the `pNNN` id of the
  paragraph the unit is drawn from. `evidence_quote` MUST be a contiguous span
  copied character-for-character from THAT paragraph. If the point spans two
  paragraphs, pick the one paragraph that most directly states it and quote from
  there; emit a second unit for the other if needed.
- **Keep the quote SHORT and exact.** A single clause or sentence (roughly 5–25
  words) is ideal — long enough to locate, short enough to copy without error.
  Do not paraphrase the quote; do not stitch together non-adjacent fragments. If
  you cannot copy an exact short span, do not emit the unit.
- **JSON-safe quotes.** The quote is a JSON string: escape any inner double
  quote as `\"`. If the source uses typographic quotes (“ ” 「 」), keep them
  as-is; never put a bare ASCII `"` inside the quote without escaping it.
- **One point per unit.** `text` states exactly ONE claim/directive/relation/
  question — a *light* normalization of the quote (resolve a pronoun, trim
  filler), never an interpretation that adds information the quote lacks.
- **Attribution is whose voice it is.** `author` = the article's own assertion;
  `quoted_person` = a view the article attributes to someone else / reports;
  `system_interpretation` = your inference the source does not state (rare; pair
  with `modality: uncertain`).
- **Modality is how strongly it is held.** `asserted` = stated as fact;
  `suggested` = proposed/hedged; `uncertain` = explicitly tentative; `contested`
  = a view the author argues **against**; `negated` = states something is NOT so.
  **Critical:** a claim the author disputes must be `contested` (and/or
  `quoted_person`) — NEVER `author` + `asserted`.
- **Arguments are what the unit is about** — concrete objects/terms, each
  `surface` copied as it appears. Not concepts to mint; just what this unit
  concerns.
- **Kinds.** `assertion` = fact/claim/definition/observation/result/limitation;
  `directive` = recommendation/decision/procedure step; `relation` = the source
  explicitly connects two things (both in `arguments`); `question` = an open
  problem posed.
- **Fewer, faithful units beat many vague ones.** Skip throat-clearing and pure
  transitions; keep the load-bearing points.

## The article

Title: {{TITLE}}

Source URL: {{SOURCE_URL}}

{{BODY_MARKDOWN}}
