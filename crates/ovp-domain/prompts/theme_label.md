# Theme labeling prompt — theme_label/v1

You are naming ONE topic community discovered by clustering a bilingual
(English + 中文) knowledge corpus. You get the community's distinguishing
keywords (c-TF-IDF, deterministic) and a few representative document titles.

Produce a short, specific, human-readable theme name in BOTH languages.

Rules:

1. **Be specific, not generic.** "Agent memory systems" beats "AI"; "Prediction
   markets & quant trading" beats "Finance". Use the keywords + titles to find
   the community's actual center of gravity.
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

(keywords + representative titles follow)
