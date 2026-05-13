"""Reader ``/digests`` list view (M22 / BL-093).

Daily digest history.  The home page surfaces only the latest
digest; this page lists *all* digests under
``40-Resources/Generated/digests/`` in reverse-chronological
order so the operator can step through past days.

Each entry links to the same ``/note?path=…`` markdown render
the home card already opens; this module only adds the index
(date + teaser + open link) and the prev/next neighbour nav
that's missing once you're inside one of those notes.

Scope deliberately tight — no filtering, no search, no card
chrome.  A small ``<ul>`` of dated rows is enough for the
volume one operator generates (≤ 1 / day).
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import quote


DIGESTS_DIR = "40-Resources/Generated/digests"


@dataclass(frozen=True)
class DigestRow:
    """One digest entry rendered in the index.

    ``date`` is the ``YYYY-MM-DD`` prefix recovered from the file
    name; ``href`` is the same ``/note?path=…`` URL the home
    page uses; ``teaser`` is the first non-frontmatter
    paragraph (truncated).  ``filename`` is exposed so the
    template can render the underlying file id as a hover-only
    affordance for operators who like that level of detail.
    """

    date: str
    href: str
    teaser: str
    filename: str


def list_digests(vault_dir: Path | str) -> list[DigestRow]:
    """Return every digest file under ``DIGESTS_DIR``, newest first.

    Linear directory scan — there are at most a few hundred
    files for daily output across a year, so the cost is
    negligible.  Frontmatter is stripped from the teaser using
    the same logic as :func:`_build_latest_digest_info` so the
    two views agree.
    """
    folder = Path(vault_dir) / DIGESTS_DIR
    if not folder.is_dir():
        return []
    rows: list[DigestRow] = []
    for path in sorted(folder.glob("*.md"), reverse=True):
        date = path.name[:10]
        teaser = _extract_teaser(path)
        rel = str(path.relative_to(vault_dir))
        href = f"/note?path={quote(rel, safe='')}"
        rows.append(
            DigestRow(date=date, href=href, teaser=teaser, filename=path.name)
        )
    return rows


def _extract_teaser(path: Path) -> str:
    try:
        body = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    lines = body.splitlines()
    if lines and lines[0].strip() == "---":
        try:
            close_idx = next(
                i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"
            )
            lines = lines[close_idx + 1 :]
        except StopIteration:
            lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if len(stripped) > 220:
            return stripped[:217].rstrip() + "…"
        return stripped
    return ""


def render_digests_list_body(vault_dir: Path | str) -> str:
    """Render the body HTML for ``/digests``."""
    rows = list_digests(vault_dir)
    if not rows:
        return (
            "<h1>Daily digests</h1>"
            "<p class='muted'>No digests yet.  Generated digests land in "
            f"<code>{escape(DIGESTS_DIR)}</code> as the pipeline produces "
            "them — typically one per UTC day.</p>"
        )

    items: list[str] = []
    for row in rows:
        teaser_html = (
            f"<p class='muted small'>{escape(row.teaser)}</p>" if row.teaser else ""
        )
        items.append(
            "<li class='card' style='margin-bottom:0.75rem'>"
            f"<a href='{escape(row.href)}'>"
            f"<strong>{escape(row.date)}</strong>"
            "</a>"
            f"<span class='muted small mono' style='margin-left:0.6rem'>"
            f"{escape(row.filename)}"
            "</span>"
            f"{teaser_html}"
            "</li>"
        )
    list_html = "<ul style='list-style:none;padding:0'>" + "".join(items) + "</ul>"
    return (
        "<h1>Daily digests</h1>"
        f"<p class='muted'>{len(rows)} digest"
        f"{'s' if len(rows) != 1 else ''} — newest first.</p>"
        + list_html
    )


def neighbour_links(vault_dir: Path | str, current_relpath: str) -> tuple[str, str]:
    """Return ``(prev_href, next_href)`` for the digest at
    ``current_relpath`` (vault-relative).

    Used by the /note route to inject "← previous day · next
    day →" pivots when the operator opens a file under
    ``DIGESTS_DIR``.  Either value is empty when the current
    file has no neighbour on that side.
    """
    rows = list_digests(vault_dir)
    if not rows:
        return "", ""
    # rows are newest-first; "prev" in operator-speak means
    # *older*, i.e. further down the list.
    idx = next(
        (i for i, r in enumerate(rows) if r.href.endswith(quote(current_relpath, safe=""))),
        -1,
    )
    if idx < 0:
        return "", ""
    older = rows[idx + 1].href if idx + 1 < len(rows) else ""
    newer = rows[idx - 1].href if idx > 0 else ""
    return older, newer
