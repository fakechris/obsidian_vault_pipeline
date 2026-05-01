"""ovp-backfill-entity-type — Batch-classify entity_type for Evergreen notes.

Traverses ``10-Knowledge/Evergreen/*.md``, skips notes that already have
``entity_type`` in their frontmatter, and uses an LLM to classify the
remaining notes into one of the 10 canonical core kinds defined in
``object_kinds.py``.

Emits structured audit events to ``60-Logs/entity-type-backfill.jsonl``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_FRONTMATTER_RE = re.compile(r"\A\s*\ufeff?---\r?\n(.*?)\r?\n---", re.DOTALL)

VALID_KINDS = frozenset(
    {
        "concept",
        "entity",
        "person",
        "company",
        "tool",
        "project",
        "paper",
        "event",
        "framework",
        "method",
    }
)

SYSTEM_PROMPT = """You are a knowledge taxonomy classifier. Given a note's title, one-sentence definition, and a short excerpt from its body, classify it into exactly ONE of these 10 entity types:

- concept: An abstract idea, principle, or pattern (e.g. "attention mechanism", "composability")
- entity: A named real-world entity that doesn't fit other specific types
- person: A specific individual (e.g. "Andrej Karpathy", "Ilya Sutskever")
- company: An organization or company (e.g. "OpenAI", "Google DeepMind")
- tool: A software tool, library, or product (e.g. "LangChain", "Docker", "Claude")
- project: A specific project or initiative (e.g. "Apollo Program", "GPT-4 red teaming")
- paper: A research paper or publication (e.g. "Attention Is All You Need")
- event: A specific event or conference (e.g. "NeurIPS 2024", "GPT-4 launch")
- framework: A structured approach or framework (e.g. "PARA method", "ReAct")
- method: A technique or algorithm (e.g. "chain-of-thought", "RLHF")

Respond with ONLY the entity type string, nothing else. No explanation, no quotes, no punctuation."""


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
    """Insert ``entity_type: <value>`` into frontmatter after ``type:`` or
    before the closing ``---``."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return text
    fm_block = m.group(1)
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
    if raw in VALID_KINDS:
        return raw
    return "concept"


def _emit_audit(log_path: Path, event: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


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

    log_path = vault_dir / "60-Logs" / "entity-type-backfill.jsonl"
    md_files = sorted(evergreen_dir.glob("*.md"))
    total = len(md_files)
    print(f"Found {total} Evergreen notes in {evergreen_dir}")

    needs_classification: list[tuple[Path, str, dict[str, str]]] = []
    skipped = 0
    for fp in md_files:
        text = fp.read_text(encoding="utf-8", errors="replace")
        fm, _, _ = _parse_frontmatter(text)
        if fm.get("entity_type") and fm["entity_type"] in VALID_KINDS:
            skipped += 1
            continue
        needs_classification.append((fp, text, fm))

    print(f"Already classified: {skipped}, needs classification: {len(needs_classification)}")
    if limit > 0:
        needs_classification = needs_classification[:limit]
        print(f"Limited to {limit} notes")

    if dry_run:
        print("[dry-run] Would classify these notes:")
        for fp, _, fm in needs_classification[:20]:
            print(f"  {fp.name}  (title={fm.get('title', '?')})")
        if len(needs_classification) > 20:
            print(f"  ... and {len(needs_classification) - 20} more")
        return {"dry_run": True, "to_classify": len(needs_classification), "skipped": skipped}

    llm = _build_llm_client()
    classified = 0
    errors = 0
    stats: dict[str, int] = {}
    t0 = time.time()

    for i, (fp, text, fm) in enumerate(needs_classification):
        title = fm.get("title", fp.stem.replace("-", " "))
        body_start = _FRONTMATTER_RE.match(text)
        body = text[body_start.end() :] if body_start else text
        definition = ""
        def_match = re.search(r">\s*\*\*一句话定义\*\*:\s*(.+)", body)
        if def_match:
            definition = def_match.group(1).strip()

        try:
            kind = _classify(llm, title, definition, body[:500])
        except Exception as exc:
            print(f"  [{i+1}/{len(needs_classification)}] ERROR {fp.name}: {exc}")
            _emit_audit(
                log_path,
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
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
            log_path,
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
                f"  [{i+1}/{len(needs_classification)}] "
                f"classified={classified} errors={errors} "
                f"rate={rate:.1f}/s  last={fp.name} -> {kind}"
            )

    elapsed = time.time() - t0
    summary = {
        "total_evergreen": total,
        "already_classified": skipped,
        "classified": classified,
        "errors": errors,
        "elapsed_seconds": round(elapsed, 1),
        "distribution": stats,
    }
    print(f"\nDone. classified={classified}, errors={errors}, elapsed={elapsed:.1f}s")
    print(f"Distribution: {json.dumps(stats, indent=2)}")

    _emit_audit(
        log_path,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Print progress every N notes",
    )
    args = parser.parse_args()
    result = run(args.vault_dir, dry_run=args.dry_run, limit=args.limit, batch_size=args.batch_size)
    if result.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
