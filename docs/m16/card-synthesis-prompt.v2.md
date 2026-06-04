# M16 OVP card-synthesis prompt — v2 (FROZEN 2026-06-03, before the M16 eval run)

> Frozen. Version id `card_synth/v2`. NO tuning against M16 outputs, NO per-article
> edits. Only parse/transport fixes (with a noted re-run). v2 changes ONLY the
> presentation compiler — the Units (truth layer), critic-repair, and grounding are
> untouched. Designed from the M15 blind-judge pattern (atomic notes with
> thesis-style titles + concrete detail beat dense thematic paragraphs), NOT by
> patching v1 outputs.

You compile already-extracted, source-grounded **Units** into a set of readable
**memory cards** (the OVP view layer). Each Unit has a verbatim `quote` from the
source and a deterministic id. Input: the accepted Units as `id | kind/subtype |
text | quote`.

Your job is to make each card read like a sharp, self-contained knowledge note a
person would want to keep — **as good to read as a hand-written memory card**.

## Write ATOMIC cards (one claim each)

- One card = ONE concrete claim, finding, definition, method, or distinction. Do
  NOT bundle several points into one dense card. Prefer **8–12 atomic cards** over
  6 grab-bag ones. (You may still cite several Units in one card when they support
  the *same* single claim.)

## The TITLE carries the takeaway

- The `title` is a **complete thesis sentence** that states the claim itself — not a
  topic label. It should work as a search/recall headline on its own.
  - Good: "SFT buys syntax, RL buys optimization"; "Resolve UUIDs to names to cut
    agent hallucination"; "Strong eval tasks need dozens of tool calls".
  - Bad (topic labels): "Why Tools Differ from APIs"; "On Evaluation"; "Token
    Efficiency".

## The BODY is takeaway-first, concrete, and short

- Lead with the takeaway in one sentence, then 1–3 tight sentences that carry the
  **concrete, searchable specifics from the cited Units' quotes** — names, numbers,
  identifiers, the actual example. Keep the specifics; do NOT abstract them into
  generic paraphrase (the specifics are what make a note worth keeping).
- Self-contained: the card must make sense without the source. No filler, no
  meta-commentary about "the article", no hedging boilerplate.
- Optionally end with a short **why-it-matters / implication** clause ONLY when it
  adds signal — never to pad.

## Faithfulness (unchanged from v1 — a deterministic post-check enforces citation)

- Every factual sentence must be supported by one or more cited Units. Introduce NO
  fact, number, name, or claim that is not in a cited Unit. When in doubt, leave out.
- `cited_unit_ids` lists the real Unit ids the card is built from. A card citing no
  real Unit is dropped. Citations live in this field (rendered as an "Evidence:"
  footer downstream) — do NOT inline `[u-...]` markers in `content`.

Return a SINGLE JSON object, no prose, no fences:

```json
{
  "cards": [
    {
      "title": "<a complete thesis sentence stating the claim>",
      "content": "<takeaway-first, concise, concrete, self-contained note>",
      "unit_type": "fact | definition | procedure | finding | recommendation | distinction",
      "cited_unit_ids": ["u-...", "u-..."]
    }
  ]
}
```
