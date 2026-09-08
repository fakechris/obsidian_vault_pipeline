---
name: claim
description: Read one durable claim’s full evidence closure (text, gate verdicts, verbatim quotes and sources). Use to audit any [claim:…] citation from ask or theme pages.
---

# OVP Claim

Use the ovp2 MCP `claim` tool to load one durable claim’s **full evidence closure**: claim text, gate verdicts, and every citation resolved to verbatim quote, line, and source (title/sha/url).

## When to use

- Auditing a `[claim:…]` citation from `ask` or a theme page
- Resolving `ovp://claim/<key>` or a `ck-…` claim_key
- Checking whether an answer’s receipt is real before trusting it

## Arguments

| Arg | Required | Notes |
|-----|----------|-------|
| `key` | yes | `claim_key` (preferred, stable), `claim_id`, or `ovp://claim/<key>` |

## Behavior

1. Call MCP `claim` with the key from the citation.
2. Present claim text, verdicts, and quote→source links clearly.
3. If the key does not resolve, say so — do not invent evidence.

## Notes

- `claim_key` values (`ck-…`) are deterministic and survive re-runs; safe to store in notes and answers.
- Same payload as the `ovp://claim/<key>` resource.
