# Crystal synthesis prompt — crystal_synth/v1

You are compiling **cross-source synthesis claims** for a durable knowledge
Crystal. The input below is a set of already-extracted, source-grounded **Units**
grouped by their source **case**. Each Unit has a deterministic `unit_id`, a
1-based source `line`, and a verbatim `quote` that was already source-verified.

Your job: propose a small set of **claims** that a careful reader could draw
*across* these sources — each claim backed ONLY by verbatim quotes from the
Units below. This is a truth layer, not a summary: **set no claim you cannot
cite.**

## Hard rules (a claim that breaks any rule is worthless here)

1. **Every claim MUST carry citations.** Each citation is `{case_id, unit_id,
   quote}` where:
   - `case_id` is the exact case key shown below (e.g. `m18-01`).
   - `unit_id` is the exact id of a Unit under that case (copy it verbatim).
   - `quote` is a **verbatim substring** (≥12 characters) of that Unit's `quote`.
     Do not paraphrase, do not merge two quotes, do not add words.
2. **Prefer cross-source claims.** A durable claim should draw on **≥2 distinct
   cases** where the evidence genuinely supports it. A single-source observation
   is allowed but will be kept only as a caveated insight, not durable truth.
3. **Do not overstate.** Preserve the source's modality — if the quotes hedge
   (may / can / tends to / often), the claim must keep that hedge. Do not use
   necessity/causal/proof verbs (requires, proves, causes, guarantees, must,
   always) unless a cited quote explicitly supports them.
4. **Record tension.** If sources disagree or a claim has a real limit, put it
   in the claim's `caveat` string (optional).
5. Output **only** JSON — no prose, no markdown fence.

## Output shape

```json
{
  "claims": [
    {
      "id": "1",
      "claim": "A complete thesis sentence stating the cross-source finding.",
      "theme": "the cluster theme (copy the theme given below)",
      "citations": [
        { "case_id": "m18-01", "unit_id": "u-000-abcd1234", "quote": "verbatim span from that unit" },
        { "case_id": "m18-02", "unit_id": "u-003-ef567890", "quote": "verbatim span from that unit" }
      ],
      "caveat": "optional — counter-evidence, limit, or cross-source tension"
    }
  ]
}
```

Use short numeric `id`s (`"1"`, `"2"`, …); the harness namespaces them per
cluster. Aim for a handful of sharp, well-cited claims over a long grab-bag.

## Cases
