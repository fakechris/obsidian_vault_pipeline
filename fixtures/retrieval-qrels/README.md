# Retrieval qrels — frozen ground truth

44 hand-adjudicated relevance judgements for `ovp2 retrieval-eval`. Same role as
the rest of `fixtures/`: the thing the system is measured against, not a sample
of its output. Schema and bucket semantics live in `docs/design/retrieval-eval.md`.

```
gold/      34 questions — the optimization set. Tune against these.
holdout/   10 questions — frozen 2026-08-14, BEFORE any tuning.
```

## Why these are in the repo

They were produced on 2026-08-13/14 by a deterministic digest of real
`ask-sessions` transcripts → cheap-model draft → operator adjudication, and they
lived only in gitignored `.run/retrieval-eval/`. That is hours of human judgement
sitting on one untracked disk. Every retrieval number this project reports is
measured against them, so losing them means losing the ability to compare against
any past result — the numbers become unfalsifiable.

## The holdout rule

**Never** use `holdout/` to choose fusion weights, thresholds, stop-words, query
modes, or any other knob. It is scored once, at the acceptance of a candidate,
and the result goes in the evolution ledger. Selection was deterministic
(sha256 of the id), stratified across classes, and excluded every question that
had already been analysed individually.

A holdout you consult while tuning is not a holdout — it is a second training
set that reports flattering numbers. `qrels_gold_and_holdout_stay_disjoint` in
`crates/ovp-cli/src/commands/retrieval_eval.rs` enforces the split mechanically
so the rule survives a copy-paste; it cannot enforce the discipline of not
looking, which stays on the operator.

## Bucket sizes are small on purpose

`docs/design/retrieval-eval.md` §2: 50–80 questions, not 300 — a single-operator
dogfood product cannot sustain weeks of annotation, and an abandoned 300-question
set is worth less than a finished 44. Several buckets hold fewer than 5
questions (`meta`, `source_scoped`, `negative`, `recent`). Per §4 those buckets
are **reported, not adjudicated**: read them as a smoke signal, never as a
verdict on a change.

## Running

```bash
ovp2 retrieval-eval --vault-root <vault> --qrels fixtures/retrieval-qrels/gold --gold-only
```

Both directories mix `q-*.json` (from directly adjudicated sessions) and
`s-*.json` (silver drafts promoted to gold confidence). They are one population;
the prefix is provenance, not a tier. Do not filter on it — a prefix whitelist
once silently dropped the `s-*` half and a 44-question run reported itself
complete at 14.
