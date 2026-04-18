# Recipe: Clippings

## Goal

Move new clipping sources into the normal `research-tech` interpretation and absorb pipeline.

## Command

```bash
ovp --full --pack research-tech --from-step clippings --batch-size 25
```

## Scope Note

This recipe intentionally skips `pinboard` and `pinboard_process`.

Use it only when you want to continue from local clipping sources already present in the vault.
For the normal daily incremental run that also pulls recent Pinboard bookmarks, use:

```bash
ovp --incremental --pack research-tech --batch-size 25
```

## Verify

Check that:

- `Clippings/` decreases
- `50-Inbox/01-Raw` and `50-Inbox/03-Processed/YYYY-MM` update
- new deep-dive notes land under `20-Areas/.../Topics/YYYY-MM`
