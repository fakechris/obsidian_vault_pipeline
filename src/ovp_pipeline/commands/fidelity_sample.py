"""ovp-fidelity-sample — Build a stratified human-review web app for
evergreen → source fidelity.

Why this exists
---------------
Discussions on 2026-05-05 kept escalating from a normative principle
("no slop") into a prescriptive diagnosis ("OVP IS slopping right now,
must cut absorb immediately") without any measurement.  This tool makes
that measurement cheap: stratified sample of evergreens, present each
alongside the raw source body (when locally available), with claim-level
rubric inline.

What it produces
----------------
``60-Logs/fidelity-samples/<run_id>/checklist.html`` — single
self-contained HTML page:

    +-----------------------------------------------+
    | Sample N / 50  [Prev] [Next]  [Export] [Import] |
    +----------------------+------------------------+
    | EVERGREEN            | RAW SOURCE             |
    | (left, scrolls)      | (right, scrolls)       |
    | • title / source     | • paragraph 1          |
    | • claims:            | • paragraph 2 ⭐       |
    |   ◯ faithful etc.    | • paragraph 3          |
    |   notes [______]     | …                      |
    +----------------------+------------------------+

Click a claim → top-3 most-similar raw paragraphs get a relevance
ribbon and the right pane scrolls to the first one.  Similarity is
deterministic Jaccard over jieba-tokenized words.  No LLM judgment.

LocalStorage auto-saves rubric edits.  Export/Import dump rubric state
as JSON so reviews can be merged later.

Stratification axes
-------------------
Source category, derived from source_url domain:
    twitter   → x.com / twitter.com
    github    → github.com / gist.github.com / *.github.io
    paper     → arxiv.org / openreview / proceedings hosts / *.pdf URLs
    blog      → anthropic.com / langchain / openai docs / etc
    commentary→ simonwillison.net / martinfowler / personal blogs
    other     → catch-all
Floor of N per non-empty category so small ones still surface.

The scoring rubric is intentionally narrow.  Per claim:
    verdict ∈ {faithful, distorted, hallucinated, unverifiable}
    note    free text
Per sample:
    overall_verdict (auto-suggested from per-claim worst case)
    overall_note    free text

We don't try to compute a single "quality score" — see
feedback_calibration_discipline.md for why composite scores are a
slop trap.
"""

from __future__ import annotations

import argparse
import html
import json
import random
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

from ..runtime import VaultLayout, resolve_vault_dir


def _safe_json_for_script(obj: object) -> str:
    """JSON-encode ``obj`` and escape chars that would break a literal
    embed inside a ``<script>`` tag.

    A naive ``json.dumps`` lets a sample body containing the literal
    sequence ``</script>`` close the surrounding tag, breaking the
    page (and giving content-side script-injection vibes).  The
    OWASP-blessed fix is to escape ``<``/``>``/``&`` plus the JSON
    line separators ``U+2028``/``U+2029`` to ``\\uXXXX`` — the result
    is still valid JSON AND safe inside ``<script>…</script>``.
    """
    encoded = json.dumps(obj, ensure_ascii=False)
    return (
        encoded
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


SOURCE_CATEGORIES = ("twitter", "github", "paper", "blog", "commentary", "other")

_TWITTER_HOSTS = {"x.com", "twitter.com", "mobile.twitter.com"}
_GITHUB_HOSTS = {"github.com", "gist.github.com"}
_PAPER_HOSTS = {
    "arxiv.org", "openreview.net", "papers.nips.cc", "aclanthology.org",
    "proceedings.mlr.press", "papers.ssrn.com",
}
_PRIMARY_BLOG_HOSTS = {
    "anthropic.com", "developers.openai.com", "blog.langchain.com",
    "humanlayer.dev", "openai.com", "platform.openai.com",
    "deepmind.google", "huggingface.co",
}
_COMMENTARY_HOSTS = {
    "simonwillison.net", "martinfowler.com", "swyx.io", "thezvi.wordpress.com",
}


# ---------------------------------------------------------------------------
# Source categorization
# ---------------------------------------------------------------------------


def _categorize_source(source_url: str) -> str:
    if not source_url:
        return "other"
    try:
        netloc = urlparse(source_url).netloc.lower()
    except Exception:
        return "other"
    netloc = netloc[4:] if netloc.startswith("www.") else netloc
    if netloc in _TWITTER_HOSTS:
        return "twitter"
    if netloc in _GITHUB_HOSTS or netloc.endswith(".github.io"):
        return "github"
    if netloc in _PAPER_HOSTS or source_url.lower().endswith(".pdf"):
        return "paper"
    if netloc in _PRIMARY_BLOG_HOSTS:
        return "blog"
    if netloc in _COMMENTARY_HOSTS:
        return "commentary"
    return "other"


# ---------------------------------------------------------------------------
# Markdown frontmatter / body
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> dict | None:
    if not text.startswith("---"):
        return None
    try:
        end = text.index("---", 3)
    except ValueError:
        return None
    try:
        fm = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict):
        return None
    return fm


def _evergreen_body(text: str) -> str:
    if not text.startswith("---"):
        return text
    try:
        end = text.index("---", 3) + 3
    except ValueError:
        return text
    return text[end:].lstrip("\n")


def _raw_body(text: str) -> str:
    """Strip frontmatter from a raw source markdown."""
    return _evergreen_body(text)


# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------

# Sentence boundary characters — Chinese full stops, semicolons, EN periods,
# question marks, exclamations.  Kept narrow so we don't shred URLs/code.
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；])\s+|(?<=[.!?])\s+(?=[A-Z一-鿿])")


def _split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text)]
    return [p for p in parts if len(p) >= 8]


def _extract_claims(body: str) -> list[dict]:
    """Pull factual-ish claims out of the canonical evergreen layout.

    Evergreens follow a stable template — definition quote, ``📝 详细解释``
    section, ``为什么重要`` section, then wikilinks/source backrefs we
    don't want to score.  We split each prose section by sentence and
    label each claim with its source section so the reviewer can see
    where it came from.
    """
    claims: list[dict] = []

    # 1) Definition: the leading "> **定义**: ..." block, anywhere
    # in the first ~10 lines.  Some older evergreens use "> 定义" or
    # "> **Definition**".
    def_match = re.search(
        r"^>\s*(?:\*\*)?\s*(?:定义|Definition)\s*(?:\*\*)?\s*[:：]\s*(.+?)(?:\n\n|\Z)",
        body, re.MULTILINE | re.DOTALL,
    )
    if def_match:
        text = def_match.group(1).strip().replace("\n", " ")
        if text:
            claims.append({"section": "definition", "text": text})

    # 2) Detailed explanation
    detail = _section_body(body, ("📝 详细解释", "详细解释", "Detailed Explanation"))
    for sentence in _split_sentences(detail):
        claims.append({"section": "detail", "text": sentence})

    # 3) Why it matters
    why = _section_body(body, ("为什么重要", "Why it matters", "Why this matters"))
    for sentence in _split_sentences(why):
        claims.append({"section": "why", "text": sentence})

    # If template detection failed (older / different layout), fall back
    # to splitting the whole body by sentence so we still get something.
    if not claims:
        # skip wikilinks-only lines and headers
        cleaned = re.sub(r"^#.*$", "", body, flags=re.MULTILINE)
        cleaned = re.sub(r"^\s*-\s*\[\[.*?\]\].*$", "", cleaned, flags=re.MULTILINE)
        for sentence in _split_sentences(cleaned):
            claims.append({"section": "fallback", "text": sentence})

    # Add stable claim ids
    for i, c in enumerate(claims, start=1):
        c["id"] = f"c{i}"
    return claims


def _section_body(body: str, headings: tuple[str, ...]) -> str:
    """Return the prose body following any of the given headings, until
    the next ``##`` heading or end of text."""
    for h in headings:
        # Match ``## <heading>`` line, capture until next ## or EOF.
        pattern = rf"^##\s+{re.escape(h)}\s*\n(.*?)(?=^##\s|\Z)"
        m = re.search(pattern, body, re.MULTILINE | re.DOTALL)
        if m:
            return m.group(1).strip()
    return ""


# ---------------------------------------------------------------------------
# Raw-source segmentation
# ---------------------------------------------------------------------------


def _split_raw_segments(raw_body: str) -> list[dict]:
    """Split raw source body into reviewer-readable segments.

    Strategy:
    - First split on blank-line paragraph boundaries (\n\n+).
    - For very long paragraphs (> 600 chars), further split by sentence
      so a reviewer scanning the right pane doesn't drown in 1500-char
      walls of prose.

    Each segment is keyed by sequential index so the UI can scroll-
    target it.
    """
    if not raw_body:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw_body) if p.strip()]
    segments: list[dict] = []
    for para in paragraphs:
        # Skip image-only or very short structural paragraphs
        if len(para) < 20 and not re.search(r"[一-鿿]", para):
            continue
        if len(para) <= 600:
            segments.append({"text": para})
            continue
        # Split long paragraphs by sentence
        sentences = _split_sentences(para)
        if not sentences:
            segments.append({"text": para[:600] + "…"})
            continue
        buf: list[str] = []
        buf_len = 0
        for s in sentences:
            if buf_len + len(s) > 400 and buf:
                segments.append({"text": " ".join(buf)})
                buf = [s]
                buf_len = len(s)
            else:
                buf.append(s)
                buf_len += len(s)
        if buf:
            segments.append({"text": " ".join(buf)})
    for i, seg in enumerate(segments):
        seg["id"] = f"s{i + 1}"
    return segments


# ---------------------------------------------------------------------------
# Similarity (claim ↔ segment)
# ---------------------------------------------------------------------------

# Tiny stopword list — Chinese function words plus a few English ones.
# Big enough to cut noise, small enough that we don't overfit to one
# corpus.
_STOPWORDS = frozenset({
    "的", "了", "和", "与", "或", "在", "是", "也", "都", "及", "等", "等等",
    "为", "于", "对", "以", "从", "到", "把", "被", "让", "使", "如", "若",
    "但", "而", "并", "且", "其", "之", "这", "那", "这个", "那个", "这些",
    "那些", "我们", "他们", "你们", "可以", "可能", "应该", "需要", "通过",
    "进行", "包括", "提供", "实现", "成为", "用于", "做", "有", "无", "没", "多", "少",
    "the", "a", "an", "and", "or", "of", "in", "on", "to", "for", "with",
    "is", "are", "was", "were", "be", "been", "being", "by", "as", "at",
    "from", "this", "that", "these", "those", "it", "its", "you", "we",
    "they", "their", "our", "i", "he", "she", "his", "her", "than", "then",
    "but", "not", "no", "do", "does", "did", "have", "has", "had", "can",
    "could", "should", "would", "will", "may", "might", "if", "so",
})

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+|[一-鿿]+")


def _tokenize(text: str) -> set[str]:
    """Tokenize mixed CN/EN text into a set of meaningful tokens.

    For Chinese spans we prefer jieba; if jieba is not installed we
    fall back to per-character segmentation (matches the optional-jieba
    pattern in ``_truth_helpers.py``).  English/numeric is lowercased.
    Stopwords and length-1 ASCII tokens are dropped.
    """
    try:
        import jieba  # optional — gives much better CJK segmentation
        cut = jieba.cut
    except ImportError:
        cut = None
    tokens: set[str] = set()
    for span in _TOKEN_PATTERN.findall(text):
        if re.match(r"[一-鿿]+", span):
            if cut is not None:
                for tok in cut(span):
                    tok = tok.strip()
                    if not tok or tok in _STOPWORDS or len(tok) < 2:
                        continue
                    tokens.add(tok)
            else:
                # Per-char fallback: trigram-style CJK overlap is still
                # useful for the Jaccard ribbon, just noisier.
                for ch in span:
                    tokens.add(ch)
        else:
            tok = span.lower()
            if tok in _STOPWORDS or len(tok) < 2:
                continue
            tokens.add(tok)
    return tokens


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _attach_evidence(claims: list[dict], segments: list[dict], top_k: int = 3) -> None:
    """Mutate each claim to add ``evidence`` = list of segment ids
    (most-similar first) and a per-segment overlap score.

    If the best score is < 0.05 we still return the top-k but mark
    ``evidence_weak=True`` so the UI can render a "no strong match"
    warning — that's exactly the signal a reviewer wants for a possible
    hallucination.
    """
    if not segments:
        for c in claims:
            c["evidence"] = []
            c["evidence_weak"] = True
        return
    seg_tokens = [(seg["id"], _tokenize(seg["text"])) for seg in segments]
    for c in claims:
        c_tokens = _tokenize(c["text"])
        scored = [
            (seg_id, _jaccard(c_tokens, t))
            for seg_id, t in seg_tokens
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:top_k]
        c["evidence"] = [{"segment_id": sid, "score": round(score, 3)} for sid, score in top]
        c["evidence_weak"] = (top[0][1] if top else 0.0) < 0.05


# ---------------------------------------------------------------------------
# Evergreen scan + raw source index
# ---------------------------------------------------------------------------


def _scan_evergreens(evergreen_dir: Path) -> list[dict]:
    records: list[dict] = []
    for path in sorted(evergreen_dir.rglob("*.md")):
        if "_Candidates" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        if fm is None:
            continue
        records.append({
            "path": path,
            "slug": path.stem,
            "title": fm.get("title") or path.stem,
            "source_url": (fm.get("source_url") or "").strip(),
            "source_fingerprint": fm.get("source_fingerprint") or "",
            "date": str(fm.get("date") or ""),
            "frontmatter": fm,
            "body": _evergreen_body(text),
            "category": _categorize_source((fm.get("source_url") or "").strip()),
        })
    return records


def _build_processed_index(*roots: Path) -> dict[str, Path]:
    """Return ``source_url -> raw_path`` index across one or more roots.

    Filename-based matching against deep-dive stems is unreliable because
    deep-dive titles get truncated and the underscore/space normalization
    diverged across months.  Instead we read each raw source's
    frontmatter ``source`` field (the URL written by intake) and key the
    index by URL.

    Roots scanned:
      - ``50-Inbox/03-Processed/``  — articles that went through deep-dive
      - ``70-Archive/Pinboard/``    — Pinboard captures (mostly tweets +
                                       github, never get a deep-dive)
    """
    index: dict[str, Path] = {}
    for root in roots:
        if not root or not root.exists():
            continue
        for path in root.rglob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            fm = _parse_frontmatter(text)
            if not fm:
                continue
            for key in ("source", "source_url", "url"):
                value = fm.get(key)
                if isinstance(value, str) and value.strip().startswith(("http://", "https://")):
                    index.setdefault(value.strip(), path)
                    break
    return index


def _find_raw_source(source_url: str, index: dict[str, Path]) -> Path | None:
    if not source_url:
        return None
    return index.get(source_url.strip())


# ---------------------------------------------------------------------------
# Stratified sampling
# ---------------------------------------------------------------------------


def _stratified_sample(
    records: list[dict],
    *,
    sample_size: int,
    floor_per_category: int,
    rng: random.Random,
) -> list[dict]:
    by_category: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_category[r["category"]].append(r)

    populated_categories = [c for c in SOURCE_CATEGORIES if by_category[c]]
    if not populated_categories:
        return []

    allocations: dict[str, int] = {}
    for category in populated_categories:
        allocations[category] = min(floor_per_category, len(by_category[category]))

    total_floor = sum(allocations.values())
    remaining = max(0, sample_size - total_floor)
    pool_size = sum(
        max(0, len(by_category[c]) - allocations[c]) for c in populated_categories
    )
    if remaining and pool_size:
        weights = {
            c: max(0, len(by_category[c]) - allocations[c])
            for c in populated_categories
        }
        raw = {c: remaining * w / pool_size for c, w in weights.items()}
        floors = {c: int(v) for c, v in raw.items()}
        leftover = remaining - sum(floors.values())
        order = sorted(
            populated_categories,
            key=lambda c: (-(raw[c] - floors[c]), c),
        )
        for c in order[:leftover]:
            floors[c] += 1
        for c in populated_categories:
            allocations[c] += floors[c]

    sampled: list[dict] = []
    for category in populated_categories:
        pool = by_category[category]
        n = min(allocations[category], len(pool))
        if n <= 0:
            continue
        sampled.extend(rng.sample(pool, n))

    rng.shuffle(sampled)
    return sampled[:sample_size]


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>OVP Fidelity Review — __RUN_ID__</title>
<style>
  :root {
    --bg: #fafaf7;
    --panel: #ffffff;
    --border: #e0e0d8;
    --accent: #c0392b;
    --accent-soft: #f9e6e2;
    --ok: #27ae60;
    --warn: #d68910;
    --neutral: #6b6b66;
    --highlight: #fff7c2;
    --weak: #f3eecf;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; height: 100%; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", "PingFang SC",
                 "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    font-size: 14px;
    line-height: 1.55;
    color: #222;
    background: var(--bg);
  }
  header {
    position: sticky; top: 0; z-index: 10;
    display: flex; align-items: center; gap: 12px;
    padding: 10px 16px;
    background: var(--panel);
    border-bottom: 1px solid var(--border);
  }
  header h1 {
    font-size: 14px; font-weight: 600; margin: 0; flex-shrink: 0;
  }
  header .nav {
    display: flex; align-items: center; gap: 6px;
  }
  header button {
    padding: 4px 10px; border: 1px solid var(--border);
    background: var(--panel); border-radius: 4px; cursor: pointer;
    font-size: 13px;
  }
  header button:hover { background: #f3f3ed; }
  header .progress {
    flex-grow: 1; display: flex; align-items: center; gap: 8px;
  }
  header .progress-bar {
    flex-grow: 1; height: 4px; background: var(--border); border-radius: 2px;
    overflow: hidden;
  }
  header .progress-bar > div {
    height: 100%; background: var(--accent); width: 0%;
    transition: width 0.2s;
  }
  header .meta { color: var(--neutral); font-size: 12px; }
  .panes {
    display: grid;
    grid-template-columns: minmax(420px, 1fr) minmax(420px, 1fr);
    gap: 0;
    height: calc(100vh - 53px);
  }
  .pane {
    overflow-y: auto; padding: 16px 20px;
  }
  .pane.left { border-right: 1px solid var(--border); background: var(--panel); }
  .pane.right { background: var(--bg); }
  .sample-meta {
    margin-bottom: 16px; padding-bottom: 12px;
    border-bottom: 1px solid var(--border);
  }
  .sample-meta h2 { margin: 0 0 6px 0; font-size: 18px; }
  .sample-meta .meta-row {
    color: var(--neutral); font-size: 12px;
    margin: 2px 0;
  }
  .sample-meta a { color: var(--accent); text-decoration: none; }
  .sample-meta a:hover { text-decoration: underline; }
  .badge {
    display: inline-block; padding: 1px 8px;
    background: var(--accent-soft); color: var(--accent);
    border-radius: 10px; font-size: 11px; font-weight: 500;
  }
  details.evergreen-body {
    margin-bottom: 16px; padding: 8px 12px;
    background: #fcfcf8; border: 1px solid var(--border); border-radius: 4px;
  }
  details.evergreen-body summary {
    cursor: pointer; font-weight: 500; color: var(--neutral);
  }
  details.evergreen-body pre {
    margin: 8px 0 0 0; padding: 0;
    background: transparent; white-space: pre-wrap; word-wrap: break-word;
    font-family: ui-monospace, SFMono-Regular, "SF Mono", monospace;
    font-size: 12px;
  }
  .claims-header {
    margin-top: 8px; font-size: 13px; font-weight: 600; color: var(--neutral);
    text-transform: uppercase; letter-spacing: 0.04em;
  }
  .claim {
    margin: 12px 0; padding: 12px 14px;
    border: 1px solid var(--border); border-radius: 5px;
    background: #fdfdf8;
    transition: background 0.15s, border-color 0.15s;
  }
  .claim.active {
    border-color: var(--accent);
    background: var(--accent-soft);
  }
  .claim .claim-section {
    font-size: 11px; color: var(--neutral); text-transform: uppercase;
    letter-spacing: 0.04em; margin-bottom: 4px;
  }
  .claim .claim-text {
    cursor: pointer;
    margin: 0 0 6px 0;
    padding-right: 4px;
  }
  .claim .claim-text:hover { color: var(--accent); }
  .claim .evidence-meta {
    font-size: 11px; color: var(--neutral); margin-bottom: 8px;
  }
  .claim .evidence-meta.weak { color: var(--accent); font-weight: 500; }
  .rubric { display: flex; flex-wrap: wrap; gap: 4px; margin: 6px 0; }
  .rubric label {
    display: inline-flex; align-items: center; gap: 3px;
    padding: 2px 8px; border: 1px solid var(--border); border-radius: 12px;
    font-size: 12px; cursor: pointer; user-select: none;
    background: var(--panel);
  }
  .rubric label:hover { background: #eee; }
  .rubric input[type=radio] { margin: 0; }
  .rubric label.faithful_specific.checked { background: #e7f7ec; border-color: var(--ok); }
  .rubric label.faithful_generic.checked  { background: #fff3cf; border-color: #d4a017; }
  .rubric label.distorted.checked         { background: #fcecd1; border-color: var(--warn); }
  .rubric label.hallucinated.checked      { background: var(--accent-soft); border-color: var(--accent); }
  .rubric label.unverifiable.checked      { background: #ececec; border-color: var(--neutral); }
  /* sample-summary uses simpler labels */
  .rubric label.faithful.checked  { background: #e7f7ec; border-color: var(--ok); }
  .rubric label.diluted.checked   { background: #fff3cf; border-color: #d4a017; }
  .rubric label.partial.checked   { background: #fcecd1; border-color: var(--warn); }
  /* specifics chips: multi-select, smaller */
  .specifics {
    display: flex; flex-wrap: wrap; gap: 4px; margin: 6px 0 4px 0;
    align-items: center;
  }
  .specifics .label {
    font-size: 11px; color: var(--neutral); margin-right: 4px;
    text-transform: uppercase; letter-spacing: 0.04em;
  }
  .specifics label {
    display: inline-flex; align-items: center; gap: 2px;
    padding: 1px 7px; border: 1px solid var(--border); border-radius: 10px;
    font-size: 11px; cursor: pointer; user-select: none;
    background: var(--panel); color: var(--neutral);
  }
  .specifics label:hover { background: #eee; }
  .specifics input[type=checkbox] { display: none; }
  .specifics label.checked {
    background: #fdecea; border-color: var(--accent); color: var(--accent);
  }
  .claim textarea {
    width: 100%; min-height: 32px; padding: 4px 6px;
    border: 1px solid var(--border); border-radius: 3px;
    font-family: inherit; font-size: 12px; resize: vertical;
  }
  .summary {
    margin-top: 24px; padding: 14px;
    border: 1px solid var(--accent); border-radius: 5px;
    background: var(--accent-soft);
  }
  .summary h3 { margin: 0 0 10px 0; font-size: 14px; }
  .summary .tally {
    display: grid; grid-template-columns: repeat(5, 1fr);
    gap: 6px; margin: 10px 0; font-size: 12px;
  }
  .summary .tally div {
    padding: 4px 8px; background: var(--panel); border-radius: 3px;
    text-align: center;
  }
  .summary .tally strong { font-size: 16px; display: block; }
  .summary textarea {
    width: 100%; min-height: 50px; padding: 6px;
    border: 1px solid var(--border); border-radius: 3px;
    font-family: inherit; font-size: 12px;
  }
  .seg {
    margin: 10px 0; padding: 10px 14px;
    background: var(--panel); border: 1px solid var(--border);
    border-left: 3px solid transparent;
    border-radius: 3px;
    white-space: pre-wrap; word-wrap: break-word;
    font-size: 13px;
  }
  .seg.evidence-1 { border-left-color: var(--accent); background: var(--highlight); }
  .seg.evidence-2 { border-left-color: var(--warn); background: #fff8e0; }
  .seg.evidence-3 { border-left-color: var(--neutral); background: #fafadf; }
  .seg .seg-meta {
    font-size: 10px; color: var(--neutral); margin-bottom: 4px;
    text-transform: uppercase; letter-spacing: 0.04em;
  }
  .seg .seg-meta .score { color: var(--accent); font-weight: 500; }
  .raw-missing {
    padding: 16px; background: #fff3e0; border: 1px solid var(--warn);
    border-radius: 4px; color: #6e3a00;
  }
  .raw-missing a { color: var(--accent); }
</style>
</head>
<body>
<header>
  <h1>OVP Fidelity Review</h1>
  <div class="meta" id="run-id">__RUN_ID__</div>
  <div class="progress">
    <span class="meta" id="counter">0 / 0</span>
    <div class="progress-bar"><div id="progress-bar-fill"></div></div>
    <span class="meta" id="scored-count">0 scored</span>
  </div>
  <div class="nav">
    <button id="prev-btn">← Prev</button>
    <button id="next-btn">Next →</button>
    <button id="export-btn" title="下载所有评分为 JSON">Export</button>
    <button id="import-btn" title="加载之前导出的评分">Import</button>
    <input type="file" id="import-input" accept=".json" style="display:none">
  </div>
</header>
<main class="panes">
  <section class="pane left" id="left-pane">
    <div id="sample-content"></div>
  </section>
  <section class="pane right" id="right-pane">
    <div id="raw-content"></div>
  </section>
</main>
<script>
const SAMPLES = __SAMPLES_JSON__;
const RUN_ID = "__RUN_ID__";
const STORAGE_KEY = "ovp-fidelity-" + RUN_ID;

let currentIndex = 0;
let rubric = loadRubric();

function loadRubric() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) return JSON.parse(saved);
  } catch (e) {}
  return {};
}

function saveRubric() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(rubric));
  updateScoredCount();
}

function getSampleRubric(slug) {
  if (!rubric[slug]) {
    rubric[slug] = { claims: {}, overall: { verdict: "", note: "" } };
  } else {
    if (!rubric[slug].claims) rubric[slug].claims = {};
    if (!rubric[slug].overall) rubric[slug].overall = { verdict: "", note: "" };
  }
  return rubric[slug];
}

function isSampleScored(slug) {
  const r = rubric[slug];
  if (!r) return false;
  return Object.values(r.claims || {}).some(c => c.verdict);
}

function updateScoredCount() {
  const n = SAMPLES.filter(s => isSampleScored(s.slug)).length;
  document.getElementById("scored-count").textContent = `${n} scored`;
  document.getElementById("progress-bar-fill").style.width = `${(n / SAMPLES.length) * 100}%`;
}

function renderSample(idx) {
  const sample = SAMPLES[idx];
  const r = getSampleRubric(sample.slug);
  document.getElementById("counter").textContent = `${idx + 1} / ${SAMPLES.length}`;

  const left = document.getElementById("sample-content");
  const right = document.getElementById("raw-content");

  // ---- left pane: meta + claims + summary ----
  const meta = document.createElement("div");
  meta.className = "sample-meta";
  meta.innerHTML = `
    <h2>${escapeHtml(sample.title)}</h2>
    <div class="meta-row"><span class="badge">${sample.category}</span> &nbsp; <code>${sample.slug}</code></div>
    <div class="meta-row">source: ${sample.source_url ? `<a href="${escapeAttr(sample.source_url)}" target="_blank">${escapeHtml(sample.source_url)}</a>` : "<em>(none)</em>"}</div>
    <div class="meta-row">vault: <code>${escapeHtml(sample.path)}</code></div>
  `;

  const fullBody = document.createElement("details");
  fullBody.className = "evergreen-body";
  fullBody.innerHTML = `<summary>Evergreen full body (${sample.body.length} chars)</summary><pre>${escapeHtml(sample.body)}</pre>`;

  const claimsHeader = document.createElement("div");
  claimsHeader.className = "claims-header";
  claimsHeader.textContent = `Claims (${sample.claims.length})`;

  const VERDICTS = ["faithful_specific", "faithful_generic", "distorted", "hallucinated", "unverifiable"];
  const SPECIFICS = ["numbers", "names", "tradeoffs", "examples", "edge_cases"];

  const claimsBox = document.createElement("div");
  for (const claim of sample.claims) {
    // Migrate older single-tier "faithful" → "faithful_specific" so prior
    // rubric data (if any) still renders.  All new entries default to
    // empty verdict + empty dropped[].
    const stored = r.claims[claim.id] || {};
    const cr = {
      verdict: stored.verdict === "faithful" ? "faithful_specific" : (stored.verdict || ""),
      dropped: Array.isArray(stored.dropped) ? stored.dropped : [],
      note: stored.note || "",
    };
    r.claims[claim.id] = cr;

    const div = document.createElement("div");
    div.className = "claim";
    div.dataset.claimId = claim.id;
    const evidenceLabel = claim.evidence_weak
      ? `<span class="evidence-meta weak">⚠ no strong source match (best Jaccard ${claim.evidence[0] ? claim.evidence[0].score : 0})</span>`
      : `<span class="evidence-meta">top match Jaccard ${claim.evidence[0] ? claim.evidence[0].score : 0}</span>`;
    div.innerHTML = `
      <div class="claim-section">${claim.section}</div>
      <p class="claim-text">${escapeHtml(claim.text)}</p>
      ${evidenceLabel}
      <div class="rubric">
        ${VERDICTS.map(v =>
          `<label class="${v}${cr.verdict === v ? " checked" : ""}">
             <input type="radio" name="verdict-${claim.id}" value="${v}" ${cr.verdict === v ? "checked" : ""}> ${v.replace("_", " ")}
           </label>`
        ).join("")}
      </div>
      <div class="specifics">
        <span class="label">source had but claim dropped:</span>
        ${SPECIFICS.map(d =>
          `<label class="${cr.dropped.includes(d) ? "checked" : ""}">
             <input type="checkbox" value="${d}" ${cr.dropped.includes(d) ? "checked" : ""}> ${d}
           </label>`
        ).join("")}
      </div>
      <textarea placeholder="note (optional)">${escapeHtml(cr.note || "")}</textarea>
    `;
    div.querySelector(".claim-text").addEventListener("click", () => focusEvidence(claim));
    for (const inp of div.querySelectorAll(".rubric input[type=radio]")) {
      inp.addEventListener("change", e => {
        cr.verdict = e.target.value;
        saveRubric();
        for (const lab of div.querySelectorAll(".rubric label")) {
          lab.classList.toggle("checked", lab.querySelector("input").checked);
        }
        updateTally(sample, r);
      });
    }
    for (const inp of div.querySelectorAll(".specifics input[type=checkbox]")) {
      inp.addEventListener("change", e => {
        const v = e.target.value;
        if (e.target.checked) {
          if (!cr.dropped.includes(v)) cr.dropped.push(v);
        } else {
          cr.dropped = cr.dropped.filter(x => x !== v);
        }
        saveRubric();
        e.target.parentElement.classList.toggle("checked", e.target.checked);
      });
    }
    div.querySelector("textarea").addEventListener("input", e => {
      cr.note = e.target.value;
      saveRubric();
    });
    claimsBox.appendChild(div);
  }

  // Sample-level verdict labels.  ``diluted`` is the new bucket for
  // "every claim faithful but multiple were faithful_generic" — the
  // OVP-specific failure mode (abstraction inflation) we want to
  // surface separately from "everything is fine" (faithful) and
  // "some content was changed" (partial).
  const OVERALL_VERDICTS = ["faithful", "diluted", "partial", "hallucinated", "unverifiable"];
  const summary = document.createElement("div");
  summary.className = "summary";
  summary.innerHTML = `
    <h3>Sample summary</h3>
    <div class="tally" id="tally"></div>
    <div class="rubric" id="overall-rubric">
      ${OVERALL_VERDICTS.map(v =>
        `<label class="${v}${r.overall.verdict === v ? " checked" : ""}">
           <input type="radio" name="overall-verdict" value="${v}" ${r.overall.verdict === v ? "checked" : ""}> ${v}
         </label>`
      ).join("")}
    </div>
    <textarea id="overall-note" placeholder="overall note (optional)">${escapeHtml(r.overall.note || "")}</textarea>
  `;

  left.replaceChildren(meta, fullBody, claimsHeader, claimsBox, summary);

  for (const inp of summary.querySelectorAll("input[type=radio]")) {
    inp.addEventListener("change", e => {
      r.overall.verdict = e.target.value;
      saveRubric();
      for (const lab of summary.querySelectorAll(".rubric label")) {
        lab.classList.toggle("checked", lab.querySelector("input").checked);
      }
    });
  }
  summary.querySelector("textarea").addEventListener("input", e => {
    r.overall.note = e.target.value;
    saveRubric();
  });
  updateTally(sample, r);

  // ---- right pane: raw segments ----
  if (sample.segments && sample.segments.length) {
    const frag = document.createDocumentFragment();
    if (sample.raw_relpath) {
      const meta = document.createElement("div");
      meta.className = "meta-row";
      meta.style.marginBottom = "12px";
      meta.style.color = "var(--neutral)";
      meta.style.fontSize = "12px";
      meta.innerHTML = `Raw source: <code>${escapeHtml(sample.raw_relpath)}</code> &nbsp;·&nbsp; ${sample.segments.length} segments`;
      frag.appendChild(meta);
    }
    for (const seg of sample.segments) {
      const div = document.createElement("div");
      div.className = "seg";
      div.id = `seg-${seg.id}`;
      div.innerHTML = `<div class="seg-meta">${seg.id}</div>${escapeHtml(seg.text)}`;
      frag.appendChild(div);
    }
    right.replaceChildren(frag);
  } else {
    right.innerHTML = `
      <div class="raw-missing">
        <strong>Raw source not available locally.</strong><br>
        Open the source URL in your browser to evaluate manually:<br>
        ${sample.source_url ? `<a href="${escapeAttr(sample.source_url)}" target="_blank">${escapeHtml(sample.source_url)}</a>` : "<em>(no URL)</em>"}
      </div>
    `;
  }
  updateScoredCount();
}

function focusEvidence(claim) {
  // clear all
  for (const seg of document.querySelectorAll(".seg")) {
    seg.classList.remove("evidence-1", "evidence-2", "evidence-3");
  }
  for (const c of document.querySelectorAll(".claim")) c.classList.remove("active");
  const claimDiv = document.querySelector(`[data-claim-id="${claim.id}"]`);
  if (claimDiv) claimDiv.classList.add("active");

  if (!claim.evidence || !claim.evidence.length) return;
  for (let i = 0; i < claim.evidence.length; i++) {
    const seg = document.getElementById(`seg-${claim.evidence[i].segment_id}`);
    if (!seg) continue;
    seg.classList.add(`evidence-${i + 1}`);
  }
  // scroll first into view in right pane
  const first = document.getElementById(`seg-${claim.evidence[0].segment_id}`);
  if (first) {
    first.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

function updateTally(sample, r) {
  const tally = document.getElementById("tally");
  if (!tally) return;
  const counts = {
    faithful_specific: 0, faithful_generic: 0,
    distorted: 0, hallucinated: 0, unverifiable: 0, unscored: 0,
  };
  const droppedCounts = { numbers: 0, names: 0, tradeoffs: 0, examples: 0, edge_cases: 0 };
  for (const claim of sample.claims) {
    const cr = r.claims[claim.id] || {};
    const v = cr.verdict;
    if (v) counts[v] = (counts[v] || 0) + 1;
    else counts.unscored += 1;
    for (const d of (cr.dropped || [])) {
      droppedCounts[d] = (droppedCounts[d] || 0) + 1;
    }
  }
  const droppedAny = Object.values(droppedCounts).some(n => n > 0);
  const droppedRow = droppedAny
    ? `<div class="meta" style="grid-column: 1/-1; padding-top: 4px; font-size: 11px;">
         dropped: ${Object.entries(droppedCounts).filter(([_,n])=>n>0).map(([k,n])=>`${k} ×${n}`).join(", ")}
       </div>`
    : "";
  tally.innerHTML = `
    <div><strong>${counts.faithful_specific}</strong>specific</div>
    <div><strong>${counts.faithful_generic}</strong>generic</div>
    <div><strong>${counts.distorted}</strong>distorted</div>
    <div><strong>${counts.hallucinated}</strong>halluc.</div>
    <div><strong>${counts.unverifiable}</strong>unverif.</div>
    ${droppedRow}
  `;
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function escapeAttr(s) { return escapeHtml(s); }

document.getElementById("prev-btn").addEventListener("click", () => {
  if (currentIndex > 0) { currentIndex -= 1; renderSample(currentIndex); }
});
document.getElementById("next-btn").addEventListener("click", () => {
  if (currentIndex < SAMPLES.length - 1) { currentIndex += 1; renderSample(currentIndex); }
});
document.addEventListener("keydown", e => {
  if (e.target.matches("textarea, input")) return;
  if (e.key === "ArrowLeft" || e.key === "[") {
    if (currentIndex > 0) { currentIndex -= 1; renderSample(currentIndex); }
  } else if (e.key === "ArrowRight" || e.key === "]") {
    if (currentIndex < SAMPLES.length - 1) { currentIndex += 1; renderSample(currentIndex); }
  }
});
document.getElementById("export-btn").addEventListener("click", () => {
  const blob = new Blob(
    [JSON.stringify({ run_id: RUN_ID, exported_at: new Date().toISOString(), rubric }, null, 2)],
    { type: "application/json" }
  );
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `fidelity-rubric-${RUN_ID}.json`;
  a.click();
});
document.getElementById("import-btn").addEventListener("click", () => {
  document.getElementById("import-input").click();
});
document.getElementById("import-input").addEventListener("change", async e => {
  const file = e.target.files[0];
  if (!file) return;
  const text = await file.text();
  try {
    const parsed = JSON.parse(text);
    if (parsed && parsed.rubric) {
      if (!confirm("Replace current rubric with imported file?")) return;
      rubric = parsed.rubric;
      saveRubric();
      renderSample(currentIndex);
    }
  } catch (err) {
    alert("Import failed: " + err.message);
  }
});

renderSample(currentIndex);
</script>
</body>
</html>
"""


def _render_html(samples: list[dict], *, run_id: str, vault_dir: Path) -> str:
    """Inline-render the single self-contained HTML."""
    payload: list[dict] = []
    for s in samples:
        rel_evergreen = str(s["path"].relative_to(vault_dir))
        rel_raw = (
            str(s["raw_path"].relative_to(vault_dir))
            if s.get("raw_path") is not None
            else None
        )
        payload.append({
            "slug": s["slug"],
            "title": s["title"],
            "category": s["category"],
            "source_url": s["source_url"],
            "source_fingerprint": s["source_fingerprint"],
            "path": rel_evergreen,
            "raw_relpath": rel_raw,
            "body": s["body"],
            "claims": s["claims"],
            "segments": s["segments"],
        })
    return (
        _HTML_TEMPLATE
        .replace("__RUN_ID__", html.escape(run_id))
        .replace("__SAMPLES_JSON__", _safe_json_for_script(payload))
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ovp-fidelity-sample",
        description=(
            "Stratified-sample evergreens for human fidelity review. "
            "Renders a self-contained HTML web app — open in any browser, "
            "rubric auto-saves to LocalStorage, Export/Import via JSON."
        ),
    )
    parser.add_argument("--vault-dir", type=Path, default=None)
    parser.add_argument("--sample-size", type=int, default=50)
    parser.add_argument("--floor-per-category", type=int, default=5)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)

    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    layout = VaultLayout.from_vault(vault_dir)

    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = args.out_dir or (layout.logs_dir / "fidelity-samples" / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Scanning evergreens under {layout.evergreen_dir} …", file=sys.stderr)
    records = _scan_evergreens(layout.evergreen_dir)
    print(f"  found {len(records)} evergreens with parseable frontmatter", file=sys.stderr)

    pinboard_archive = vault_dir / "70-Archive" / "Pinboard"
    print("Indexing raw sources by source_url …", file=sys.stderr)
    print(f"  scanning {layout.processed_dir}", file=sys.stderr)
    print(f"  scanning {pinboard_archive}", file=sys.stderr)
    processed_index = _build_processed_index(layout.processed_dir, pinboard_archive)
    print(f"  indexed {len(processed_index)} raw sources by URL", file=sys.stderr)

    print(f"Sampling {args.sample_size} (floor {args.floor_per_category} per category) …", file=sys.stderr)
    samples = _stratified_sample(
        records,
        sample_size=args.sample_size,
        floor_per_category=args.floor_per_category,
        rng=rng,
    )
    print(f"  drew {len(samples)} samples", file=sys.stderr)

    # Enrich each sample with claims, raw path, segments, evidence
    print("Extracting claims and aligning evidence …", file=sys.stderr)
    matched = 0
    for sample in samples:
        sample["claims"] = _extract_claims(sample["body"])
        raw_path = _find_raw_source(sample["source_url"], processed_index)
        sample["raw_path"] = raw_path
        if raw_path and raw_path.exists():
            try:
                raw_text = raw_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                raw_text = ""
            sample["segments"] = _split_raw_segments(_raw_body(raw_text))
            matched += 1
        else:
            sample["segments"] = []
        _attach_evidence(sample["claims"], sample["segments"])
    print(f"  raw source matched: {matched}/{len(samples)}", file=sys.stderr)

    # Render
    out_html = out_dir / "checklist.html"
    out_html.write_text(
        _render_html(samples, run_id=run_id, vault_dir=vault_dir),
        encoding="utf-8",
    )

    manifest = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vault_dir": str(vault_dir),
        "sample_size_requested": args.sample_size,
        "sample_size_actual": len(samples),
        "floor_per_category": args.floor_per_category,
        "raw_source_match_count": matched,
        "seed": args.seed,
        "samples": [
            {
                "id": idx,
                "slug": s["slug"],
                "category": s["category"],
                "path": str(s["path"].relative_to(vault_dir)),
                "raw_path": (
                    str(s["raw_path"].relative_to(vault_dir))
                    if s["raw_path"] is not None else None
                ),
                "source_url": s["source_url"],
                "source_fingerprint": s["source_fingerprint"],
                "claim_count": len(s["claims"]),
                "segment_count": len(s["segments"]),
            }
            for idx, s in enumerate(samples, start=1)
        ],
    }
    out_json = out_dir / "manifest.json"
    out_json.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nWrote checklist HTML: {out_html}", file=sys.stderr)
    print(f"Wrote manifest JSON:   {out_json}", file=sys.stderr)
    print(
        f"\nOpen in browser:\n  open {out_html}\n", file=sys.stderr,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
