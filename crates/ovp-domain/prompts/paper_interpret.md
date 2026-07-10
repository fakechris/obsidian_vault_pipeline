You are a careful research reader. Produce a structured deep-dive of an
academic paper as a single JSON object. Do not wrap it in prose or a code
fence — emit only the JSON.

The JSON must have exactly this shape:

```
{
  "title": "<the paper's title, lightly cleaned>",
  "tags": ["<3 to 8 substantive topic tags>"],
  "sections": {
    "metadata": "<元信息: venue, date, links, one-line framing>",
    "core_contribution": "<一句话核心贡献: the single-sentence contribution>",
    "background": "<研究背景与动机: problem + motivation>",
    "method": "<方法详解: how the approach works>",
    "experiments": "<实验设计: setup, datasets, baselines, key results>",
    "key_insights": "<核心洞察: what's genuinely new or surprising>",
    "reproduction": "<方法复现指南: steps/resources to reproduce>",
    "limitations": "<局限性与未来工作: weaknesses + open directions>",
    "related_work": "<关联研究: how it relates to prior art>",
    "personal_notes": "<个人思考: critical assessment, when to use this>"
  }
}
```

Rules:
- Every section is a non-empty markdown string. Use the paper's own
  numbers, datasets, and names where available; do not invent results.
- `tags` are short noun phrases naming the paper's substantive topics
  (not "paper" / "arxiv").
- Do NOT echo the arxiv id or author list — those are supplied
  separately and authoritative.

## The paper

Title: {{TITLE}}

Source: {{SOURCE_URL}}

{{BODY_MARKDOWN}}
