# Theme page prompt — theme_page/v1

You are writing ONE topic page for a personal knowledge base. You get a
topic's distinguishing keywords and a list of DURABLE CLAIMS — statements
that already passed an evidence gate (each is backed by verbatim quotes from
multiple sources). The claims are the ONLY material you may use.

Weave the claims into a short, readable wiki page: group related claims into
sections, connect them into flowing prose, surface tensions between claims
where they exist. Do NOT add outside knowledge, examples, or conclusions the
claims do not support.

Rules:

1. **Every paragraph must cite its claims.** After each statement, cite the
   claim(s) it comes from as `[claim:<key>]`, using the exact keys given in
   the input (e.g. `[claim:ck-1a2b3c4d5e6f7a8b]`). A paragraph with no
   citation will be rejected by a deterministic verifier.
2. **Only the given claims.** Never invent a key; never cite anything else.
   You do not have to use every claim — prefer a coherent page over full
   coverage — but unused claims are reported, so drop one only when it truly
   does not fit.
3. **Structure.** 2–5 sections. Each section: a short heading and 1–3
   paragraphs. No introduction restating the topic name; start with substance.
4. **Language.** Write in the dominant language of the claims (English or
   中文). Keep established technical terms (Claude Code, MCP, RAG …) in their
   original form.
5. **Tone.** Factual and compact — a reference page, not an essay. No
   "this theme covers…" meta-prose.
6. Output **only** JSON — no prose, no markdown fence:

```json
{"sections": [{"heading": "<heading>", "body": "<paragraphs separated by \n\n, citations inline>"}]}
```

## Topic

(keywords + claims follow)
