# Recipe: Web Article

## Goal

Run a normal web article source through the `research-tech` workflow without fabricating missing content.

## Command

```bash
ovp --full --pack research-tech
```

## Verify

- thin/metadata-only sources abstain instead of hallucinating
- successful article sources produce deep-dive notes
- `ovp-query --pack research-tech "..."` can retrieve the new material after indexing
