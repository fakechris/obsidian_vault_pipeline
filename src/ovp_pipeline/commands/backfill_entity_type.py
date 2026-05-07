"""ovp-backfill-entity-type — Batch-classify entity_type for Evergreen notes.

Two-phase backfill (BL-030, building on BL-025/026):

  **Phase 1 — deterministic fast-path** (zero LLM cost):
  Evergreens written by v2 absorb already carry ``unit_type:`` in
  their frontmatter (one of fact / method / procedure / tradeoff /
  ...). The pre-BL-025 collapse landed them as ``entity_type:
  concept`` regardless.  Phase 1 just rewrites
  ``entity_type = unit_type`` on those.

  **Phase 2 — LLM classification** (paid):
  Evergreens without ``unit_type`` (v1 deep-dive output, manual
  notes) need an LLM to choose from the unified taxonomy
  (``CORE_OBJECT_KINDS | V2_UNIT_TYPES``).

The classifier picks from 19 distinct kinds — the 10 entity-side
kinds plus the 9 v2-only unit kinds (KIND_METHOD overlaps).

Emits structured audit events to ``60-Logs/pipeline.jsonl``:

  - ``entity_type_backfill_v2_passthrough`` (Phase 1)
  - ``entity_type_backfill`` (Phase 2 success)
  - ``entity_type_backfill_error`` (Phase 2 LLM failure)
  - ``entity_type_backfill_summary`` (final stats)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from ..object_kinds import (
    CORE_OBJECT_KINDS,
    KIND_CONCEPT,
    V2_UNIT_TYPES,
    normalize_kind,
)

_FRONTMATTER_RE = re.compile(r"\A\s*\ufeff?---\r?\n(.*?)\r?\n---", re.DOTALL)
_ENTITY_TYPE_LINE_RE = re.compile(r"^entity_type:\s*.*$", re.MULTILINE)

# Unified taxonomy for backfill: entity-side kinds + v2 unit kinds.
# KIND_METHOD lives in both, so the union has 19 distinct values.
VALID_KINDS = CORE_OBJECT_KINDS | V2_UNIT_TYPES

SYSTEM_PROMPT = """You are a knowledge-unit classifier. Pick exactly ONE of these 19 kinds for the note. Reply with just the kind string, nothing else.

Entity-side kinds (the note names a real-world thing):
- person: A specific individual (e.g. "Andrej Karpathy")
- company: An organization (e.g. "OpenAI", "Anthropic")
- tool: A software tool / library / product (e.g. "LangChain", "Claude Code")
- project: A specific project or repo (e.g. "Apollo Program", "AutoGPT")
- paper: A research publication (e.g. "Attention Is All You Need")
- event: A dated event / conference (e.g. "NeurIPS 2024", "GPT-4 launch")
- framework: A named methodology / mental model (e.g. "PARA", "ReAct")
- method: A named technique / algorithm (e.g. "chain-of-thought", "RLHF")
- entity: Catch-all named entity not fitting the above
- concept: An abstract idea / principle / pattern

Knowledge-unit kinds (the note states a knowledge claim):
- fact: A single objective fact + at least one specific anchor (number, name, date)
- procedure: Numbered steps with concrete actions / commands
- tradeoff: Choice between alternatives + cost + applicability
- failure_mode: How a system breaks; what conditions trigger it
- counterexample: Concrete instance that contradicts a generally-held claim
- case_detail: Specific case (who / where / what / outcome)
- learning: Insight + the source's evidence for it
- decision: A made decision + alternatives + rationale
- quote: Verbatim quote worth preserving + brief annotation

Pick the most-specific applicable kind. Reply with the kind word only — no explanation, no quotes, no punctuation."""


def _parse_frontmatter(text: str) -> tuple[dict[str, str], int, int]:
    """Return (frontmatter_dict, body_start, body_end) from raw markdown."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, 0, len(text)
    fm_block = m.group(1)
    kvs: dict[str, str] = {}
    for line in fm_block.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            kvs[key.strip()] = val.strip().strip('"').strip("'")
    return kvs, m.end(), len(text)


def _inject_entity_type(text: str, entity_type: str) -> str:
    """Insert or replace ``entity_type: <value>`` in frontmatter."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return text
    fm_block = m.group(1)
    if _ENTITY_TYPE_LINE_RE.search(fm_block):
        new_fm = _ENTITY_TYPE_LINE_RE.sub(f"entity_type: {entity_type}", fm_block)
    else:
        lines = fm_block.splitlines()
        insert_idx = len(lines)
        for i, line in enumerate(lines):
            if line.startswith("type:"):
                insert_idx = i + 1
                break
        lines.insert(insert_idx, f"entity_type: {entity_type}")
        new_fm = "\n".join(lines)
    return text[: m.start(1)] + new_fm + text[m.end(1) :]


def _build_llm_client() -> Any:
    from ..auto_evergreen_extractor import LiteLLMClient

    return LiteLLMClient(temperature=0.1, api_key=None, api_base=None)


def _classify(llm: Any, title: str, definition: str, excerpt: str) -> str:
    user_prompt = f"Title: {title}\nDefinition: {definition}\nExcerpt: {excerpt[:500]}"
    raw = llm.generate(SYSTEM_PROMPT, user_prompt, max_tokens=20).strip().lower()
    raw = raw.strip('"').strip("'").strip(".")
    normalized = normalize_kind(raw)
    if normalized in VALID_KINDS:
        return normalized
    return KIND_CONCEPT


def _emit_audit(logger: Any, event: dict[str, Any]) -> None:
    """Emit one audit event via a shared logger.

    Pre-fix this re-instantiated ``PipelineLogger`` on every call,
    which gave each event a fresh ``session_id`` — defeating
    BL-053's by-run grouping (``/ops/runs/<txn_id>``).  The caller
    now constructs a single logger up-front and reuses it for the
    whole backfill so the per-run drilldown sees the entire run's
    events together.
    """
    event_type = event.pop("event_type", "backfill")
    event.pop("timestamp", None)
    logger.log(event_type, event)


def run(
    vault_dir: Path,
    *,
    dry_run: bool = False,
    limit: int = 0,
    batch_size: int = 50,
) -> dict[str, Any]:
    evergreen_dir = vault_dir / "10-Knowledge" / "Evergreen"
    if not evergreen_dir.is_dir():
        print(f"Evergreen directory not found: {evergreen_dir}")
        return {"error": "directory_not_found"}

    log_path = vault_dir / "60-Logs" / "pipeline.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Single PipelineLogger instance for the whole run so every
    # audit event shares the same ``session_id`` — that's what
    # ``/ops/runs/<txn_id>`` keys off to group events.  Pre-fix
    # ``_emit_audit`` re-instantiated PipelineLogger per call,
    # giving each event a fresh session.
    from ..auto_moc_updater import PipelineLogger
    logger = PipelineLogger(log_path)

    md_files = sorted(evergreen_dir.glob("*.md"))
    total = len(md_files)
    print(f"Found {total} Evergreen notes in {evergreen_dir}")

    # Phase 1 buckets — deterministic fast-path candidates and
    # everything else.  ``already_correct`` skips files where
    # entity_type already agrees with unit_type (or there's no
    # unit_type to override).  ``phase1`` covers v2 evergreens
    # whose entity_type was set by the pre-BL-025 collapse but
    # whose unit_type carries the real richer kind.  ``phase2``
    # covers v1 evergreens (no unit_type) — they need LLM.
    # Each tuple carries ``body_start`` so Phase 2 doesn't have
    # to re-match the frontmatter regex.
    already_correct = 0
    phase1: list[tuple[Path, str, dict[str, str], int]] = []
    phase2: list[tuple[Path, str, dict[str, str], int]] = []
    for fp in md_files:
        text = fp.read_text(encoding="utf-8", errors="replace")
        fm, body_start, _ = _parse_frontmatter(text)
        existing_type = fm.get("entity_type", "").strip()
        unit_type = fm.get("unit_type", "").strip()

        if unit_type in V2_UNIT_TYPES:
            # v2 evergreen — fast-path eligible.  Skip if
            # entity_type already matches unit_type; otherwise
            # rewrite to unit_type with no LLM call.
            if existing_type == unit_type:
                already_correct += 1
            else:
                phase1.append((fp, text, fm, body_start))
            continue

        # No (recognised) unit_type — needs LLM if entity_type
        # missing / invalid.
        if existing_type and existing_type in VALID_KINDS:
            already_correct += 1
            continue
        phase2.append((fp, text, fm, body_start))

    print(
        f"Already correct: {already_correct}, "
        f"Phase 1 (deterministic): {len(phase1)}, "
        f"Phase 2 (LLM): {len(phase2)}"
    )

    if limit > 0:
        # Apply limit globally — Phase 1 first, then Phase 2.
        phase1_take = min(len(phase1), limit)
        phase2_take = min(len(phase2), max(0, limit - phase1_take))
        phase1 = phase1[:phase1_take]
        phase2 = phase2[:phase2_take]
        print(f"Limited to {phase1_take} Phase 1 + {phase2_take} Phase 2")

    if dry_run:
        print("[dry-run] Phase 1 sample (first 10):")
        for fp, _, fm, _ in phase1[:10]:
            ut = fm.get("unit_type", "?")
            print(f"  {fp.name}  unit_type={ut}  (would set entity_type={ut})")
        if len(phase1) > 10:
            print(f"  ... and {len(phase1) - 10} more Phase 1 files")
        print("[dry-run] Phase 2 sample (first 10):")
        for fp, _, fm, _ in phase2[:10]:
            print(f"  {fp.name}  title={fm.get('title', '?')}")
        if len(phase2) > 10:
            print(f"  ... and {len(phase2) - 10} more Phase 2 files")
        return {
            "dry_run": True,
            "already_correct": already_correct,
            "phase1": len(phase1),
            "phase2": len(phase2),
        }

    classified = 0
    errors = 0
    stats: dict[str, int] = {}
    t0 = time.time()

    # Phase 1: deterministic rewrite (no LLM).  Pass through
    # unit_type → entity_type for each file.
    print(f"\n=== Phase 1: deterministic ({len(phase1)} files) ===")
    for i, (fp, text, fm, _body_start) in enumerate(phase1):
        unit_type = fm["unit_type"].strip()
        new_text = _inject_entity_type(text, unit_type)
        fp.write_text(new_text, encoding="utf-8")
        classified += 1
        stats[unit_type] = stats.get(unit_type, 0) + 1
        _emit_audit(
            logger,
            {
                "event_type": "entity_type_backfill_v2_passthrough",
                "file": str(fp.relative_to(vault_dir)),
                "entity_type": unit_type,
                "previous": fm.get("entity_type", ""),
            },
        )
        if (i + 1) % batch_size == 0:
            print(f"  [{i+1}/{len(phase1)}] passthrough  last={fp.name} -> {unit_type}")

    # Phase 2: LLM classification.  Skip entirely if Phase 2 list
    # is empty (avoids paying for LLM init / API key check).
    if not phase2:
        print("\n=== Phase 2: skipped (no v1 evergreens needing LLM) ===")
        llm = None
    else:
        print(f"\n=== Phase 2: LLM classification ({len(phase2)} files) ===")
        llm = _build_llm_client()

    for i, (fp, text, fm, body_start) in enumerate(phase2):
        title = fm.get("title", fp.stem.replace("-", " "))
        # Reuse body_start computed in the scan loop instead of
        # re-running the frontmatter regex per file.
        body = text[body_start:] if body_start else text
        definition = ""
        def_match = re.search(r">\s*\*\*(?:一句话定义|Definition)\*\*:\s*(.+)", body, re.IGNORECASE)
        if def_match:
            definition = def_match.group(1).strip()

        try:
            kind = _classify(llm, title, definition, body[:500])
        except Exception as exc:  # noqa: BLE001 - continue per-note processing; errors are audited
            print(f"  [{i+1}/{len(phase2)}] ERROR {fp.name}: {exc}")
            _emit_audit(
                logger,
                {
                    "event_type": "entity_type_backfill_error",
                    "file": str(fp.relative_to(vault_dir)),
                    "error": str(exc),
                },
            )
            errors += 1
            continue

        new_text = _inject_entity_type(text, kind)
        fp.write_text(new_text, encoding="utf-8")
        classified += 1
        stats[kind] = stats.get(kind, 0) + 1

        _emit_audit(
            logger,
            {
                "event_type": "entity_type_backfill",
                "file": str(fp.relative_to(vault_dir)),
                "entity_type": kind,
                "title": title,
            },
        )

        if (i + 1) % batch_size == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            print(
                f"  [{i+1}/{len(phase2)}] "
                f"classified={classified} errors={errors} "
                f"rate={rate:.1f}/s  last={fp.name} -> {kind}"
            )

    elapsed = time.time() - t0
    summary = {
        "total_evergreen": total,
        "already_correct": already_correct,
        "phase1_count": len(phase1),
        "phase2_count": len(phase2),
        "classified": classified,
        "errors": errors,
        "elapsed_seconds": round(elapsed, 1),
        "distribution": stats,
    }
    print(f"\nDone. classified={classified}, errors={errors}, elapsed={elapsed:.1f}s")
    print(f"Distribution: {json.dumps(stats, indent=2)}")

    _emit_audit(
        logger,
        {
            "event_type": "entity_type_backfill_summary",
            **summary,
        },
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill entity_type for Evergreen notes using LLM classification"
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=Path.cwd(),
        help="Vault root directory (default: cwd)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without modifying files")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of notes to classify (0=all)",
    )
    def _positive_int(value: str) -> int:
        parsed = int(value)
        if parsed <= 0:
            raise argparse.ArgumentTypeError("batch-size must be a positive integer")
        return parsed

    parser.add_argument(
        "--batch-size",
        type=_positive_int,
        default=50,
        help="Print progress every N notes",
    )
    args = parser.parse_args()
    result = run(args.vault_dir, dry_run=args.dry_run, limit=args.limit, batch_size=args.batch_size)
    if result.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
