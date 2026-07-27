# Portal screenshots (README gallery)

Captured from a local dogfood vault via `ovp2 serve` for the product-facing
README. Public technical clippings only — re-audit before publishing if the
vault has changed.

| File | Page |
|---|---|
| `01-today.png` | Today dashboard |
| `02-library.png` | Library list |
| `03-source-detail.png` | Source memory cards |
| `04-knowledge.png` | Knowledge themes (list) |
| `05-knowledge-graph.png` | Knowledge graph |
| `06-ask.png` | Ask empty / examples |
| `07-ask-history.png` | Ask history + process graph |
| `08-search.png` | Search results |

### Capture notes

Prefer **light theme** (`data-theme=light`) and **EN** UI for README consistency.

Do **not** ship raw infinite-scroll full pages (Library can be 70k+ CSS px).
Use either:

1. `fullPage: true` with a CSS max-height on `body` (and hide list rows after ~8), or  
2. post-crop the top band to ~1600–2000 CSS px (≈3200–4000 device px at 2×).

Portal must be running (e.g. `ovp2 serve … --port 7777`). Re-audit for secrets
before publishing if the dogfood vault has changed.
