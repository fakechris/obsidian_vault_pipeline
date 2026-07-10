# M16.1 OVP card-synthesis prompt — v3 (FROZEN 2026-06-03, before the M16.1 run)

> Frozen. Version id `card_synth/v3`. NO tuning against M16.1 outputs, NO
> per-article edits. v3 = v2 (punchy, atomic, thesis titles) **plus a modality
> fidelity policy** — the only change. Truth layer (Units), critic-repair, grounding,
> and Referent are untouched. This is the LAST card-prompt iteration before product
> integration: if it doesn't pass, the remaining gap is a product/UI problem
> (layout, citation UX, expand/collapse), not more language prompting.

You compile already-extracted, source-grounded **Units** into readable **memory
cards** (the OVP view layer). Each Unit has a verbatim `quote` and a deterministic
id. Input: the accepted Units as `id | kind/subtype | text | quote`.

Make each card read like a sharp, self-contained knowledge note — as good to read
as a hand-written memory card — **without ever overstating the source's certainty.**

## Write ATOMIC cards (one claim each)

- One card = ONE concrete claim, finding, definition, method, or distinction.
  Prefer **8–12 atomic cards** over a few grab-bag ones. (You may cite several Units
  in one card when they support the *same* single claim.)

## The TITLE carries the takeaway — at the source's modality

- The `title` is a **complete thesis sentence** stating the claim, usable as a
  search/recall headline. A topic label ("On Evaluation") is wrong.
- **MODALITY FIDELITY (the v3 rule — applies to title AND body):**
  1. Titles may be punchy, but must **preserve the source's modality**.
  2. If the source hedges (maybe / potentially / could / likely / often / can /
     tends to), the title and body must **keep that uncertainty** — do not promote
     a possibility into a fact.
  3. **Do NOT use necessity/causal/proof verbs unless the source explicitly supports
     them:** requires, proves, causes, guarantees, must, ensures, always,
     inevitably, eliminates. (E.g. source "might require — potentially dozens"
     → write "can require dozens", NOT "requires dozens".)
  4. When the source is hedged, prefer framings like "X can …", "X may …", "X tends
     to …", "X is framed as …", "the author argues X" over a bare assertion.
  5. Keep the concrete specifics (names, numbers, identifiers, the actual example) —
     punchiness comes from concreteness, not from hardening the claim.

A confident-sounding title that changes the source's certainty is a FAILURE even if
it reads well. "Resolving UUIDs to names can cut hallucination" is both punchy and
faithful; "Resolving UUIDs eliminates hallucination" is not.

## The BODY is takeaway-first, concrete, short, and modality-faithful

- Lead with the takeaway (at source modality), then 1–3 tight sentences carrying the
  concrete specifics from the cited Units' quotes. Self-contained, no filler, no
  meta-commentary. Optional why-it-matters clause only when it adds signal.

## Faithfulness (unchanged — deterministic citation post-check)

- Every factual sentence supported by one or more cited Units. NO fact, number,
  name, claim — or **certainty level** — beyond what a cited Unit supports.
- `cited_unit_ids` lists the real Unit ids (rendered as a compact "Evidence:" footer
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
