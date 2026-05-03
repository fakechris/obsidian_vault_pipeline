"""ovp-score-domain — LLM-assisted authority scoring for one new host.

Usage::

    ovp-score-domain mp.weixin.qq.com --vault-dir ~/Documents/ovp-vault
    ovp-score-domain cloudflare.com --apply           # write override after confirm
    ovp-score-domain xyz.com --offline                # heuristics-only fallback

Reads sample URLs + article titles from the vault for the given host,
calls the LLM with a structured prompt, gets back a verdict
``{authority, bucket, rationale}``, and (if ``--apply``) appends to
``60-Logs/domain_overrides.yaml``.

Cost
----

One LLM call per invocation, ~¥0.001 with MiniMax-M2.7-highspeed.
Cheap enough to run during weekly ``ovp-source-coverage`` review.

Offline fallback
----------------

If ``--offline`` is set or no LLM client is configured, the command
emits a heuristic-only stub with ``authority=0.55, bucket=mixed,
source=heuristic`` and lets the user fill in the rationale manually
before applying.

Idempotence
-----------

If the host already has an entry in ``domain_overrides.yaml`` and
``--force`` is not set, the command shows the existing entry and exits.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any

from ..source_signals.url_utils import normalize_host


_LLM_SYSTEM_PROMPT = """你是一个知识图谱的来源权威性评分专家。

任务:给定一个域名 + 几篇该域名下文章的样本(URL + 标题),输出 JSON:

{
  "authority": <0.0-1.0 的数字>,
  "bucket": "canonical" | "mixed" | "low",
  "rationale": "<1-2 句中文,说明为什么这个分数>"
}

评分锚点(严格遵守):
- 0.90+ canonical: 一线 AI 实验室官方博客 / 主要技术公司工程博客 / 顶级学术期刊 / 知名研究员个人网站
- 0.75-0.85 mixed-high: 有信誉的科技媒体 / 知名作者的 Substack / 大厂工程团队博客
- 0.55-0.70 mixed: 一般质量但仍有信号的平台聚合者(微信公众号、Medium、Substack、知乎、reddit)
- 0.40-0.50 low-mixed: 营销博客、个人小博客、未知来源
- < 0.40 low: 营销农场、内容农场、Spam、SEO 站

注意:
- 中文平台不要因为是中文就降分;按等量逻辑评估
- 平台聚合者(WeChat / Medium / 知乎)默认 0.55,不能更高,因为质量取决于具体作者/账号
- 单一作者博客权重应基于作者影响力,不是平台
"""


def _sample_for_host(vault_dir: Path, host: str, max_samples: int = 5) -> list[dict]:
    """Pull up to ``max_samples`` source URLs + article titles for the host
    from knowledge.db.  Returns ``[]`` if the DB doesn't have data."""
    db_path = vault_dir / "60-Logs" / "knowledge.db"
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        try:
            # SQLite doesn't allow column aliases in WHERE — repeat
            # the json_extract expression there.
            rows = conn.execute(
                "SELECT json_extract(frontmatter_json, '$.source_url') AS url, "
                "json_extract(frontmatter_json, '$.title') AS title, "
                "json_extract(frontmatter_json, '$.author') AS author "
                "FROM pages_index "
                "WHERE json_extract(frontmatter_json, '$.source_url') LIKE ?",
                (f"%{host}%",),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        out: list[dict] = []
        for url, title, author in rows[:max_samples]:
            if not url:
                continue
            if normalize_host(url) != host:
                continue
            out.append({
                "url": url,
                "title": title or "(no title)",
                "author": author or "",
            })
        return out
    finally:
        conn.close()


def _existing_override(vault_dir: Path, host: str) -> dict | None:
    overrides = vault_dir / "60-Logs" / "domain_overrides.yaml"
    if not overrides.exists():
        return None
    try:
        import yaml
    except ImportError:
        return None
    try:
        data = yaml.safe_load(overrides.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return None
    return (data.get("domains") or {}).get(host)


def _llm_score_domain(host: str, samples: list[dict], vault_dir: Path) -> dict | None:
    """Call the LLM client, parse the JSON verdict.  Returns None on failure."""
    try:
        from ..llm_client import get_litellm_client
    except ImportError:
        return None
    client = get_litellm_client(vault_dir=vault_dir)
    if client is None:
        return None

    user_prompt = f"""域名: {host}

样本文章 ({len(samples)} 篇):
""" + "\n".join([
        f"- {s['url']}\n  标题: {s['title']}"
        + (f"\n  作者: {s['author']}" if s['author'] else "")
        for s in samples
    ]) + "\n\n请输出 JSON 格式的评分。"

    text = client.call(_LLM_SYSTEM_PROMPT, user_prompt, max_tokens=600)
    try:
        # Greedy match — the negated-set ``[^{}]`` form fails on
        # nested braces or braces inside string values (e.g. inside
        # a multi-paragraph rationale).  ``\{.*\}`` with re.DOTALL
        # works for the realistic LLM-output shape.
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        parsed = json.loads(m.group(0))
    except (json.JSONDecodeError, AttributeError):
        return None
    try:
        authority = float(parsed.get("authority"))
    except (TypeError, ValueError):
        return None
    bucket = str(parsed.get("bucket", "mixed")).lower()
    if bucket not in {"canonical", "mixed", "low"}:
        bucket = "mixed"
    return {
        "authority": max(0.0, min(1.0, authority)),
        "bucket": bucket,
        "rationale": str(parsed.get("rationale", "")).strip(),
    }


def _apply_override(
    vault_dir: Path, host: str, verdict: dict, source: str,
) -> Path:
    """Append/update the host's entry in domain_overrides.yaml.

    Preserves any other domains in the file.  Returns the path written.
    """
    try:
        import yaml
    except ImportError:
        raise SystemExit("PyYAML not available; install or hand-edit yaml")

    path = vault_dir / "60-Logs" / "domain_overrides.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError):
            existing = {}
    else:
        existing = {}

    domains = existing.get("domains") or {}
    domains[host] = {
        "authority": verdict["authority"],
        "bucket": verdict["bucket"],
        "rationale": verdict.get("rationale", ""),
        "source": source,
        "added_at": date.today().isoformat(),
    }
    existing["domains"] = domains
    path.write_text(
        yaml.safe_dump(existing, allow_unicode=True, sort_keys=True),
        encoding="utf-8",
    )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="LLM-assisted authority scoring for one host",
    )
    parser.add_argument("host", help="Host to score (e.g. mp.weixin.qq.com)")
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument("--apply", action="store_true",
                        help="Write to domain_overrides.yaml without prompting")
    parser.add_argument("--offline", action="store_true",
                        help="Skip LLM call; emit heuristic stub for manual edit")
    parser.add_argument("--force", action="store_true",
                        help="Re-score even if host already in overrides")
    args = parser.parse_args(argv)

    vault = args.vault_dir.resolve()
    if not vault.is_dir():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 2

    host = args.host.lower()
    if host.startswith("www."):
        host = host[4:]

    existing = _existing_override(vault, host)
    if existing and not args.force:
        print(f"{host} already has an override:")
        print(f"  authority: {existing.get('authority')}")
        print(f"  bucket:    {existing.get('bucket')}")
        print(f"  rationale: {existing.get('rationale')}")
        print("Use --force to re-score.")
        return 0

    samples = _sample_for_host(vault, host, max_samples=5)
    if not samples:
        print(f"warning: no source samples found for {host} in knowledge.db")

    if args.offline:
        verdict = {
            "authority": 0.55,
            "bucket": "mixed",
            "rationale": f"Heuristic default for {host} (no LLM, manual review pending)",
        }
        verdict_source = "heuristic"
    else:
        verdict = _llm_score_domain(host, samples, vault)
        if verdict is None:
            print(f"LLM scoring failed for {host}; falling back to heuristic.")
            verdict = {
                "authority": 0.55,
                "bucket": "mixed",
                "rationale": f"LLM unavailable for {host}; manual review pending",
            }
            verdict_source = "heuristic"
        else:
            verdict_source = "llm_assisted"

    print(f"\n=== Verdict for {host} ===")
    print(f"  authority: {verdict['authority']:.2f}")
    print(f"  bucket:    {verdict['bucket']}")
    print(f"  rationale: {verdict['rationale']}")
    print(f"  source:    {verdict_source}")

    if args.apply:
        path = _apply_override(vault, host, verdict, verdict_source)
        print(f"\n→ Written to {path}")
        return 0

    print("\nDry-run.  Pass --apply to write to domain_overrides.yaml.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
