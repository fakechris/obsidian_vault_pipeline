#!/usr/bin/env python3
"""Capture the Knowledge Mem arm for the M18/M21 held-out sample.

Reads a sample TSV with columns:
  case_id, input_path, category

For each source:
  POST /sources/ingest/file-path
  POST /sources/{id}/extract when needed
  poll GET /sources/{id} until extracted

Writes gitignored runtime artifacts under --out:
  <case>/source-detail.json
  <case>/memories.json
  <case>/memories.md
  kmem.json

The output schema is intentionally simple and case-id keyed so
scripts/m21_build_dashboard.py can render source-scoped memories without using
global search as a substitute.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path


def curl_json(method: str, url: str, payload: dict | None = None, timeout_s: int = 60):
    cmd = ["curl", "-sS", "--noproxy", "127.0.0.1", "-m", str(timeout_s), "-X", method, url]
    if payload is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(payload, ensure_ascii=False)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s + 10)
    if p.returncode != 0:
        raise RuntimeError(f"curl failed ({p.returncode}) for {url}: {p.stderr.strip()}")
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"non-JSON response from {url}: {e}: {p.stdout[:300]}") from e


def read_sample(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def memory_view(memory: dict) -> dict:
    return {
        "id": memory.get("id", ""),
        "title": memory.get("title", ""),
        "content": memory.get("content", ""),
        "unit_type": memory.get("unit_type", ""),
        "confidence": memory.get("confidence"),
        "chunk_index": memory.get("chunk_index"),
        "chunk_range": memory.get("chunk_range", ""),
    }


def enrich_full_content(base_url: str, view: dict) -> dict:
    """`GET /sources/{id}` returns memory `content` truncated to a ~200-char
    preview; `GET /memories/{id}` returns the full body. Fetch the full content
    so the AB judges KMEM on its real output, not a stub. Best-effort: on any
    failure keep the preview (and flag it)."""
    mid = view.get("id")
    if not mid:
        return view
    try:
        full = curl_json("GET", f"{base_url}/memories/{mid}", timeout_s=30)
    except RuntimeError:
        view["content_truncated"] = True
        return view
    content = full.get("content")
    if isinstance(content, str) and len(content) >= len(view.get("content", "")):
        view["content"] = content
        view["content_truncated"] = False
    return view


def write_case_markdown(path: Path, case_id: str, source_path: str, source: dict, memories: list[dict]) -> None:
    lines = [
        f"# Knowledge Mem source memories — {case_id}",
        "",
        f"- source_id: `{source.get('id', '')}`",
        f"- lifecycle: `{source.get('lifecycle_state', '')}`",
        f"- memory_count: `{source.get('memory_count', len(memories))}`",
        f"- source: `{source_path}`",
        "",
    ]
    for idx, memory in enumerate(memories, 1):
        lines += [
            f"## {idx}. {memory.get('title') or '(untitled)'}",
            "",
            f"- unit_type: `{memory.get('unit_type', '')}`",
            f"- confidence: `{memory.get('confidence', '')}`",
            f"- chunk_index: `{memory.get('chunk_index', '')}`",
            "",
            str(memory.get("content") or "").strip(),
            "",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")


def capture_case(base_url: str, row: dict, out_dir: Path, poll_max_s: int, poll_every_s: int) -> dict:
    case_id = row["case_id"]
    source_path = row["input_path"]
    case_dir = out_dir / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    existing_detail = case_dir / "source-detail.json"
    existing_memories = case_dir / "memories.json"
    if existing_detail.exists() and existing_memories.exists():
        detail = json.loads(existing_detail.read_text(encoding="utf-8"))
        memories = json.loads(existing_memories.read_text(encoding="utf-8"))
        source = detail.get("source") or {}
        if memories:
            return {
                "case_id": case_id,
                "path": source_path,
                "category": row.get("category", ""),
                "status": "available" if source.get("lifecycle_state") == "extracted" else "available_lifecycle_not_extracted",
                "source_id": source.get("id", ""),
                "lifecycle_state": source.get("lifecycle_state", ""),
                "memory_count": len(memories),
                "chunk_count": source.get("chunk_count"),
                "memories": memories,
                "cached": True,
            }

    ingest = curl_json("POST", f"{base_url}/sources/ingest/file-path", {"file_path": source_path})
    source_id = ingest.get("source_id")
    if not source_id:
        raise RuntimeError(f"{case_id}: ingest did not return source_id: {ingest}")

    detail = curl_json("GET", f"{base_url}/sources/{source_id}", timeout_s=30)
    state = (detail.get("source") or {}).get("lifecycle_state")
    if state != "extracted":
        curl_json("POST", f"{base_url}/sources/{source_id}/extract", {}, timeout_s=30)
        deadline = time.time() + poll_max_s
        while time.time() < deadline:
            time.sleep(poll_every_s)
            detail = curl_json("GET", f"{base_url}/sources/{source_id}", timeout_s=30)
            state = (detail.get("source") or {}).get("lifecycle_state")
            if state == "extracted" or (detail.get("memories") or []):
                break

    source = detail.get("source") or {}
    memories = [enrich_full_content(base_url, memory_view(m)) for m in (detail.get("memories") or [])]

    (case_dir / "source-detail.json").write_text(
        json.dumps(detail, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (case_dir / "memories.json").write_text(
        json.dumps(memories, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_case_markdown(case_dir / "memories.md", case_id, source_path, source, memories)

    if memories and source.get("lifecycle_state") == "extracted":
        status = "available"
    elif memories:
        status = "available_lifecycle_not_extracted"
    else:
        status = "empty_or_not_extracted"
    return {
        "case_id": case_id,
        "path": source_path,
        "category": row.get("category", ""),
        "status": status,
        "source_id": source_id,
        "lifecycle_state": source.get("lifecycle_state", ""),
        "memory_count": len(memories),
        "chunk_count": source.get("chunk_count"),
        "memories": memories,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default=".run/m18/sample.tsv", type=Path)
    ap.add_argument("--out", default=".run/m21/kmem", type=Path)
    ap.add_argument("--base-url", default="http://127.0.0.1:14242")
    ap.add_argument("--poll-max-s", type=int, default=420)
    ap.add_argument("--poll-every-s", type=int, default=10)
    ap.add_argument("--only", help="Comma-separated case ids for a smoke run")
    args = ap.parse_args()

    rows = read_sample(args.sample)
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        rows = [r for r in rows if r["case_id"] in wanted]
    args.out.mkdir(parents=True, exist_ok=True)

    cases: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for row in rows:
        case_id = row["case_id"]
        try:
            result = capture_case(args.base_url.rstrip("/"), row, args.out, args.poll_max_s, args.poll_every_s)
            cases[case_id] = result
            print(
                f"{case_id}: {result['status']} source={result['source_id']} "
                f"lifecycle={result['lifecycle_state']} memories={result['memory_count']}"
            )
        except Exception as e:
            errors[case_id] = str(e)
            print(f"{case_id}: ERROR {e}", file=sys.stderr)

    bundle = {
        "base_url": args.base_url,
        "sample": str(args.sample),
        "n_cases": len(cases),
        "n_errors": len(errors),
        "cases": cases,
        "errors": errors,
    }
    (args.out / "kmem.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out / 'kmem.json'} ({len(cases)} cases, {len(errors)} errors)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
