# Recipe: Paper / PDF

## Goal

Run paper/PDF sources through the `research-tech` pack and preserve evidence into the truth layer.

## Command

```bash
ovp --full --pack research-tech
```

## Verify

- paper processor resolves and reads the PDF
- deep-dive note is produced
- `ovp-build-views --pack research-tech --view event/dossier` still compiles after indexing
