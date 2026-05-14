"""Honest-zero messaging (M24.3, 2026-05-14).

Tiny shared module so every surface that can display a zero count
says the same true thing about what the zero means.  The doc that
defines this principle is ``docs/operational-lifecycle.md``
§Honest-zero.

A zero count on a Maintainer surface can mean three different
upstream things — three causes that collapse into one observation:

* **not run** — the producer didn't fire today.
* **no output** — the producer fired and emitted zero rows.
* **missing instrumentation** — the producer ran successfully but
  doesn't emit the audit row we're counting (M24.2 producer-audit
  gap).

Until M24.2 lands, no surface can distinguish the three.  This
module's job is to keep us honest about that ambiguity rather than
fabricate a single diagnosis.

After M24.2, callers will branch on the actual diagnosis the
``ops_state`` projection + producer-audit registry expose; the
``UNKNOWN_*`` phrasing here will narrow naturally.
"""

from __future__ import annotations

from typing import Final


HONEST_ZERO_SHORT: Final[str] = (
    "May mean: not run · no output · missing instrumentation."
)

HONEST_ZERO_LONG: Final[str] = (
    "No evidence in this window.  This can mean three different "
    "upstream things — the producer didn't run today, it ran and "
    "emitted nothing, or it ran successfully but the audit row "
    "we count is missing (M24.2 will close this gap).  Until then, "
    "do not infer a single cause from a zero count."
)


def honest_zero_html(*, short: bool = True, css_class: str = "muted tiny") -> str:
    """Return an HTML fragment surfacing the honest-zero message.

    Default is the short single-line form ``HONEST_ZERO_SHORT``,
    suitable for cards and inline footers.  ``short=False`` returns
    the long-form paragraph for empty-page banners.
    """
    text = HONEST_ZERO_SHORT if short else HONEST_ZERO_LONG
    css = css_class.strip() or "muted tiny"
    if short:
        return (
            f"<p class='{css}' style='margin-top:6px'>{text}</p>"
        )
    return (
        f"<div class='card' style='border-color:#9ca3af;"
        f"background:#f5f5f4;padding:0.75rem 1rem;margin:0.5rem 0'>"
        f"<p class='{css}' style='margin:0'>{text}</p></div>"
    )


def honest_zero_markdown() -> str:
    """Markdown-formatted honest-zero footer for digest bodies.

    The daily digest uses this when a Layer renders zero items so
    the operator gets the same explanation the UI cards already
    surface.
    """
    return f"\n\n_{HONEST_ZERO_SHORT}_\n"


__all__ = [
    "HONEST_ZERO_LONG",
    "HONEST_ZERO_SHORT",
    "honest_zero_html",
    "honest_zero_markdown",
]
