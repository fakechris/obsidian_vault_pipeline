"""``DIGEST-*`` handler — M20 / BL-077 daily synthesis.

Implements the "vault talks back" feature.  Once per day (driven by
``ovp-digest --enqueue-daily``, typically called by cron / launchd /
AutoPilot), a ``DIGEST-daily.md`` task lands in
``50-Inbox/02-Tasks/`` and the BL-076 dispatcher routes it here.

Output: ``40-Resources/Generated/digests/YYYY-MM-DD.md`` — a
~200-word brief in three sections:

1. **Tensions worth sitting with** — 2-3 contradictions from
   top-scoring ``crystal_scores`` rows where
   ``crystal_kind = 'contradiction'``.
2. **Themes you keep circling** — 2-3 community crystals
   synthesized in the last 24h.
3. **Unanswered questions** — 2-3 open
   ``contradiction_crystals`` (``superseded_by_synthesized_at = ''``)
   that have NOT yet been covered by a tension above.

Inputs are aggregated by ``_collect_digest_inputs``, formatted into
a user prompt, and the LLM is asked to compose a brief in the user's
voice (USER.md context is automatically prefixed by BL-076).

CLI shapes
----------

* ``ovp-digest --enqueue-daily``  — drop ``DIGEST-daily.md`` into
                                     ``50-Inbox/02-Tasks/`` so the
                                     next dispatcher run picks it up
* ``ovp-digest --run-now``         — enqueue + dispatch synchronously
* ``ovp-digest --show-latest``     — print the path of the most
                                     recent digest file
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..context_loader import load_user_profile
from .task_dispatch import (
    TaskContext,
    TaskResult,
    dispatch_task,
    register_handler,
)

logger = logging.getLogger(__name__)

DIGESTS_SUBDIR = "digests"
TOP_TENSIONS_N = 3
RECENT_THEMES_N = 3
OPEN_QUESTIONS_N = 3
RECENT_CRYSTALS_WINDOW_H = 24


def _knowledge_db_path(vault_dir: Path) -> Path:
    return vault_dir / "60-Logs" / "knowledge.db"


def _collect_digest_inputs(vault_dir: Path, pack: str) -> dict[str, Any]:
    """Pull tensions / themes / open-questions from ``knowledge.db``.

    Empty vault → returns all three lists empty.  The handler then
    composes a sparse digest noting the empty state instead of
    failing.
    """
    db_path = _knowledge_db_path(vault_dir)
    if not db_path.exists():
        return {"tensions": [], "themes": [], "open_questions": []}

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=RECENT_CRYSTALS_WINDOW_H)).isoformat(
        timespec="seconds",
    )

    with sqlite3.connect(db_path) as conn:
        # Top-scoring tensions (contradictions).
        tensions = conn.execute(
            """
            SELECT cs.crystal_id, cs.score, cc.subject_key, cc.body_md
              FROM crystal_scores cs
              JOIN contradiction_crystals cc
                ON cc.pack = cs.pack
               AND cc.contradiction_id = cs.crystal_id
               AND cc.superseded_by_synthesized_at = ''
             WHERE cs.pack = ?
               AND cs.crystal_kind = 'contradiction'
             ORDER BY cs.score DESC
             LIMIT ?
            """,
            (pack, TOP_TENSIONS_N),
        ).fetchall()

        # Recently synthesized community crystals.
        themes = conn.execute(
            """
            SELECT cc.cluster_id, cc.synthesized_at, gc.label, cc.body_md
              FROM community_crystals cc
              JOIN graph_clusters gc
                ON gc.pack = cc.pack AND gc.cluster_id = cc.cluster_id
             WHERE cc.pack = ?
               AND cc.superseded_by_synthesized_at = ''
               AND cc.synthesized_at >= ?
             ORDER BY cc.synthesized_at DESC
             LIMIT ?
            """,
            (pack, cutoff, RECENT_THEMES_N),
        ).fetchall()

        # Open contradictions (excluding the ones already in tensions
        # above so the digest doesn't double-count).
        tension_ids = {row[0] for row in tensions}
        open_qs_all = conn.execute(
            """
            SELECT contradiction_id, subject_key, body_md, synthesized_at
              FROM contradiction_crystals
             WHERE pack = ?
               AND superseded_by_synthesized_at = ''
             ORDER BY synthesized_at DESC
             LIMIT ?
            """,
            (pack, OPEN_QUESTIONS_N * 3),  # over-fetch and filter
        ).fetchall()
        open_questions = [
            row for row in open_qs_all
            if row[0] not in tension_ids
        ][:OPEN_QUESTIONS_N]

    def _teaser(text: str, max_chars: int = 220) -> str:
        cleaned = " ".join((text or "").split())
        return cleaned[:max_chars]

    return {
        "tensions": [
            {
                "id": row[0],
                "score": row[1],
                "subject": row[2],
                "teaser": _teaser(row[3]),
            }
            for row in tensions
        ],
        "themes": [
            {
                "cluster_id": row[0],
                "synthesized_at": row[1],
                "label": row[2],
                "teaser": _teaser(row[3]),
            }
            for row in themes
        ],
        "open_questions": [
            {
                "id": row[0],
                "subject": row[1],
                "teaser": _teaser(row[2]),
            }
            for row in open_questions
        ],
    }


_DIGEST_SYSTEM_PROMPT = """\
You are OVP's daily-digest handler.  You receive three aggregated
inputs from the operator's vault and produce a ~200-word morning
brief in the user's voice.

Output structure (use these literal headings):

## Tensions worth sitting with
Two or three contradictions, each 1-2 sentences.  Frame as
questions the operator hasn't yet answered.

## Themes you keep circling
Two or three recently-synthesized topics that recurred in the last
24h.  One sentence each.  Surface the through-line — what makes
them feel related.

## Unanswered questions
Two or three open contradictions the operator hasn't resolved.  One
sentence each.

Hard rules:
- Stay under 220 words total.
- Don't summarise everything — pick the items that pull on each
  other.  A digest is a curated rail, not a feed.
- Don't invent topics that aren't in the inputs.  If an input list
  is empty, omit that section.
- Don't include a closing call to action — leave the operator with
  the questions, not a to-do.
"""


def _build_digest_user_prompt(
    inputs: dict[str, Any], user_focus: str
) -> str:
    parts: list[str] = []
    if user_focus.strip():
        parts.append("Operator's current focus (from USER.md):\n")
        parts.append(user_focus.strip() + "\n")
    parts.append("\n# Recent inputs from the vault\n")

    if inputs["tensions"]:
        parts.append("\n## Top-scoring contradictions")
        for t in inputs["tensions"]:
            parts.append(
                f"- **{t['subject']}** (score {t['score']:.2f})\n  "
                f"{t['teaser']}"
            )
    else:
        parts.append("\n## Top-scoring contradictions\n(none)\n")

    if inputs["themes"]:
        parts.append("\n## Communities synthesized in the last 24h")
        for t in inputs["themes"]:
            parts.append(f"- **{t['label']}**\n  {t['teaser']}")
    else:
        parts.append("\n## Communities synthesized in the last 24h\n(none)\n")

    if inputs["open_questions"]:
        parts.append("\n## Other open contradictions")
        for q in inputs["open_questions"]:
            parts.append(f"- **{q['subject']}**\n  {q['teaser']}")
    else:
        parts.append("\n## Other open contradictions\n(none)\n")

    parts.append("\nNow compose the brief.")
    return "\n".join(parts)


def _today_utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def handle_digest(ctx: TaskContext) -> TaskResult:
    """Aggregate vault signals + compose the daily digest."""
    inputs = _collect_digest_inputs(ctx.vault_dir, ctx.pack)
    user_focus = load_user_profile(ctx.vault_dir)
    user_prompt = _build_digest_user_prompt(inputs, user_focus)
    prefix = ctx.llm_prefix()
    sys_prompt = (
        prefix + "\n" + _DIGEST_SYSTEM_PROMPT
        if prefix else _DIGEST_SYSTEM_PROMPT
    )

    # Empty vault: skip the LLM call entirely, write a stub digest
    # that explains there's nothing to surface.  Saves both the
    # token spend and a guaranteed-bland generic response.
    if not any((inputs["tensions"], inputs["themes"], inputs["open_questions"])):
        body_md = (
            f"# Digest — {_today_utc_date()}\n\n"
            "Nothing new to surface today.  No contradictions, "
            "themes, or open questions in this pack.\n\n"
            "Either the vault is still warming up (run "
            "`ovp-synthesize-community-crystals` and `ovp-knowledge-index`), "
            "or you have read everything already — in which case, "
            "perhaps capture something new.\n"
        )
    else:
        composed = ctx.llm_client.call(
            sys_prompt, user_prompt, max_tokens=900,
        )
        composed = (composed or "").strip()
        body_md = (
            f"# Digest — {_today_utc_date()}\n\n{composed}\n"
        )

    footer = (
        "\n---\n\n"
        f"**Generated by DIGEST handler on {_today_utc_date()} "
        f"from `50-Inbox/02-Tasks/{ctx.task_path.name}`. "
        f"Tensions: {len(inputs['tensions'])}, "
        f"Themes: {len(inputs['themes'])}, "
        f"Open questions: {len(inputs['open_questions'])}.**\n"
    )
    return TaskResult(
        body_md=body_md + footer,
        subdir=DIGESTS_SUBDIR,
        metadata={
            "tensions": len(inputs["tensions"]),
            "themes": len(inputs["themes"]),
            "open_questions": len(inputs["open_questions"]),
        },
    )


register_handler("DIGEST", handle_digest, "Daily synthesis brief.")


# ── ovp-digest CLI ─────────────────────────────────────────────────


def _enqueue_daily(vault_dir: Path) -> Path:
    """Drop ``DIGEST-daily.md`` into ``50-Inbox/02-Tasks/`` so the
    next dispatcher run picks it up.  Idempotent — if today's task
    file already exists, return its path without overwriting."""
    folder = vault_dir / "50-Inbox" / "02-Tasks"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "DIGEST-daily.md"
    if target.exists():
        return target
    target.write_text(
        "<!-- Auto-generated by `ovp-digest --enqueue-daily`. "
        "The handler ignores this body — vault state is pulled "
        "directly from knowledge.db. -->\n",
        encoding="utf-8",
    )
    return target


def _latest_digest(vault_dir: Path) -> Path | None:
    folder = vault_dir / "40-Resources" / "Generated" / DIGESTS_SUBDIR
    if not folder.exists():
        return None
    candidates = sorted(folder.glob("*.md"))
    return candidates[-1] if candidates else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Daily digest helpers (M20 / BL-077).",
    )
    parser.add_argument("--vault-dir", required=True, type=Path)
    parser.add_argument(
        "--enqueue-daily", action="store_true",
        help="Create DIGEST-daily.md in 50-Inbox/02-Tasks/.",
    )
    parser.add_argument(
        "--run-now", action="store_true",
        help="Enqueue then dispatch synchronously.",
    )
    parser.add_argument(
        "--show-latest", action="store_true",
        help="Print the path of the most recent digest.",
    )
    parser.add_argument(
        "--pack", default="research-tech",
        help="Pack name (default: research-tech).",
    )
    args = parser.parse_args(argv)

    if not any((args.enqueue_daily, args.run_now, args.show_latest)):
        parser.error("pass --enqueue-daily, --run-now, or --show-latest")

    vault = args.vault_dir.expanduser().resolve()
    if not vault.exists():
        print(f"error: vault dir does not exist: {vault}", file=sys.stderr)
        return 2

    if args.show_latest:
        latest = _latest_digest(vault)
        if latest is None:
            print("(no digests yet)")
            return 1
        print(latest)
        return 0

    if args.enqueue_daily and not args.run_now:
        path = _enqueue_daily(vault)
        print(f"enqueued: {path}")
        return 0

    # --run-now (alone, or with --enqueue-daily; same effect)
    task_path = _enqueue_daily(vault)
    try:
        out = dispatch_task(vault, task_path, pack=args.pack)
    except Exception as exc:
        print(f"error: dispatch failed: {exc}", file=sys.stderr)
        return 1
    print(f"ok: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
