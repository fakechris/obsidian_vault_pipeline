# Card synthesis prompt — card_synth/v3 (FROZEN; canonical copy of docs/m16/card-synthesis-prompt.v3.md)

You compile already-extracted, source-grounded **Units** into readable **memory
cards** (the OVP view layer). Each Unit has a verbatim `quote` and a deterministic
id. Input below: the accepted Units as `id | kind/subtype | text | quote`.

Make each card read like a sharp, self-contained knowledge note — as good to read
as a hand-written memory card — **without ever overstating the source's certainty.**

## Write ATOMIC cards (one claim each)

- One card = ONE concrete claim, finding, definition, method, or distinction.
  Prefer **8–12 atomic cards** over a few grab-bag ones. (You may cite several Units
  in one card when they support the *same* single claim.)

## The TITLE carries the takeaway — at the source's modality

- The `title` is a **complete thesis sentence** stating the claim, usable as a
  search/recall headline. A topic label ("On Evaluation") is wrong.
- **MODALITY FIDELITY (applies to title AND body):**
  1. Titles may be punchy, but must **preserve the source's modality**.
  2. If the source hedges (maybe / potentially / could / likely / often / can /
     tends to), the title and body must **keep that uncertainty**.
  3. **Do NOT use necessity/causal/proof verbs unless the source explicitly supports
     them:** requires, proves, causes, guarantees, must, ensures, always,
     inevitably, eliminates.
  4. When hedged, prefer "X can …", "X may …", "X tends to …", "X is framed as …".
  5. Keep concrete specifics (names, numbers, identifiers, the actual example);
     punchiness comes from concreteness, not from hardening the claim.

## The BODY is takeaway-first, concrete, short, and modality-faithful

- Lead with the takeaway (at source modality), then 1–3 tight sentences carrying the
  concrete specifics from the cited Units' quotes. Self-contained, no filler.

## Faithfulness (deterministic citation post-check)

- Every factual sentence supported by one or more cited Units. NO fact, number,
  name, claim, or certainty level beyond what a cited Unit supports.
- `cited_unit_ids` lists the real Unit ids (rendered as an "Evidence:" footer
  downstream); do NOT inline `[u-...]` markers in `content`.

Return a SINGLE JSON object, no prose, no fences:

```json
{
  "cards": [
    {
      "title": "<a complete thesis sentence at the source's modality>",
      "content": "<takeaway-first, concise, concrete, modality-faithful note>",
      "unit_type": "fact | definition | procedure | finding | recommendation | distinction",
      "cited_unit_ids": ["u-...", "u-..."]
    }
  ]
}
```

## Accepted Units
