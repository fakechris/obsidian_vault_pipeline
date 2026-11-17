# Theme labeling prompt — theme_label/v3

You are naming ONE topic community discovered by clustering a bilingual
(English + 中文) knowledge corpus. You get the community's distinguishing
keywords (c-TF-IDF, deterministic), member titles sampled across the WHOLE
community (from its most typical members to its edges), some of the claims a
reader sees under this theme, and the names other themes already carry.

Produce a short, human-readable theme name in BOTH languages.

Rules:

1. **Name what most members share — as specifically as that allows.** The
   name must fit the majority of the titles and claims, not one or two of
   them: a topic only a couple of titles mention does not belong in it.
   Within that, prefer the most specific common thread over a generic one.
2. **Distinct from the other themes.** When names already used by other
   themes are listed, yours must make clear what sets THIS theme apart from
   all of them — its distinguishing subject, not a shared umbrella. Do not
   reuse their generic head words (e.g. another "AI Agent …") when a more
   specific term fits.
3. **Short.** ≤ 6 English words; ≤ 12 Chinese characters. No trailing period.
4. **Name the topic, not the corpus.** Never use words like "cluster",
   "community", "documents", "articles", "various", "misc".
5. The Chinese name is a natural rendering of the same topic, not a
   transliteration. Keep established technical terms (Claude Code, MCP, RAG,
   Polymarket…) in their original form in both names.
6. Output **only** JSON — no prose, no markdown fence:

```json
{"label": "<English name>", "label_zh": "<中文名>"}
```

## Community

(keywords, member titles, claims and names already in use follow)
