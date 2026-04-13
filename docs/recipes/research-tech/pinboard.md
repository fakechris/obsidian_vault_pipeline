# Recipe: Pinboard

## Goal

Run the `research-tech` pack from fresh Pinboard source items through the full workflow.

## Command

```bash
ovp --full --pack research-tech --batch-size 25
```

## Verify

```bash
ovp-doctor --pack research-tech --vault-dir /path/to/vault --json
tail -f /path/to/vault/60-Logs/pipeline.jsonl
```
