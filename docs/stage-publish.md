# Publish to the public web (durable knowledge + visualizations)

`ovp2 publish` turns the vault's durable knowledge into a blog-like static site:
the crystal **durable** claims, themes, the interactive graph + 3D knowledge
terrain, and lightweight per-source pages that link OUT to the original. It runs
the same `console-ui` SPA — built in static mode — over a snapshot of the
read-only `/api/*` surface, so the whole portal works on any static host with no
server.

## Why sync is a non-problem here

The vault is "projections over ledgers": every run rebuilds `index.json` (and
`themes.json`, `terrain.json`) deterministically from append-only JSONL, in
milliseconds. **The published site is just another projection.** You never
diff-sync files against a mutable remote — you rebuild the whole `site/` from the
ledgers and let git compute the diff. Deterministic builders → byte-identical
output for unchanged content → minimal diffs. `.ovp/publish.jsonl` records each
run's content hash so a no-op publish skips the push.

## What ships (and what doesn't)

Enforced at one choke-point, `PublicView` (`ovp-api-projection::redact`):

- **Ships:** `Processed` sources (title, outbound URL, date, theme), **durable**
  claims with their short citation quotes, themes, terrain/graph, per-source
  *lite* pages (no full `reader.md` body — avoids republishing third-party
  article text).
- **Never ships:** caveated/superseded/retracted claims, `review.json`, blocked/
  failed/needs-content sources, `rel_path`/failure reasons/run internals, the
  vault path, ask/LLM config, the crystal ledger, run reports.

## Architecture

```
ledgers (JSONL) → build_index → IndexModel (+ durable records + terrain/themes)
  → PublicView (redact) → shared ovp-api-projection builders
  → site/api/*.json + static SPA bundle → git push → GitHub Pages
```

`ovp-api-projection` is the single source of truth for the `/api/*` bodies —
both the live `ovp-server` and `ovp-publish` call it, so the published site can
never drift from the app. `ovp-publish` snapshots the tree; `ovp2 publish`
orchestrates the SPA copy, ledger, and deploy.

## Usage

```bash
# 1. Build the SPA once for static hosting (VITE_OVP_BASE = the Pages sub-path)
cd console-ui
VITE_OVP_STATIC=1 VITE_OVP_BASE=/ npx vite build --outDir dist-static

# 2. Snapshot + assemble the site (rebuilds the index first unless --no-rebuild)
ovp2 publish --vault-root ~/Documents/ovp-vault \
  --out /tmp/ovp-site --spa-dir console-ui/dist-static

# 3. Preview locally
cd /tmp/ovp-site && python3 -m http.server 8899   # → http://127.0.0.1:8899/

# 4. Deploy to a SEPARATE public repo (the vault repo is private)
ovp2 publish --vault-root ~/Documents/ovp-vault \
  --out /tmp/ovp-site --spa-dir console-ui/dist-static \
  --repo git@github.com:<you>/<vault>-site.git --branch gh-pages
```

Flags: `--base-url` (default `/`), `--no-rebuild`, `--force` (publish even when
content is unchanged), `--repo`/`--branch` (git deploy). Serving under a
sub-path (`https://<you>.github.io/<repo>/`) → build with
`VITE_OVP_BASE=/<repo>/` and pass `--base-url /<repo>/`.

The static build uses **HashRouter** (`/#/knowledge`) so deep links work on any
static host with zero rewrite rules, and is **knowledge-only**: home =
Knowledge, nav = Knowledge / Library / Search (Today / Ask / System and the run
banner are live-ops surfaces, hidden on the published site).

## Weekly schedule (optional)

There is no generic add-job command (the seeded registry is daily + crystallize),
so add a publish job to `.ovp/schedule.json` by hand — it needs your `--out` and
`--repo`:

```json
{
  "id": "publish",
  "cadence": { "weekly": { "weekday": "Sun", "hour": 11, "minute": 0 } },
  "argv": [
    "publish", "--vault-root", "{vault}",
    "--out", "{vault}/.ovp/site",
    "--spa-dir", "{vault}/.ovp/site-spa",
    "--repo", "git@github.com:<you>/<vault>-site.git"
  ],
  "enabled": true,
  "stamp_date": false,
  "description": "Publish durable knowledge to the public site"
}
```

The scheduler sources `.ovp/daily.env` before each job, so a deploy token lives
there (not in the registry). The content-hash skip makes a scheduled no-op cheap.
Rebuild `--spa-dir` when `console-ui` changes.
