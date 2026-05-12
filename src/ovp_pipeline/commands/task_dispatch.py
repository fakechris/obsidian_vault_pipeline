"""``ovp-task`` — QUEUE → GENERATED task dispatcher (M20 / BL-076).

The vault gains a programmable surface: drop a markdown file into
``50-Inbox/02-Tasks/`` whose name starts with a known prefix, and a
handler runs an LLM (with BL-075 user + rules context) and writes
the result to ``40-Resources/Generated/YYYY-MM/``.  The original
task file moves to ``70-Archive/tasks/`` so the queue stays small.

Filename prefix → handler map
-----------------------------

* ``RESEARCH-<topic>.md``   → ``handle_research``     (deep-research brief)
* ``SYNTHESIZE-<topic>.md`` → ``handle_synthesize``   (multi-source synth)
* ``CONTRADICT-<topic>.md`` → ``handle_contradict``   (contradiction sweep)
* ``DIGEST-<slug>.md``      → ``handle_digest``       (BL-077, registered there)

CLI shapes
----------

* ``ovp-task --file <path>``         — synchronously run a single task
* ``ovp-task --process-pending``     — drain every task file currently in
                                        ``50-Inbox/02-Tasks/``
* ``ovp-task --list-handlers``       — print the registered prefixes

Rate limit
----------

A simple per-day counter at ``60-Logs/task-dispatch.jsonl`` caps total
dispatch count per UTC day.  Default 12.  Override with
``--rate-limit <n>`` or ``OVP_TASK_RATE_LIMIT`` env var.  Above the
cap, ``--process-pending`` skips remaining files with a
``task_rate_limited`` audit event.

Audit
-----

Every dispatch writes a ``task_dispatched`` event to
``60-Logs/pipeline.jsonl`` (and a copy to
``60-Logs/task-dispatch.jsonl`` for the rate-limit ledger).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Final

from ..context_loader import load_llm_context
from ..event_emitter import emit
from ..llm_client import get_litellm_client

logger = logging.getLogger(__name__)

TASKS_REL: Final[str] = "50-Inbox/02-Tasks"
GENERATED_REL: Final[str] = "40-Resources/Generated"
ARCHIVE_REL: Final[str] = "70-Archive/tasks"
RATE_LIMIT_LOG: Final[str] = "task-dispatch.jsonl"
DEFAULT_RATE_LIMIT: Final[int] = 12

# Prefix → (handler_name, description).  Handlers register themselves
# at module load via ``register_handler``.
HandlerFn = Callable[["TaskContext"], "TaskResult"]


class TaskContext:
    """Inputs every handler receives.  Bundled so handlers stay simple."""

    def __init__(
        self,
        *,
        vault_dir: Path,
        task_path: Path,
        prefix: str,
        slug: str,
        body: str,
        pack: str,
        llm_client: Any,
    ) -> None:
        self.vault_dir = vault_dir
        self.task_path = task_path
        self.prefix = prefix
        self.slug = slug
        self.body = body
        self.pack = pack
        self.llm_client = llm_client

    def llm_prefix(self) -> str:
        """USER.md + OVP_RULES.md as a system-prompt prefix.

        Retained for backwards-compatibility with handlers that
        format the prefix themselves; new handlers should prefer
        :meth:`compose_system_prompt` so the inject-prefix
        boilerplate doesn't repeat (rev-bot 206.2).
        """
        return load_llm_context(self.vault_dir)

    def compose_system_prompt(self, handler_prompt: str) -> str:
        """Return the handler's static prompt with USER + RULES
        prepended.  Returns the handler prompt unchanged when the
        vault has neither file."""
        from ..context_loader import inject_llm_context

        return inject_llm_context(self.vault_dir, handler_prompt)


class TaskResult:
    """What a handler produces.  Markdown body, optional metadata."""

    def __init__(
        self,
        *,
        body_md: str,
        subdir: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.body_md = body_md
        # Override the default ``YYYY-MM/`` placement; e.g. digest
        # handler writes to ``digests/`` instead.
        self.subdir = subdir
        self.metadata = metadata or {}


_HANDLERS: dict[str, tuple[HandlerFn, str]] = {}


def register_handler(prefix: str, fn: HandlerFn, description: str) -> None:
    """Register a handler for ``prefix-*.md`` filenames.  Idempotent:
    repeated registration with the same prefix replaces the previous
    binding (useful for tests)."""
    _HANDLERS[prefix.upper()] = (fn, description)


def known_prefixes() -> list[tuple[str, str]]:
    return sorted([(k, v[1]) for k, v in _HANDLERS.items()])


_FILENAME_RE = re.compile(r"^([A-Z][A-Z_]*)-(.+)\.md$")


def parse_task_filename(name: str) -> tuple[str, str] | None:
    """``RESEARCH-claude-code.md`` → ``("RESEARCH", "claude-code")``.

    Returns None for filenames that don't match the prefix-slug
    convention (those files are ignored by the dispatcher so the
    folder can also hold ``README.md`` or hand-written drafts).
    """
    m = _FILENAME_RE.match(name)
    if not m:
        return None
    return m.group(1).upper(), m.group(2)


def _today_utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _today_utc_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _count_today_dispatches(vault_dir: Path) -> int:
    log = vault_dir / "60-Logs" / RATE_LIMIT_LOG
    if not log.exists():
        return 0
    today = _today_utc_date()
    count = 0
    try:
        with log.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(row.get("ts", "")).startswith(today):
                    count += 1
    except OSError:
        return 0
    return count


def _record_dispatch(
    vault_dir: Path, task_path: Path, prefix: str, output_path: Path | None
) -> None:
    log = vault_dir / "60-Logs" / RATE_LIMIT_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prefix": prefix,
        "task": str(task_path.relative_to(vault_dir))
        if vault_dir in task_path.parents
        else str(task_path),
        "output": str(output_path.relative_to(vault_dir))
        if output_path and vault_dir in output_path.parents
        else (str(output_path) if output_path else ""),
    }
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _slugify_for_output(slug: str) -> str:
    """Sanitise a slug for filesystem use.  Lowercase, hyphenated,
    no traversal segments."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", slug).strip("-").lower()
    if not s or s.startswith("."):
        s = "task-" + s.lstrip(".") if s else "task"
    return s


def _resolve_output_path(
    vault_dir: Path, prefix: str, slug: str, subdir: str
) -> Path:
    if subdir:
        base = vault_dir / GENERATED_REL / subdir
    else:
        base = vault_dir / GENERATED_REL / _today_utc_month()
    base.mkdir(parents=True, exist_ok=True)
    fname = f"{_today_utc_date()}-{prefix.lower()}-{_slugify_for_output(slug)}.md"
    return base / fname


def _archive_task(vault_dir: Path, task_path: Path) -> Path:
    archive = vault_dir / ARCHIVE_REL / _today_utc_month()
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / task_path.name
    # Avoid clobbering same-named historical tasks.
    if target.exists():
        target = archive / f"{int(time.time())}-{task_path.name}"
    shutil.move(str(task_path), str(target))
    return target


class DispatchError(RuntimeError):
    pass


class RateLimitedError(DispatchError):
    pass


class NoHandlerError(DispatchError):
    pass


def dispatch_task(
    vault_dir: Path,
    task_path: Path,
    *,
    pack: str = "research-tech",
    llm_client: Any | None = None,
    rate_limit: int | None = None,
    archive: bool = True,
) -> Path:
    """Execute one task file.  Returns the output path.

    Raises ``NoHandlerError`` when the filename prefix has no
    handler, ``RateLimitedError`` when the daily cap is hit, and
    ``DispatchError`` for any other handler failure.
    """
    cap = rate_limit
    if cap is None:
        try:
            cap = int(os.environ.get("OVP_TASK_RATE_LIMIT", DEFAULT_RATE_LIMIT))
        except ValueError:
            cap = DEFAULT_RATE_LIMIT

    today_count = _count_today_dispatches(vault_dir)
    if today_count >= cap:
        emit(
            vault_dir, "pipeline.jsonl", "task_rate_limited",
            {
                "task": task_path.name,
                "count_today": today_count,
                "cap": cap,
            },
            pack=pack,
        )
        raise RateLimitedError(
            f"task rate limit reached for today: {today_count}/{cap}"
        )

    parsed = parse_task_filename(task_path.name)
    if parsed is None:
        raise NoHandlerError(
            f"filename does not match PREFIX-slug.md convention: {task_path.name}"
        )
    prefix, slug = parsed
    handler_entry = _HANDLERS.get(prefix)
    if handler_entry is None:
        raise NoHandlerError(
            f"no handler registered for prefix {prefix!r}"
            f" (known: {', '.join(sorted(_HANDLERS))})"
        )
    handler_fn = handler_entry[0]

    body = task_path.read_text(encoding="utf-8")

    if llm_client is None:
        llm_client = get_litellm_client(vault_dir=vault_dir)
        if llm_client is None:
            raise DispatchError(
                "no LLM client available — set OVP API key in the vault .env"
            )

    ctx = TaskContext(
        vault_dir=vault_dir,
        task_path=task_path,
        prefix=prefix,
        slug=slug,
        body=body,
        pack=pack,
        llm_client=llm_client,
    )

    start = time.monotonic()
    try:
        result = handler_fn(ctx)
    except Exception as exc:
        emit(
            vault_dir, "pipeline.jsonl", "task_failed",
            {
                "task": task_path.name,
                "prefix": prefix,
                "error": repr(exc),
            },
            pack=pack,
        )
        raise DispatchError(f"handler {prefix} failed: {exc}") from exc
    duration_ms = int((time.monotonic() - start) * 1000)

    output_path = _resolve_output_path(vault_dir, prefix, slug, result.subdir)
    output_path.write_text(result.body_md, encoding="utf-8")

    archived_path: Path | None = None
    if archive:
        archived_path = _archive_task(vault_dir, task_path)

    _record_dispatch(vault_dir, task_path, prefix, output_path)
    emit(
        vault_dir, "pipeline.jsonl", "task_dispatched",
        {
            "task": task_path.name,
            "prefix": prefix,
            "slug": slug,
            "output": str(output_path.relative_to(vault_dir)),
            "archived": str(archived_path.relative_to(vault_dir))
            if archived_path else "",
            "duration_ms": duration_ms,
            **result.metadata,
        },
        pack=pack,
    )
    return output_path


def list_pending_tasks(vault_dir: Path) -> list[Path]:
    """Sorted list of task files awaiting dispatch."""
    folder = vault_dir / TASKS_REL
    if not folder.exists():
        return []
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix == ".md" and parse_task_filename(p.name)
    )


# ── Built-in handlers ──────────────────────────────────────────────


_RESEARCH_SYSTEM_PROMPT = """\
You are OVP's deep-research handler.  Read the topic + the
operator's notes from the task body, and produce a structured
research brief in markdown.

Required sections (in this order):

1. **Core insight** — one clear sentence.  Must be non-obvious.
2. **What most people miss** — the angle the obvious framing skips.
3. **Supporting evidence** — 3 specific examples / studies / quotes.
4. **Counterargument** — the strongest case against the core insight.
5. **Three content angles** — ranked by leverage, with a one-line hook each.
6. **Open questions** — things this brief did not answer.

Quality bar: if the core insight is something a moderately
informed reader already believes, dig deeper before writing.
"""


def handle_research(ctx: TaskContext) -> TaskResult:
    user_prompt = (
        f"Topic: {ctx.slug.replace('-', ' ')}\n\n"
        "Operator notes (free-form, may be empty):\n"
        f"```\n{ctx.body.strip()}\n```\n"
    )
    sys_prompt = ctx.compose_system_prompt(_RESEARCH_SYSTEM_PROMPT)
    body_md = ctx.llm_client.call(sys_prompt, user_prompt, max_tokens=3500)
    body_md = (body_md or "").strip() + "\n"
    footer = (
        "\n---\n\n"
        f"**Generated by RESEARCH handler on {_today_utc_date()} "
        f"from `50-Inbox/02-Tasks/{ctx.task_path.name}`.**\n"
    )
    return TaskResult(body_md=body_md + footer)


_SYNTHESIZE_SYSTEM_PROMPT = """\
You are OVP's synthesis handler.  The operator has given you a
topic or question.  Pull from the user's vault context (provided in
the operator notes) to compose a synthesis — connect the relevant
ideas, surface tensions, and end with the most useful takeaway.

Required sections (in this order):

1. **One-line synthesis** — what these ideas, taken together, say.
2. **Threads** — 2–4 distinct strands that need to hold together.
3. **Where they tension** — the contradiction(s) between threads.
4. **The takeaway** — what to do or believe next, given all of it.

Avoid summarising each source separately.  The value is the
synthesis, not the inventory.
"""


def handle_synthesize(ctx: TaskContext) -> TaskResult:
    user_prompt = (
        f"Synthesis topic: {ctx.slug.replace('-', ' ')}\n\n"
        "Operator notes (vault excerpts, questions, scope):\n"
        f"```\n{ctx.body.strip()}\n```\n"
    )
    sys_prompt = ctx.compose_system_prompt(_SYNTHESIZE_SYSTEM_PROMPT)
    body_md = ctx.llm_client.call(sys_prompt, user_prompt, max_tokens=2500)
    body_md = (body_md or "").strip() + "\n"
    footer = (
        "\n---\n\n"
        f"**Generated by SYNTHESIZE handler on {_today_utc_date()} "
        f"from `50-Inbox/02-Tasks/{ctx.task_path.name}`.**\n"
    )
    return TaskResult(body_md=body_md + footer)


_CONTRADICT_SYSTEM_PROMPT = """\
You are OVP's contradiction-sweep handler.  The operator has named
a topic where they suspect tension.  Find the contradictions in
the supplied context and frame each as an open question — do not
resolve them.  Preserving the tension is the point.

Required structure:

For each contradiction (aim for 2–4):

### Open question: <short framing>

**Side A:** <claim + grounding>
**Side B:** <claim + grounding>
**Why it matters:** <stakes / what changes depending on the answer>
**What would resolve it:** <evidence or experiment that would settle it>

End with a short paragraph: which open question is most worth
sitting with first, and why.
"""


def handle_contradict(ctx: TaskContext) -> TaskResult:
    user_prompt = (
        f"Topic: {ctx.slug.replace('-', ' ')}\n\n"
        "Operator notes (claims, sources, suspected tensions):\n"
        f"```\n{ctx.body.strip()}\n```\n"
    )
    sys_prompt = ctx.compose_system_prompt(_CONTRADICT_SYSTEM_PROMPT)
    body_md = ctx.llm_client.call(sys_prompt, user_prompt, max_tokens=3000)
    body_md = (body_md or "").strip() + "\n"
    footer = (
        "\n---\n\n"
        f"**Generated by CONTRADICT handler on {_today_utc_date()} "
        f"from `50-Inbox/02-Tasks/{ctx.task_path.name}`.**\n"
    )
    return TaskResult(body_md=body_md + footer)


register_handler("RESEARCH",   handle_research,   "Deep-research brief.")
register_handler("SYNTHESIZE", handle_synthesize, "Multi-source synthesis.")
register_handler("CONTRADICT", handle_contradict, "Contradiction sweep.")


# ── CLI ────────────────────────────────────────────────────────────


def _print_handlers() -> None:
    print("Registered task prefixes:")
    for prefix, desc in known_prefixes():
        print(f"  {prefix:12}  {desc}")


def _register_extension_handlers() -> None:
    """Import sibling modules whose import side effect is to call
    ``register_handler``.  Kept lazy so ``task_dispatch`` itself
    stays cheap to import (e.g. for unit tests that don't need the
    digest handler's sqlite dependency)."""
    try:
        from . import digest_handler  # noqa: F401  side-effect: registers DIGEST
    except Exception:  # pragma: no cover - import failures are non-fatal
        logger.warning("digest_handler import failed; DIGEST prefix unavailable")


def main(argv: list[str] | None = None) -> int:
    _register_extension_handlers()
    parser = argparse.ArgumentParser(
        description="Run a vault task file from 50-Inbox/02-Tasks/.",
    )
    parser.add_argument("--vault-dir", required=True, type=Path)
    parser.add_argument(
        "--file", type=Path,
        help="Single task path to dispatch (synchronous).",
    )
    parser.add_argument(
        "--process-pending", action="store_true",
        help="Drain every pending task file under 50-Inbox/02-Tasks/.",
    )
    parser.add_argument(
        "--pack", default="research-tech",
        help="Pack name for audit events (default: research-tech).",
    )
    parser.add_argument(
        "--rate-limit", type=int,
        help="Override the per-UTC-day dispatch cap.",
    )
    parser.add_argument(
        "--list-handlers", action="store_true",
        help="Print registered prefix → handler map and exit.",
    )
    args = parser.parse_args(argv)

    if args.list_handlers:
        _print_handlers()
        return 0

    vault = args.vault_dir.expanduser().resolve()
    if not vault.exists():
        print(f"error: vault dir does not exist: {vault}", file=sys.stderr)
        return 2

    if args.file is None and not args.process_pending:
        parser.error("pass --file <path> or --process-pending")

    if args.file is not None:
        try:
            out = dispatch_task(
                vault, args.file.expanduser().resolve(),
                pack=args.pack,
                rate_limit=args.rate_limit,
            )
        except RateLimitedError as exc:
            print(f"rate-limited: {exc}", file=sys.stderr)
            return 3
        except (NoHandlerError, DispatchError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"ok     {out}")
        return 0

    # --process-pending
    pending = list_pending_tasks(vault)
    if not pending:
        print("no pending tasks")
        return 0

    ok = 0
    skipped = 0
    failed = 0
    for path in pending:
        try:
            out = dispatch_task(
                vault, path,
                pack=args.pack,
                rate_limit=args.rate_limit,
            )
            print(f"ok     {path.name} → {out.name}")
            ok += 1
        except RateLimitedError:
            print(f"skip   {path.name} (rate-limited; remaining files deferred)")
            skipped = len(pending) - ok - failed
            break
        except DispatchError as exc:
            print(f"fail   {path.name} ({exc})", file=sys.stderr)
            failed += 1
    print(f"summary: ok={ok} failed={failed} deferred={skipped}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
