"""ovp-prompt-ab — A/B-compare the old absorb prompt vs a v2 candidate
prompt on the same set of source documents, render side-by-side HTML
for human review.

Why this exists
---------------
Fidelity sampling on 50 evergreens (2026-05-05) revealed that most are
``faithful_generic`` — abstraction-inflated.  Comparison with NM showed
the cause is structural (forced 定义/详细解释/为什么重要 template +
"抽得多比抽得少好" volume bias in the SYSTEM_PROMPT).  Before we change
the production absorb prompt, we want to verify on a handful of real
sources that v2 actually produces less generic output.

This tool runs both prompts against the same sources, collects raw JSON
output, and renders a 3-pane HTML where each row shows:

    [ source body ] [ old units ] [ new units ]

so a reviewer can read the source once and judge both extractions.

Outputs
-------
``60-Logs/prompt-ab/<run-id>/``
    ├── manifest.json
    ├── checklist.html         single-page review UI
    ├── <source-stem>.old.json raw old-prompt response
    └── <source-stem>.new.json raw new-prompt response

The new prompt's intent is documented in v2 — we don't transcribe NM's
prompt; we re-express the same goals (preserve specifics, allow empty
output, drop forced template) in OVP's language.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..llm_client import get_litellm_client
from ..runtime import VaultLayout, resolve_vault_dir, safe_json_for_script


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

# Snapshot of the production prompt (auto_evergreen_extractor.SYSTEM_PROMPT
# circa 2026-05-05).  We pin the text here rather than importing the
# module because we want the experiment baseline to be stable even if
# the production prompt evolves under us.
OLD_PROMPT = """你是知识提取专家。请从文章中提取**所有**有价值的原子知识单元(atomic knowledge units)。

## 数量目标(取决于原文长度和密度,不是硬上限)

- 短文(< 1k 词): 5-10 个
- 中等(1-3k 词): 8-20 个
- 长文(> 3k 词): 15-30+ 个

不要预设上限。让原文的密度决定数量;有 N 个独立可表达的知识单元,就抽 N 个。
**抽得多比抽得少好** —— 后续会自动去重。

## Evergreen 笔记标准

1. **原子性**: 一个 atomic unit 一个笔记 —— 一个具体的事实/方法/洞察,不是一个主题
2. **断言性**: 用**陈述句**命名,直接说出结论
3. **永久性**: 时间无关的知识(版本号/Twitter 用户名/具体日期不入)
4. **可链接**: 能跟其他原子单元连接
5. **非元数据**: 不要把文件名、仓库目录、README/AGENTS.md/package.json、GitHub Action、URL 本身当成概念

## unit_type 分类(显式标注)

每个原子单元必须标 `unit_type`,从以下 4 类选 1:

- **fact**: 客观事实/现象/数据
- **procedure**: 操作流程/方法/recipe
- **learning**: 经验/洞察/反直觉教训
- **concept**: 命名抽象/定义

## 输出格式(严格 JSON 数组,不要 markdown 包装)

[
  {
    "concept_name": "concept-name-kebab-case",
    "title": "Declarative claim sentence",
    "entity_type": "concept",
    "unit_type": "fact",
    "one_sentence_def": "一句话定义(中文,保留技术术语英文)",
    "explanation": "详细解释(2-4 句,中文)",
    "importance": "为什么重要(1-2 句)",
    "related_concepts": ["Related-1", "Related-2", "Related-3"]
  }
]
"""

# v2 prompt — written from scratch in OVP's voice.  Goals encoded:
#   - allow empty output (skip_reason)
#   - 0-8 cap, not 5-30
#   - 10 unit_types including method / tradeoff / failure_mode /
#     counterexample / case_detail (NM-inspired but redrafted)
#   - source_anchor with verbatim requirement
#   - explicit anti-rules: no enumeration shell, no Wikipedia-level
#     definitions, no abstraction inflation
#   - title must be a claim sentence not a category label
NEW_PROMPT = """你的任务:从一篇源文里抽取对 vault 有价值的 CandidateUnit。
你不是在总结源文,也不是在把源文改写成笔记。
你是在找出源文中那些**保留了原文具体物**(数字、命名实体、方法步骤、
工具名、反例、边界条件、对照选择)的可复用知识单元。

## 输出格式(严格 JSON,不要 markdown 包装)

{
  "source_value_summary": "一句话概括这篇源文的可抽取价值。如果价值很低,直说。",
  "units": [
    {
      "title": "一句完整的陈述句,不是名词短语",
      "unit_type": "fact|method|procedure|tradeoff|failure_mode|counterexample|case_detail|learning|decision|quote",
      "epistemic_role": "fact|interpretation|method|quote|attributed_claim",
      "content": "markdown,自包含,不需要回读源文也能理解",
      "source_anchor": "源文中逐字出现的短语/数字/名称/API,作为这条单元的具体锚点",
      "specifics": ["保留下来的具体物分类:numbers / names / tradeoffs / examples / edge_cases"]
    }
  ],
  "skip_reason": "如果 units 是空,说明为什么:常识 / 重复 / 没具体物 / 全是观点没证据 / 等等"
}

## 抽取规则,违反任何一条这条单元就不该存在:

1. **自包含。** 读这条 unit 不需要回去看源文。如果核心信息是 "用 X 方法解决 Y" 而 X 是什么没说,这条是空壳。要么把 X 具体写进来,要么不写。

2. **先选 unit_type,再写 content。形态必须匹配:**
   - fact:单点事实 + 至少一个具体锚点(数字/命名物/场景)
   - method:有名字的具体方法 + 操作要点 + 用什么工具/API
   - procedure:编号步骤,每步有具体动作和命令/工具
   - tradeoff:选 A 不选 B + 代价是什么 + 适用条件
   - failure_mode:在什么条件下会出什么问题
   - counterexample:与某个普遍说法不一致的具体例子
   - case_detail:具体案例(谁/在哪/做了什么/结果如何)
   - learning:观点 + 源文给出的依据(不能只有观点)
   - decision:做了什么选择 + 备选 + 理由
   - quote:值得逐字保留的原文(content 必须是逐字引用 + 你的简短标注)

3. **不抽常识。** "X 是用于 Y 的方法" 这种 textbook 定义,默认跳过。只在源文给出 (a) 反例 (b) 数字数据 (c) 实现 tradeoff (d) 具体操作细节 之一时,这个主题才值得抽。

4. **Title 是观点,不是分类。**
   ✗ "Skill Pack 生态" / "三层架构" / "Agent 记忆系统"
   ✓ "Hudson 的 Swift skill 偏广度,Antoine 偏深度"
   ✓ "把平台差异隔离在 schema 解析层,可让 view model 不变"
   读 title 应能立刻知道这条主张什么。

5. **不要 enumeration shell。** "X 由 5 大来源组成" / "三层架构实现关注点分离" 这种**只列骨架不展开差异**的列表,不算保留 specifics。要么每项都展开它的独特点,要么不写这条 enumeration。

6. **数量节制。** 0-8 单元。一篇短 tweet 可能 0-2;一篇长 paper 可能 5-8;**很少超过 8**。如果你写到第 6 条发现是在重复前面的意思,停。

7. **跳过样板。** markdown 标题、转场段、礼貌话术、互动提示("如果你觉得有用请关注")、boilerplate 不抽。

8. **宁可 0 条,不要凑数。** 如果这篇源文主要是观点反复 / 没有具体方法和数据 / 全是泛泛而谈,返回 units=[],写 skip_reason。这是被允许且鼓励的输出。

9. **每条 unit 的 content 必须包含至少一段源文中逐字出现的内容**(在 source_anchor 字段标出)。如果做不到,说明这条已经飘到太抽象的层次,不写。

10. **epistemic_role 区分清楚:** fact = 源文当事实陈述的内容;interpretation = 作者/你对事实的解释,不是事实本身;quote = 逐字引用;method = 可执行的操作描述;attributed_claim = 作者引用别人说的。不要把 interpretation 伪装成 fact。
"""


USER_TEMPLATE = """以下是源文 markdown(已去除 frontmatter)。请按你的规则抽取。

```
{body}
```
"""


# Cap the body sent to the LLM so a runaway clipping (occasionally
# hundreds of KB after Reader normalises a Substack post) can't blow
# past the model's context window.  30 KB ≈ ~10k tokens which fits
# comfortably under all production-tier providers.
MAX_LLM_BODY_CHARS = 30000


# ---------------------------------------------------------------------------
# Source loading + body extraction
# ---------------------------------------------------------------------------


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    try:
        end = text.index("---", 3) + 3
    except ValueError:
        return text
    return text[end:].lstrip("\n")


def _word_count(text: str) -> int:
    # rough — matches OLD_PROMPT's "短文/中等/长文" categories
    chinese = len(re.findall(r"[一-鿿]", text))
    english = len(re.findall(r"[A-Za-z]+", text))
    return chinese + english


@dataclass
class SourceInput:
    path: Path
    rel_path: str
    body: str
    word_count: int


def _safe_relpath(path: Path, vault_dir: Path) -> str:
    """Return a vault-relative display path; fall back to the absolute
    path when ``path`` lives outside the vault.

    The CLI accepts absolute ``--source`` paths, but
    ``Path.relative_to`` raises ``ValueError`` for paths outside
    ``vault_dir`` — using it unconditionally crashed the run.
    """
    try:
        return str(path.relative_to(vault_dir))
    except ValueError:
        return str(path)


def _load_source(path: Path, vault_dir: Path) -> SourceInput | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    body = _strip_frontmatter(text)
    return SourceInput(
        path=path,
        rel_path=_safe_relpath(path, vault_dir),
        body=body,
        word_count=_word_count(body),
    )


# ---------------------------------------------------------------------------
# LLM invocation
# ---------------------------------------------------------------------------


def _parse_json_response(text: str) -> dict | list:
    """Parse LLM JSON output, tolerating markdown code fences."""
    s = text.strip()
    # strip ```json ... ``` or ``` ... ``` wrappers
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"```\s*$", "", s)
    s = s.strip()
    return json.loads(s)


def _call(llm_call, system: str, body: str) -> tuple[str, object]:
    """Invoke LLM, return (raw_text, parsed_or_error_dict).

    Returns the raw response so the experiment can show the reviewer
    exactly what the model emitted (including parse failures, which are
    themselves a quality signal).
    """
    user = USER_TEMPLATE.format(body=body[:MAX_LLM_BODY_CHARS])
    try:
        raw = llm_call(system, user, 6000)
    except Exception as exc:
        return ("", {"error": str(exc), "stage": "llm_call"})
    try:
        parsed = _parse_json_response(raw)
        return (raw, parsed)
    except json.JSONDecodeError as exc:
        return (raw, {"error": str(exc), "stage": "json_parse"})


# ---------------------------------------------------------------------------
# Normalize old/new outputs into a comparable shape for the UI
# ---------------------------------------------------------------------------


def _normalize_old(parsed) -> list[dict]:
    """Old prompt returns a JSON array of concepts."""
    if not isinstance(parsed, list):
        return []
    out = []
    for c in parsed:
        if not isinstance(c, dict):
            continue
        out.append({
            "title": c.get("title") or c.get("concept_name", ""),
            "unit_type": c.get("unit_type") or "concept",
            "content": "\n\n".join(filter(None, [
                c.get("one_sentence_def", "").strip(),
                c.get("explanation", "").strip(),
                f"**为什么重要:** {c.get('importance', '').strip()}" if c.get("importance") else "",
            ])),
            "extra": {
                "concept_name": c.get("concept_name"),
                "entity_type": c.get("entity_type"),
                "related_concepts": c.get("related_concepts", []),
            },
        })
    return out


def _normalize_new(parsed) -> tuple[str, list[dict], str]:
    """New prompt returns a JSON object with units + skip_reason."""
    if not isinstance(parsed, dict):
        return ("", [], "")
    units = parsed.get("units") or []
    out = []
    for u in units:
        if not isinstance(u, dict):
            continue
        out.append({
            "title": u.get("title", ""),
            "unit_type": u.get("unit_type", ""),
            "epistemic_role": u.get("epistemic_role", ""),
            "content": u.get("content", ""),
            "source_anchor": u.get("source_anchor", ""),
            "specifics": u.get("specifics", []),
        })
    return (
        parsed.get("source_value_summary", ""),
        out,
        parsed.get("skip_reason", ""),
    )


# ---------------------------------------------------------------------------
# HTML rendering (3-pane: source / old / new)
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>OVP Prompt A/B — __RUN_ID__</title>
<style>
  :root {
    --bg: #fafaf7; --panel: #fff; --border: #e0e0d8;
    --old: #b7410e; --old-soft: #fdecea;
    --new: #1f6f3f; --new-soft: #e7f7ec;
    --src: #4a4a45; --neutral: #6b6b66;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; height: 100%; font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", "PingFang SC", "Microsoft YaHei", sans-serif; font-size: 13px; line-height: 1.5; color: #222; background: var(--bg); }
  header {
    position: sticky; top: 0; z-index: 10;
    display: flex; align-items: center; gap: 12px;
    padding: 8px 14px; background: var(--panel); border-bottom: 1px solid var(--border);
  }
  header h1 { font-size: 13px; font-weight: 600; margin: 0; flex-shrink: 0; }
  header button { padding: 3px 10px; border: 1px solid var(--border); background: var(--panel); border-radius: 4px; cursor: pointer; font-size: 12px; }
  header button:hover { background: #f3f3ed; }
  header .meta { color: var(--neutral); font-size: 12px; }
  header .progress { flex-grow: 1; }
  .panes {
    display: grid; grid-template-columns: 1.2fr 1fr 1fr; gap: 0;
    height: calc(100vh - 45px);
  }
  .pane { overflow-y: auto; padding: 14px 18px; }
  .pane.source { border-right: 1px solid var(--border); background: var(--panel); }
  .pane.old    { border-right: 1px solid var(--border); background: var(--bg); }
  .pane.new    { background: var(--bg); }
  .pane h2 { margin: 0 0 12px 0; font-size: 13px; font-weight: 600; padding-bottom: 6px; border-bottom: 1px solid var(--border); }
  .pane.old   h2 { color: var(--old); }
  .pane.new   h2 { color: var(--new); }
  .pane.source h2 { color: var(--src); }
  .source-meta { font-size: 11px; color: var(--neutral); margin-bottom: 10px; }
  .source-meta a { color: var(--old); text-decoration: none; }
  .source-body {
    background: #fcfcf8; border: 1px solid var(--border); border-radius: 4px;
    padding: 10px 12px; white-space: pre-wrap; word-wrap: break-word;
    font-size: 12px; max-height: calc(100vh - 200px); overflow-y: auto;
  }
  .summary-box {
    margin-bottom: 12px; padding: 8px 12px;
    background: var(--new-soft); border-left: 3px solid var(--new); border-radius: 3px;
    font-size: 12px;
  }
  .summary-box.skip {
    background: #fff3cf; border-left-color: #d4a017;
  }
  .unit {
    margin: 0 0 10px 0; padding: 10px 12px;
    background: var(--panel); border: 1px solid var(--border); border-radius: 4px;
  }
  .unit .ut-row {
    display: flex; gap: 6px; margin-bottom: 6px; flex-wrap: wrap;
    font-size: 11px; align-items: center;
  }
  .unit .badge {
    padding: 1px 7px; border-radius: 9px; font-size: 10px;
    background: var(--neutral); color: white;
    text-transform: uppercase; letter-spacing: 0.04em;
  }
  .unit .badge.fact   { background: #5a7a9e; }
  .unit .badge.method { background: #6f9143; }
  .unit .badge.procedure { background: #6f9143; }
  .unit .badge.learning  { background: #8a6a30; }
  .unit .badge.tradeoff  { background: #b06f1c; }
  .unit .badge.failure_mode { background: var(--old); }
  .unit .badge.counterexample { background: var(--old); }
  .unit .badge.case_detail { background: #4f6f8a; }
  .unit .badge.decision { background: #7a4f8a; }
  .unit .badge.concept { background: #999; }
  .unit .badge.quote { background: #6b6b66; }
  .unit .badge.epistemic { background: #b08055; }
  .unit .title {
    font-weight: 600; font-size: 13px; margin: 0 0 6px 0;
  }
  .unit .content {
    font-size: 12px; color: #333;
    white-space: pre-wrap; word-wrap: break-word;
  }
  .unit .anchor {
    margin-top: 6px; padding: 4px 8px;
    background: #fff7c2; border-left: 2px solid #d4a017;
    font-size: 11px; font-style: italic; border-radius: 2px;
  }
  .unit .specifics {
    margin-top: 4px; font-size: 11px; color: var(--neutral);
  }
  .unit .specifics .chip {
    display: inline-block; padding: 0 6px; margin-right: 3px;
    background: var(--old-soft); border-radius: 8px; color: var(--old);
  }
  .unit .extra {
    margin-top: 6px; font-size: 11px; color: var(--neutral);
  }
  .empty { padding: 14px; color: var(--neutral); font-style: italic; text-align: center; }
  .stats {
    margin-bottom: 8px; padding: 6px 10px;
    background: var(--panel); border-radius: 3px; font-size: 11px;
    display: flex; gap: 14px; flex-wrap: wrap;
  }
  .stats strong { color: var(--src); }
  .err {
    padding: 10px; background: #fff3e0; border: 1px solid #d4a017;
    border-radius: 3px; font-size: 11px; font-family: monospace;
  }
</style>
</head>
<body>
<header>
  <h1>OVP Prompt A/B</h1>
  <span class="meta" id="run-id">__RUN_ID__</span>
  <span class="progress meta" id="counter">0 / 0</span>
  <button id="prev-btn">← Prev</button>
  <button id="next-btn">Next →</button>
  <span class="meta">←/→ to navigate</span>
</header>
<main class="panes">
  <section class="pane source" id="src-pane"></section>
  <section class="pane old" id="old-pane"></section>
  <section class="pane new" id="new-pane"></section>
</main>
<script>
const DATA = __DATA_JSON__;
const RUN_ID = "__RUN_ID__";
let idx = 0;

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}

function unitCard(u, side) {
  const badges = [
    u.unit_type ? `<span class="badge ${escapeHtml(u.unit_type)}">${escapeHtml(u.unit_type)}</span>` : "",
    u.epistemic_role ? `<span class="badge epistemic">role: ${escapeHtml(u.epistemic_role)}</span>` : "",
  ].filter(Boolean).join("");
  const anchor = u.source_anchor
    ? `<div class="anchor">⚓ <em>${escapeHtml(u.source_anchor)}</em></div>` : "";
  const specifics = (u.specifics && u.specifics.length)
    ? `<div class="specifics">specifics: ${u.specifics.map(s => `<span class="chip">${escapeHtml(s)}</span>`).join("")}</div>`
    : "";
  const extra = u.extra ? `<div class="extra">slug: <code>${escapeHtml(u.extra.concept_name||"")}</code> &middot; entity: ${escapeHtml(u.extra.entity_type||"")} &middot; related: ${(u.extra.related_concepts||[]).map(escapeHtml).join(", ") || "—"}</div>` : "";
  return `
    <div class="unit">
      <div class="ut-row">${badges}</div>
      <div class="title">${escapeHtml(u.title || "(no title)")}</div>
      <div class="content">${escapeHtml(u.content || "")}</div>
      ${anchor}
      ${specifics}
      ${extra}
    </div>
  `;
}

function render(i) {
  const r = DATA[i];
  document.getElementById("counter").textContent = `${i+1} / ${DATA.length}`;

  // SOURCE pane
  document.getElementById("src-pane").innerHTML = `
    <h2>Source</h2>
    <div class="source-meta">
      <code>${escapeHtml(r.rel_path)}</code><br>
      ${r.source_url ? `<a href="${escapeHtml(r.source_url)}" target="_blank">${escapeHtml(r.source_url)}</a><br>` : ""}
      ${r.word_count} words &middot; ${r.body_chars} chars
    </div>
    <div class="source-body">${escapeHtml(r.body)}</div>
  `;

  // OLD pane
  let oldHtml = `<h2>OLD prompt — ${r.old.units.length} units</h2>`;
  oldHtml += `<div class="stats"><strong>${r.old.units.length}</strong> units extracted</div>`;
  if (r.old.error) {
    oldHtml += `<div class="err">${escapeHtml(JSON.stringify(r.old.error))}</div>`;
  } else if (r.old.units.length === 0) {
    oldHtml += `<div class="empty">(no units)</div>`;
  } else {
    oldHtml += r.old.units.map(u => unitCard(u, "old")).join("");
  }
  document.getElementById("old-pane").innerHTML = oldHtml;

  // NEW pane
  let newHtml = `<h2>NEW prompt — ${r.new.units.length} units</h2>`;
  if (r.new.summary) {
    newHtml += `<div class="summary-box">📋 <strong>summary:</strong> ${escapeHtml(r.new.summary)}</div>`;
  }
  if (r.new.skip_reason) {
    newHtml += `<div class="summary-box skip">⏭ <strong>skip_reason:</strong> ${escapeHtml(r.new.skip_reason)}</div>`;
  }
  newHtml += `<div class="stats"><strong>${r.new.units.length}</strong> units extracted</div>`;
  if (r.new.error) {
    newHtml += `<div class="err">${escapeHtml(JSON.stringify(r.new.error))}</div>`;
  } else if (r.new.units.length === 0 && !r.new.skip_reason) {
    newHtml += `<div class="empty">(no units, no skip_reason — possibly LLM failed to follow schema)</div>`;
  } else if (r.new.units.length === 0) {
    newHtml += `<div class="empty">(model chose to skip — see reason above)</div>`;
  } else {
    newHtml += r.new.units.map(u => unitCard(u, "new")).join("");
  }
  document.getElementById("new-pane").innerHTML = newHtml;

  // scroll all panes to top on switch
  for (const id of ["src-pane","old-pane","new-pane"]) {
    document.getElementById(id).scrollTop = 0;
  }
}

document.getElementById("prev-btn").onclick = () => { if (idx>0) { idx--; render(idx); } };
document.getElementById("next-btn").onclick = () => { if (idx<DATA.length-1) { idx++; render(idx); } };
document.addEventListener("keydown", e => {
  if (e.target.matches("textarea, input")) return;
  if (e.key === "ArrowLeft" || e.key === "[") { if (idx>0) { idx--; render(idx); } }
  else if (e.key === "ArrowRight" || e.key === "]") { if (idx<DATA.length-1) { idx++; render(idx); } }
});
render(idx);
</script>
</body>
</html>
"""


def _render_html(rows: list[dict], *, run_id: str) -> str:
    return (
        _HTML
        .replace("__RUN_ID__", run_id)
        .replace("__DATA_JSON__", safe_json_for_script(rows))
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ovp-prompt-ab",
        description=(
            "Run old vs v2 absorb prompt against the same sources, "
            "save raw JSON, render side-by-side HTML."
        ),
    )
    parser.add_argument("--vault-dir", type=Path, default=None)
    parser.add_argument(
        "--source", action="append", type=Path, default=None,
        help="Path to a source markdown (relative to vault or absolute). "
             "Pass --source repeatedly. Required.",
    )
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    layout = VaultLayout.from_vault(vault_dir)
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = args.out_dir or (layout.logs_dir / "prompt-ab" / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not args.source:
        print(
            "error: at least one --source is required.\n"
            "Suggestion: pick 6 covering short tweet / long tweet / "
            "tech blog / opinion blog / paper / low-value article.",
            file=sys.stderr,
        )
        return 2

    # Resolve source paths
    sources: list[SourceInput] = []
    for raw in args.source:
        p = raw if raw.is_absolute() else vault_dir / raw
        if not p.exists():
            print(f"warning: source not found, skipping: {p}", file=sys.stderr)
            continue
        s = _load_source(p, vault_dir)
        if s:
            sources.append(s)
    if not sources:
        print("error: no readable sources", file=sys.stderr)
        return 2

    # LLM client
    client = get_litellm_client(vault_dir=vault_dir)
    if client is None:
        print("error: no LLM client available (no API key configured)", file=sys.stderr)
        return 2
    llm_call = client.call

    print(f"running A/B on {len(sources)} sources …", file=sys.stderr)

    rows: list[dict] = []
    for s in sources:
        print(f"  {s.rel_path} ({s.word_count} words) …", file=sys.stderr)
        # OLD
        old_raw, old_parsed = _call(llm_call, OLD_PROMPT, s.body)
        if isinstance(old_parsed, dict) and "error" in old_parsed:
            old_units = []
            old_err = old_parsed
        else:
            old_units = _normalize_old(old_parsed)
            old_err = None
        # NEW
        new_raw, new_parsed = _call(llm_call, NEW_PROMPT, s.body)
        if isinstance(new_parsed, dict) and "error" in new_parsed:
            new_summary, new_units, new_skip = "", [], ""
            new_err = new_parsed
        else:
            new_summary, new_units, new_skip = _normalize_new(new_parsed)
            new_err = None

        # Save raw — suffix with a short hash of the absolute path so
        # two ``--source`` files that share a basename (e.g. two
        # different ``README.md``) don't clobber each other's outputs.
        path_hash = hashlib.sha1(
            str(s.path.resolve()).encode("utf-8")
        ).hexdigest()[:8]
        stem = f"{s.path.stem.replace('/', '_')}_{path_hash}"
        (out_dir / f"{stem}.old.json").write_text(
            json.dumps({"raw": old_raw, "parsed": old_parsed}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (out_dir / f"{stem}.new.json").write_text(
            json.dumps({"raw": new_raw, "parsed": new_parsed}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Truncate body for embedding in HTML so the page doesn't balloon
        body_for_ui = s.body if len(s.body) <= 12000 else s.body[:12000] + "\n\n[… truncated …]"
        rows.append({
            "rel_path": s.rel_path,
            "source_url": _extract_source_url(s.path),
            "word_count": s.word_count,
            "body_chars": len(s.body),
            "body": body_for_ui,
            "old": {"units": old_units, "error": old_err},
            "new": {"summary": new_summary, "units": new_units, "skip_reason": new_skip, "error": new_err},
        })
        print(f"      old: {len(old_units)} units{' [ERROR]' if old_err else ''}", file=sys.stderr)
        print(f"      new: {len(new_units)} units{' [SKIP: '+new_skip[:60]+']' if new_skip else ''}{' [ERROR]' if new_err else ''}", file=sys.stderr)

    # Manifest
    manifest = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vault_dir": str(vault_dir),
        "source_count": len(sources),
        "sources": [
            {
                "rel_path": s.rel_path,
                "word_count": s.word_count,
                "old_unit_count": len(rows[i]["old"]["units"]),
                "new_unit_count": len(rows[i]["new"]["units"]),
                "new_skip_reason": rows[i]["new"]["skip_reason"],
            }
            for i, s in enumerate(sources)
        ],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    # HTML
    html = _render_html(rows, run_id=run_id)
    out_html = out_dir / "checklist.html"
    out_html.write_text(html, encoding="utf-8")

    # Summary
    print("\n--- summary ---", file=sys.stderr)
    print(f"  sources:        {len(sources)}", file=sys.stderr)
    total_old = sum(len(r["old"]["units"]) for r in rows)
    total_new = sum(len(r["new"]["units"]) for r in rows)
    print(f"  old units:      {total_old}  (avg {total_old/len(sources):.1f}/source)", file=sys.stderr)
    print(f"  new units:      {total_new}  (avg {total_new/len(sources):.1f}/source)", file=sys.stderr)
    skip_count = sum(1 for r in rows if r["new"]["skip_reason"])
    print(f"  new skipped:    {skip_count}/{len(sources)}", file=sys.stderr)
    print(f"\n  HTML:     {out_html}", file=sys.stderr)
    print(f"  Manifest: {out_dir / 'manifest.json'}", file=sys.stderr)
    return 0


def _extract_source_url(path: Path) -> str:
    """Read ``path``'s frontmatter and return its canonical ``source:``
    URL, if any.  Thin adapter over ``source_dedup.extract_source_url``
    (the public helper that intake URL dedup also uses) so this CLI
    can't drift from the field-recognition rules used by the rest of
    the pipeline.
    """
    from ..source_dedup import extract_source_url, read_file_head
    try:
        text = read_file_head(path)
    except OSError:
        return ""
    return extract_source_url(text) or ""


if __name__ == "__main__":
    sys.exit(main())
