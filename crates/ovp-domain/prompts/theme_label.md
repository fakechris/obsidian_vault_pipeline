# Theme labeling prompt — theme_label/v2

You are naming ONE topic community discovered by clustering a bilingual
(English + 中文) knowledge corpus. You get the community's distinguishing
keywords (c-TF-IDF, deterministic), member titles sampled across the WHOLE
community (from its most typical members to its edges), and some of the
claims a reader sees under this theme.

Produce a short, human-readable theme name in BOTH languages.

Rules:

1. **Name what most members share.** The name must fit the majority of the
   titles and claims, not one or two of them. A topic that only a couple of
   titles mention does not belong in the name. When the members are
   diverse, name their common thread at the level of generality that still
   covers them (e.g. "Open-source AI developer tools"), rather than a
   specific subtopic.
2. **Short.** ≤ 6 English words; ≤ 12 Chinese characters. No trailing period.
3. **Name the topic, not the corpus.** Never use words like "cluster",
   "community", "documents", "articles", "various", "misc".
4. The Chinese name is a natural rendering of the same topic, not a
   transliteration. Keep established technical terms (Claude Code, MCP, RAG,
   Polymarket…) in their original form in both names.
5. Output **only** JSON — no prose, no markdown fence:

```json
{"label": "<English name>", "label_zh": "<中文名>"}
```

## Community

(keywords, member titles and claims follow)
