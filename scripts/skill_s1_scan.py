#!/usr/bin/env python3
"""S1 — skill-learning value experiment, stage 1: deterministic signal-density scan.

Scans local coding-agent transcripts (Claude Code + Codex) and counts, per
session/project, the deterministic signals defined in
docs/design/skill-learning-from-agent-transcripts.md §L2:

  D1  error->fix      : failed tool call later followed by a success of the
                        same tool in the same session (Claude: tool_result
                        is_error; Codex: "Process exited with code N", N!=0).
                        Two granularities: d1_error_fix pairs every failure
                        with a later same-tool success (loose upper bound);
                        d1_clusters collapses a failure run (failures of one
                        tool until its next success) into one window (tight).
  D2  user correction : short user text message carrying a correction marker
  D3  repeated proc   : normalized bash-command heads recurring across >=3
                        sessions of the same project
  D4  permission deny : is_error result whose text shows the user/harness
                        rejected the call
  D5  hook feedback   : is_error / system text mentioning a hook block

Pure counting: no LLM, no content is written out except normalized command
heads (first two shell tokens, truncated). Artifacts go to .run/skill-s1/
(NEVER /tmp). Pre-registered decision rules live in the design doc §7.

Usage: python3 scripts/skill_s1_scan.py [--out .run/skill-s1]
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import time

CORRECTION_MARKERS = [
    # zh
    '不对', '不是这', '错了', '搞错', '不要这', '别这', '应该是', '其实是', '你理解错',
    '撤销', '回退', '改回',
    # en
    'wrong', "that's not", 'not what i', 'undo that', 'revert that', 'stop,',
    "don't do that", 'no, ', 'no. ', 'incorrect',
]

DENY_MARKERS = [
    "user doesn't want", 'user rejected', 'permission denied by user',
    'denied by user', 'declined', 'rejected this tool',
]

HOOK_MARKERS = ['hook blocked', 'blocked by hook', 'posttooluse', 'pretooluse']

EXIT_CODE_RE = re.compile(r'(?:Process exited|exited) with code (\d+)')


def _norm_cmd_head(command: str) -> str | None:
    """First two meaningful tokens of a bash command, env-prefix stripped."""
    toks = command.strip().split()
    while toks and ('=' in toks[0] or toks[0] in ('sudo', 'env', 'nohup')):
        toks = toks[1:]
    if not toks:
        return None
    head = ' '.join(toks[:2])
    return head[:60]


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get('text') or item.get('content') or ''))
        return '\n'.join(parts)
    return ''


def scan_claude_session(path: str):
    """One Claude Code session file -> per-session signal counts."""
    s = collections.Counter()
    tool_name_by_id: dict[str, str] = {}
    failed_tools: dict[str, int] = {}  # tool name -> open failure count
    in_failure_run: set[str] = set()  # tools currently in a failure run
    cmd_heads: set[str] = set()
    first_ts = last_ts = None
    for line in open(path, errors='ignore'):
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        t = d.get('type')
        ts = d.get('timestamp')
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        msg = d.get('message') if isinstance(d.get('message'), dict) else None
        if t == 'assistant' and msg:
            s['asst'] += 1
            for item in msg.get('content') or []:
                if not isinstance(item, dict) or item.get('type') != 'tool_use':
                    continue
                name = str(item.get('name', ''))
                tool_name_by_id[str(item.get('id', ''))] = name
                if name == 'Bash':
                    head = _norm_cmd_head(str((item.get('input') or {}).get('command', '')))
                    if head:
                        cmd_heads.add(head)
        elif t == 'user' and msg:
            s['user'] += 1
            content = msg.get('content')
            if isinstance(content, str):
                low = content.lower()
                if len(content) < 500 and any(m in low or m in content for m in CORRECTION_MARKERS):
                    s['d2_correction'] += 1
                continue
            for item in content or []:
                if not isinstance(item, dict) or item.get('type') != 'tool_result':
                    continue
                name = tool_name_by_id.get(str(item.get('tool_use_id', '')), '?')
                if item.get('is_error'):
                    s['tool_error'] += 1
                    text = _text_of(item.get('content')).lower()
                    if any(m in text for m in DENY_MARKERS):
                        s['d4_deny'] += 1
                    elif any(m in text for m in HOOK_MARKERS):
                        s['d5_hook'] += 1
                    else:
                        failed_tools[name] = failed_tools.get(name, 0) + 1
                        if name not in in_failure_run:
                            in_failure_run.add(name)
                            s['d1_clusters'] += 1
                elif failed_tools.get(name):
                    # a success of a previously-failed tool closes one window
                    failed_tools[name] -= 1
                    in_failure_run.discard(name)
                    s['d1_error_fix'] += 1
                else:
                    in_failure_run.discard(name)
    s['duration_events'] = s['user'] + s['asst']
    return s, cmd_heads, first_ts, last_ts


def scan_codex_session(path: str):
    s = collections.Counter()
    cwd = None
    open_failures = 0
    cmd_heads: set[str] = set()
    for line in open(path, errors='ignore'):
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        t = d.get('type')
        p = d.get('payload') or {}
        if t == 'session_meta':
            cwd = p.get('cwd') or (p.get('payload') or {}).get('cwd')
        elif t == 'turn_context':
            cwd = p.get('cwd') or cwd
        elif t == 'response_item':
            pt = p.get('type')
            if pt == 'message':
                role = p.get('role')
                if role == 'user':
                    s['user'] += 1
                    text = _text_of(p.get('content'))
                    low = text.lower()
                    if 0 < len(text) < 500 and any(m in low or m in text for m in CORRECTION_MARKERS):
                        s['d2_correction'] += 1
                elif role == 'assistant':
                    s['asst'] += 1
            elif pt in ('function_call', 'custom_tool_call'):
                s['tool_call'] += 1
                args = p.get('arguments')
                if isinstance(args, str) and '"command"' in args:
                    try:
                        cmd = json.loads(args).get('command')
                        if isinstance(cmd, list):
                            cmd = ' '.join(str(c) for c in cmd)
                        if isinstance(cmd, str):
                            head = _norm_cmd_head(cmd)
                            if head:
                                cmd_heads.add(head)
                    except (json.JSONDecodeError, ValueError):
                        pass
            elif pt in ('function_call_output', 'custom_tool_call_output'):
                out = p.get('output')
                text = out if isinstance(out, str) else _text_of(out)
                m = EXIT_CODE_RE.search(text or '')
                if m and m.group(1) != '0':
                    s['tool_error'] += 1
                    if not open_failures:
                        s['d1_clusters'] += 1
                    open_failures += 1
                elif m and open_failures:
                    open_failures -= 1
                    s['d1_error_fix'] += 1
                elif m:
                    open_failures = 0
    s['duration_events'] = s['user'] + s['asst']
    return s, cmd_heads, cwd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='.run/skill-s1')
    ap.add_argument('--claude-root', default=os.path.expanduser('~/.claude/projects'))
    ap.add_argument('--codex-root', default=os.path.expanduser('~/.codex/sessions'))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    t0 = time.time()
    now = time.time()
    per_project: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    proj_cmd_sessions: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    proj_recency: dict[str, float] = collections.defaultdict(float)
    totals = collections.Counter()
    rows = []

    claude_files = [
        f for f in glob.glob(f'{args.claude_root}/*/*.jsonl')
        if '/memory/' not in f and '/subagents/' not in f
    ]
    for f in sorted(claude_files):
        proj = os.path.basename(os.path.dirname(f))
        try:
            s, heads, _, _ = scan_claude_session(f)
        except OSError:
            continue
        # subagent transcripts belong to the parent session's occasion
        # (KMEM's parser includes them the same way)
        sub_root = os.path.join(os.path.dirname(f), os.path.splitext(os.path.basename(f))[0])
        for sf in sorted(glob.glob(f'{sub_root}/**/*.jsonl', recursive=True)):
            try:
                ss, sheads, _, _ = scan_claude_session(sf)
            except OSError:
                continue
            s.update(ss)
            heads |= sheads
            s['subagent_files'] += 1
            totals['claude_subagent_files'] += 1
        mtime = os.path.getmtime(f)
        proj_recency[proj] = max(proj_recency[proj], mtime)
        per_project[proj].update(s)
        per_project[proj]['sessions'] += 1
        for h in heads:
            proj_cmd_sessions[proj][h] += 1
        totals.update(s)
        totals['claude_sessions'] += 1
        rows.append({'source': 'claude', 'project': proj, 'file': os.path.basename(f),
                     'mtime_days_ago': round((now - mtime) / 86400, 1), **dict(s)})

    codex_files = glob.glob(f'{args.codex_root}/**/*.jsonl', recursive=True)
    for f in sorted(codex_files):
        try:
            s, heads, cwd = scan_codex_session(f)
        except OSError:
            continue
        proj = f'codex:{cwd or "?"}'
        mtime = os.path.getmtime(f)
        proj_recency[proj] = max(proj_recency[proj], mtime)
        per_project[proj].update(s)
        per_project[proj]['sessions'] += 1
        for h in heads:
            proj_cmd_sessions[proj][h] += 1
        totals.update(s)
        totals['codex_sessions'] += 1
        rows.append({'source': 'codex', 'project': proj, 'file': os.path.basename(f),
                     'mtime_days_ago': round((now - mtime) / 86400, 1), **dict(s)})

    # D3: command heads recurring in >=3 sessions of one project
    d3 = {}
    for proj, ctr in proj_cmd_sessions.items():
        rep = {h: n for h, n in ctr.items() if n >= 3}
        if rep:
            d3[proj] = dict(sorted(rep.items(), key=lambda kv: -kv[1])[:20])
            per_project[proj]['d3_repeated_heads'] = len(rep)
            totals['d3_repeated_heads'] += len(rep)

    # window estimate for L3 cost: one window per failure run (tight),
    # correction, deny, hook event, plus one per repeated command head
    totals['l3_window_estimate'] = (
        totals['d1_clusters'] + totals['d2_correction'] + totals['d4_deny']
        + totals['d5_hook'] + totals['d3_repeated_heads']
    )

    active_projects_90d = [p for p, m in proj_recency.items() if (now - m) < 90 * 86400]
    signal_projects_90d = [
        p for p in active_projects_90d
        if per_project[p]['d1_error_fix'] + per_project[p]['d2_correction'] > 0
    ]

    summary = {
        'scanned_at_epoch': int(now),
        'elapsed_s': round(time.time() - t0, 1),
        'totals': dict(totals),
        'active_projects_90d': len(active_projects_90d),
        'signal_projects_90d': len(signal_projects_90d),
        'signal_coverage_90d': round(len(signal_projects_90d) / max(1, len(active_projects_90d)), 2),
        'per_project': {p: dict(c) for p, c in sorted(
            per_project.items(), key=lambda kv: -kv[1]['sessions'])},
        'd3_repeated_command_heads': d3,
    }
    with open(f'{args.out}/summary.json', 'w') as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    with open(f'{args.out}/sessions.jsonl', 'w') as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')

    t = totals
    print(f"claude sessions: {t['claude_sessions']} (+{t['claude_subagent_files']} subagent files)  "
          f"codex sessions: {t['codex_sessions']}")
    print(f"tool errors: {t['tool_error']}  D1 pairs: {t['d1_error_fix']}  "
          f"D1 clusters: {t['d1_clusters']}  "
          f"D2 corrections: {t['d2_correction']}  D4 deny: {t['d4_deny']}  "
          f"D5 hook: {t['d5_hook']}  D3 repeated heads: {t['d3_repeated_heads']}")
    print(f"L3 window estimate (tight): {t['l3_window_estimate']}")
    print(f"active projects (90d): {len(active_projects_90d)}, "
          f"with D1/D2 signal: {len(signal_projects_90d)} "
          f"({summary['signal_coverage_90d'] * 100:.0f}%)")
    print(f"artifacts: {args.out}/summary.json, {args.out}/sessions.jsonl  "
          f"({summary['elapsed_s']}s)")


if __name__ == '__main__':
    main()
