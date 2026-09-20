# Frontmatter fences: five implementations, five behaviours

`split_frontmatter` is implemented **five** separate times in this workspace.
They disagree, and the one that disagrees most is the one that matters most.

| # | Implementation | BOM | CRLF open fence | Unterminated | Body fidelity |
|---|---|---|---|---|---|
| 1 | `ovp-domain/src/sources/markdown_inbox.rs` | stripped | **not recognised** | → body | exact slice |
| 2 | `ovp-enrich/src/web_fetch.rs` | not handled | recognised | → body | exact slice |
| 3 | `ovp-enrich/src/github.rs` | not handled | recognised | → body | leaves a stray `\r` |
| 4 | `ovp-review/src/compare.rs` | not handled | recognised | → body | rejoined with `\n` (lossy) |
| 5 | `ovp-eval/src/normalize.rs` | not handled | recognised | → body | rejoined with `\n` (lossy) |

Implementations 4 and 5 are byte-identical to each other.

## Why #1 is the expensive one

Implementation 1 is what `parse_clipping` uses, so it is the one that turns a
captured file into a `SourceDoc` — the thing the grounded reader quotes as
evidence. It is also the only one that does **not** accept a CRLF fence,
because it matches `---\n` with `strip_prefix` rather than tolerating `\r`.

The consequence, pinned by tests in `inv627_fence_shape_tests`:

- A CRLF note parses with **no** frontmatter at all. `title` falls back to
  `Untitled`, `source` to empty, `tags` to none.
- The whole YAML block, including `annotation:`, becomes `body_markdown`.

That last point undoes the contract INV-619 established. `annotation:` holds
the *reader's own words*; the entire point is that they are never source
evidence. For a CRLF note they are, and they can be quoted back as if the
author had written them.

CRLF is not a hypothetical: any note edited on Windows, or round-tripped
through a sync tool, can carry it.

## Unterminated fences

All five agree: an opening `---` with no closing fence means "no frontmatter",
so the YAML is read as prose. That consistency is worth keeping in mind — it
is a real decision, not an accident, and changing it to `Unparseable` (which
would make the operator see and fix it) is a product call, not a bug fix.

## Before changing any of this

Making #1 CRLF-tolerant **changes how existing vault files parse**. A note that
today yields "no frontmatter, everything is body" would start yielding a real
title, source, tags and annotation. That is the desired end state, but it is a
migration, not a patch: the affected notes' derived state (index rows, reader
packs, anything that quoted the leaked text) was built from the old reading.

Measure first, on a copy of a real vault: how many notes have a CRLF or
unterminated fence, and what do they currently parse to.
