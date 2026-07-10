# Cluster selection prompt — cluster_select/v1

You are scouting for **one cross-source synthesis cluster** inside a knowledge
corpus. Below is a **seed** source (currently not covered by any durable
claim) and its nearest **neighbors** by content similarity. Each entry is a
compact digest: `case_id`, the source title, and its card titles.

Your job: decide whether some of these sources — read together — could support
at least ONE claim-worthy **cross-source** synthesis (a finding a careful
reader could only state by drawing on several of these sources at once). If
yes, pick the case ids that form that single, coherent cluster. If not,
**refuse** — "no opportunity here" is a first-class, correct answer, and a
refusal is strictly better than a forced grab-bag.

## Rules

1. Select between `min_cases` and `max_cases` distinct `case_id`s, copied
   **verbatim** from the digests below. Never invent or edit an id.
2. Prefer clusters that **include the seed** — it is the coverage target. You
   may exclude the seed only when its neighbors form a genuinely stronger
   cluster that the seed does not belong to.
3. **Content diversity is mandatory.** The selected cases must approach a
   shared question from different sources or angles. Do NOT select
   near-duplicates of one another (same article re-clipped, translations of
   one piece, serialized parts of one post) unless independent corroboration
   is exactly the point — a cluster of rehashes yields a trivial claim.
4. Pick ONE cluster only: the tightest claim-worthy subset, not "everything
   vaguely related". Smaller and sharper beats larger and mushier.
5. Refuse when: the neighborhood is topically scattered; fewer than
   `min_cases` cases genuinely intersect; or the only common ground is too
   generic to support a claim worth keeping.
6. Output **only** JSON — no prose, no markdown fence.

## Output shape

Either a selection:

```json
{
  "selected_case_ids": ["<case_id>", "<case_id>", "<case_id>"],
  "rationale": "one sentence: the cross-source question this cluster can answer"
}
```

or a refusal:

```json
{
  "refuse": true,
  "reason": "one sentence: why no claim-worthy cluster exists here"
}
```

## Corpus
