#!/usr/bin/env python3
"""M15 Phase 3A — KMEM arm over the 12-article sample manifest.

Per article: ingest (POST /sources/ingest/file-path) -> POST /sources/{id}/extract
-> poll lifecycle=extracted (~3 min/src) -> GET /sources/{id} -> write memories.

Reads docs/m15/sample-manifest.json. Writes .run/m15/kmem/<slug>/ (gitignored) +
.run/m15/kmem/cases.json (slug->path map). Idempotent: skips extract if already
extracted (sha256 dedup on ingest). Stable slugs s01..s12.

Usage: python3 scripts/m15_capture_kmem.py
"""
import json, os, subprocess, sys, time

BASE = "http://127.0.0.1:14242"
OUT = ".run/m15/kmem"
POLL_MAX_S = 420
POLL_EVERY = 15


def curl(args, m=60):
    try:
        p = subprocess.run(["curl", "-s", "--noproxy", "127.0.0.1", "-m", str(m)] + args,
                           capture_output=True, text=True, timeout=m + 10)
        return p.stdout
    except Exception:
        return ""


def cjson(args, m=60, retries=4):
    for a in range(retries):
        out = curl(args, m)
        try:
            return json.loads(out)
        except Exception:
            time.sleep(1.5 * (a + 1))
    return None


def main():
    manifest = json.load(open("docs/m15/sample-manifest.json"))
    sample = manifest["sample"]
    os.makedirs(OUT, exist_ok=True)
    cases = {}
    for idx, path in enumerate(sample, 1):
        slug = f"s{idx:02d}"
        cases[slug] = path
        nd = os.path.join(OUT, slug)
        os.makedirs(nd, exist_ok=True)
        # ingest (idempotent)
        ing = cjson(["-X", "POST", BASE + "/sources/ingest/file-path", "-H", "Content-Type: application/json",
                     "-d", json.dumps({"file_path": path})])
        sid = (ing or {}).get("source_id")
        if not sid:
            print(f"{slug}: INGEST FAILED ({os.path.basename(path)})")
            json.dump({"error": "ingest failed", "path": path}, open(f"{nd}/error.json", "w"))
            continue
        # current state
        d = cjson(["-X", "GET", f"{BASE}/sources/{sid}"]) or {}
        state = d.get("source", {}).get("lifecycle_state")
        if state != "extracted":
            curl(["-X", "POST", f"{BASE}/sources/{sid}/extract", "-H", "Content-Type: application/json", "-d", "{}"], m=30)
            deadline = time.time() + POLL_MAX_S
            while time.time() < deadline:
                time.sleep(POLL_EVERY)
                d = cjson(["-X", "GET", f"{BASE}/sources/{sid}"]) or {}
                if d.get("source", {}).get("lifecycle_state") == "extracted":
                    break
        src = d.get("source", {})
        memories = d.get("memories", []) or []
        json.dump(d, open(f"{nd}/source-detail.json", "w"), ensure_ascii=False, indent=1)
        json.dump(memories, open(f"{nd}/memories.json", "w"), ensure_ascii=False, indent=1)
        md = [f"# KMEM memories — {slug}", f"- source_id: {sid}", f"- path: {path}",
              f"- lifecycle: {src.get('lifecycle_state')}  memory_count: {src.get('memory_count')}  chunks: {src.get('chunk_count')}", ""]
        for i, m in enumerate(memories, 1):
            md += [f"## {i}. {m.get('title','(no title)')}",
                   f"- unit_type: {m.get('unit_type')}", f"- {m.get('content','').strip()}", ""]
        open(f"{nd}/memories.md", "w", encoding="utf-8").write("\n".join(md))
        print(f"{slug}: sid={sid} lifecycle={src.get('lifecycle_state')} memories={len(memories)} ({os.path.basename(path)})")
    json.dump(cases, open(os.path.join(OUT, "cases.json"), "w"), ensure_ascii=False, indent=2)
    print("done. cases ->", os.path.join(OUT, "cases.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
