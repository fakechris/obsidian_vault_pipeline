# M15 OVP card-synthesis prompt — v1 (FROZEN 2026-06-03, before any eval run)

> Frozen per docs/stage-m15-methodology-audit.md "Frozen prompt / no-tuning rule".
> Exactly one synthesis prompt. NO tuning against M15 outputs. NO per-article
> edits. Only parse/transport fixes (with a noted re-run). Version id: `card_synth/v1`.

You compile a set of already-extracted, source-grounded knowledge **Units** into a
small set of readable **memory cards** — the OVP "view layer". The Units are the
truth layer: each has a verbatim `quote` from the source and a deterministic id.

You are given the accepted Units as JSON lines: `id | kind/subtype | text | quote`.

Produce **5–8 memory cards**. Each card groups related Units into one
self-contained, useful note.

Hard constraints (a deterministic post-check enforces the citation ones):
- `content` MAY paraphrase / reorganize for readability and flow.
- But every factual sentence in `content` must be supported by one or more of the
  Units you cite in `cited_unit_ids`. **Introduce NO fact, number, name, or claim
  that is not in a cited Unit.** When in doubt, leave it out.
- `cited_unit_ids` lists the Unit ids the card is built from (must be real ids from
  the input). A card with no citable Units is not a card — drop it.
- Group co-referential / same-topic Units into ONE card; do not emit one card per
  Unit and do not split one topic across many cards.
- A card is a *memory* a reader would want to keep: a definition, a finding, a
  method, a recommendation, a key distinction. Not meta-commentary about the article.

Return a SINGLE JSON object, no prose, no fences:

```json
{
  "cards": [
    {
      "title": "<short, specific>",
      "content": "<readable note; every factual sentence backed by a cited unit>",
      "unit_type": "fact | definition | procedure | finding | recommendation | distinction",
      "cited_unit_ids": ["u-...", "u-..."]
    }
  ]
}
```
