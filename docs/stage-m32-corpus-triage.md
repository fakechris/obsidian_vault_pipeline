# Stage M32 — Full-Corpus Run Coverage & Failure Triage (Exit Criterion #2)

**Closes:** M32 Level-3 exit criterion #2 — "Every `blocked`/failed source **classified**
(transport vs real content defect); real defects fixed or explicitly waived"
(`docs/stage-m32-python-retirement-and-product-definition.md` §3), and the triage half of
IMPLEMENTATION_PLAN Stage 2.

**Written:** 2026-07-09, reconstructed from preserved artifacts of the 2026-07-02 full-corpus
run (`.run/m32-stage123-20260702/`), the 2026-07-07 994-pack rebuild
(`.run/m35-review-rebuild-20260707-994/`), the live vault index
(`~/Documents/ovp-vault/.ovp/index/index.json`, dated 2026-07-09), and the 2026-07-09 daily-run
logs (`.run/p2p3-daily-20260709*.log`). Every number below comes from one of these artifacts;
the reproduction commands are in the Appendix. Where the 07-02 runner did not record something,
this doc says so instead of inferring.

ZH 摘要：2026-07-02 全量跑 1012 个源（全部来自 `50-Inbox/03-Processed`），成功 994（98.2%），
失败 18（1.8%），零 transport 失败；18 个失败逐一分类为管线鲁棒性缺陷（11 JSON 解析）、
provider 异常（3 空回复）、内容缺陷（2 超长 + 2 空体裸书签）。02-Pinboard 归档（计划 Q3 的
~390 源）**没有**进这次跑。needs-content 一类已于 07-09 被 web-fetch/github enrichment
打通（200 条 197 成功）。唯一 blocked 源 84fbf6dc 单列。每个失败类都有 fix 指针或
建议 waive 理由，等 operator 签字。

---

## 1. Coverage — what the 2026-07-02 run attempted and produced

| Metric | Value | Artifact |
|---|---:|---|
| Sources attempted | **1012** | `stage1-reader/meta/sources.txt` (1012 lines) |
| Per-source status records | 1012 | `stage1-reader-v2/status/*.status` (1012 files) |
| Succeeded (status `ok`) | **994** (98.2%) | `stage1-reader-summary/ok-packs.tsv` (994 rows) |
| Failed | **18** (1.8%) | `stage1-reader-summary/failures.tsv` (18 rows + header) |
| Pack directories written | 1012 (= 994 complete + 18 partial/failed) | `stage1-reader-v2/packs/` |
| Resume-run summary | `done=1012 success=994 non_ok=18` | `stage1-reader-v2/summary-resume.txt` |

Cross-checks against later artifacts:

- **2026-07-07 rebuild:** `.run/m35-review-rebuild-20260707-994/packs/` contains exactly
  **994** pack directories — the same success set, re-materialized (this time with
  `cluster_batching` in `warnings.json` showing `split_all_cases` across 7 clusters and
  **0 entries** in `cluster_cap_overflow`, i.e. the Stage 3a batching fix removed the cap-drop
  that the 07-02 crystal run suffered; see §4 note).
- **2026-07-09 live index:** `index.json` (`run_id: daily-2026-07-09`) reports
  `packs: 1070` — exactly **994 (07-02 corpus) + 76 (`processed` daily-lane sources)**.
  The corpus run and the daily lane reconcile with no unexplained packs.

**Scope caveat (recorded, not hidden):** all 1012 attempted sources are under
`50-Inbox/03-Processed/` (grep: 1012/1012); **zero** are from `02-Pinboard` (grep: 0).
The plan (M32 §4, Q3) scoped the full corpus as "~1012 processed + ~390 pinboard archive" —
the pinboard-archive arm was **not part of the 07-02 run**. As of 2026-07-09 that arm is being
consumed through the *daily* lane instead: the P.2 live pinboard sync + intake put 211 sources
in `queued` status (index totals), processed at the daily cap. Disposition in §5.

**What the 07-02 runner did NOT record** (plain statement, per the no-hand-waving rule):

- `scripts/corpus_rerun.sh` was never committed; the exact invocation (concurrency level,
  `OVP_LLM_TIMEOUT_SECS`, retry policy) is **not recoverable from artifacts**. The plan
  specified ~6-way; whether that was used cannot be verified.
- No `results.jsonl` (planned append-only per-source log). What exists instead:
  per-source `status/*.status` files, `failures.tsv` (id, status, category, source, error),
  and `progress.tsv`/`progress-resume.tsv`. These are sufficient to reconstruct
  attempted/ok/failed and per-failure error strings, which is what this doc uses.
- No record of whether failed sources got the planned second retry pass >1h later.
  (All 18 recorded failures are deterministic-content or provider-content errors, not
  transport, so a transport-retry pass would not have changed them — but its execution is
  unrecorded.)

## 2. Failure enumeration and classification (18 sources)

Zero of the 18 failures are transport-level (`error sending request` / timeout class — the
category that dominated the 2026-06 pilot at 12-way concurrency appears **0 times** in
`failures.tsv`). Classification by error category, with every source listed:

### 2a. Pipeline robustness defect — LLM output JSON unparseable (11 sources)

The model returned malformed JSON and the single-shot parser gave up. Not a source-content
problem: the inputs are ordinary articles. Two sub-buckets from `failures.tsv`:

**`unit_json_parse` (5)** — failed at unit extraction, no pack content:

| sha8 | Source |
|---|---|
| 0951c213 | 2026-06-08_The Harness Is The Product |
| 28ae4f4c | 2026-05-04_精读 Cursor "Continually improving our agent harness" |
| 75aac211 | 2026-05-12_Agent Skill 规范、构建与设计模式 |
| 9c015c8e | 2026-06-03_op7418 — 开源个 Skill（小红/绿书配图） |
| b5bb2a36 | 2026-04-24_jundot_omlx |

**`card_json_parse` (6)** — units succeeded, card synthesis JSON failed (`(pack written)`:
partial pack exists with units but incomplete cards):

| sha8 | Source |
|---|---|
| 20d5b1f4 | 2026-05-22_xingpt — Token爆炸到物理瓶颈：存储大牛市十万字报告 (43.6 KB) |
| 2a23b94b | 2026-05-19_Russell3402 — 多智能体协作调查 |
| 2c365286 | 2026-05-07_dotey — 把视频变成图文博客 |
| 3e9e282b | 2026-04-21_yaojingang_geo-citation-lab |
| 99eab439 | 2026-05-18_LMDFinance — HBM国产替代&产业链拆解 |
| ce53cbbc | 2026-06-03_giantcutie666 — 彭博社揭秘：突破5万限额转移资产 |

**Classification: pipeline bug** (parse robustness / no re-ask on malformed JSON), aggravated
at run time by the cassette-pinning defect (a bad response was cached, so in-run retries
replayed the same bad JSON). The cassette fix (`ModelClient::invalidate`, Stage 0.5, commit
`1dd1818c`) is on trunk — these 18 are now *retryable* by deleting/invalidating the cassette
and re-running, which was not possible during the 07-02 run.

### 2b. Provider anomaly — empty response (3 sources, `no_text_content_blocks`)

Provider returned a response with no text content block (`stop_reason=end_turn`):

| sha8 | Source |
|---|---|
| 3036fe8f | The Knowledge Graph Inside MiroShark |
| c9c316f5 | 2026-04-08_Ataraxy-Labs_opensessions |
| d2a4d661 | 2026-06-03_prukalpa — What an Enterprise Context Layer Actually Is |

**Classification: provider/transport-adjacent anomaly** (not a content defect of the source;
not a crash of ours). Same remediation as 2a: retry after cassette invalidation.

### 2c. Content defect — oversize input (2 sources, `context_window`)

Provider rejected with `invalid params, context window exceeds limit (2013)`. Verified against
the actual files — both are ~0.8 MB of markdown:

| sha8 | Source | Size |
|---|---|---:|
| 6e56f7d9 | 2026-04-02_saturndec_waoowaoo | 818,204 B |
| 75b970bb | 2026-05-14_Imbad0202_academic-research-skills | 780,388 B |

**Classification: real content defect (oversize)**. Needs input chunking/truncation policy or
a per-source waiver.

### 2d. Content defect — empty/stub body (2 sources, `zero_units`)

Verified by reading the files:

| sha8 | Source | What it is |
|---|---|---|
| 1e427ebb | 2026-04-19_iamagenius00_hermes-a2a | 634 B GitHub stub; frontmatter says "All enrichment tiers returned empty content" |
| 6528cebb | 2026-02-21_Agent_Orchestrator | 412 B bare bookmark; body is just the URL + "（完整原文见上方）" |

**Classification: content defect — bare bookmark / empty body pre-enrichment.** `0 units
extracted` is the correct, honest behavior on these inputs. This is exactly the
`needs-content` class (see §3).

## 3. The needs-content class now has a remediation path (2026-07-09)

The 2026-07-09 daily runs (`.run/p2p3-daily-20260709.log`, `-run2.log`) exercised the
web-fetch/github enrichment on bare bookmarks at scale:

- Run 1: **200 needs-content URLs → 197 enriched, 3 failed** — the 3 named in the log:
  `github.com/statica-ai/statica` HTTP 404 (dead repo; counted twice, once via web-fetch and
  once via the GitHub API path: `github: 140 repo URL(s), 139 enriched, 1 failed`) and
  `vercel.com/design.md` rejected for `content-type: text/markdown` (our defect — fix noted in
  IMPLEMENTATION_PLAN P.3/B5: web_fetch to accept markdown content-type).
- Run 2: **13 needs-content URLs → 13 enriched, 0 failed**.
- Current index: `needs_content: 16`, `failed: 0`, `unparseable: 0`.

So the §2d class (and any future bare bookmark) is recoverable through the standing daily
lane rather than being a corpus dead-end.

## 4. The known blocked source — its own line

**`84fbf6dc` — "How to Build a Claude Code Slash Command Library (Exact Template Inside)"**
(x.com/0x_rody). Status `blocked` in the live index, `fail_count: 3`, `last_reason:
"grounded extraction/repair failed: provider error api_error: input new_sensitive (1026)"`
(since `daily-2026-06-15`; still reported as blocked in both 07-09 daily logs).
**Classification: provider content-policy refusal** (input flagged "sensitive" by MiniMax).
Not a transport issue, not our pipeline defect. Disposition: retry via
`--retry-blocked` on a different provider, or waive as provider-refused (recommendation: waive;
one X-thread source is not worth a provider swap).

*Related but distinct:* the 07-02 **crystal** run (`stage2-crystal-run.log`) was a capped smoke
run — the cap=16 cluster limit dropped ~866 of 978 clustered cases (agents 335→16, misc 403→16,
etc.). That is a crystallization-coverage issue tracked under exit criterion #3 / Stage 3a
(Phase 1 merged, PR #283; the 07-07 rebuild and the 07-09 `crystal-full-20260709.log` show
`split_all_cases` batching with no cap drops). It is noted here only so nobody mistakes the
crystal cap-drop for a reader-run failure; it is out of scope for criterion #2.

## 5. Waive / fix table (every unresolved class dispositioned)

| # | Class | Count | Disposition | Pointer / rationale | Operator sign-off |
|---|---|---:|---|---|---|
| 1 | unit/card JSON parse (2a) | 11 | **Fix path exists, execute** | Cassette invalidation on trunk (commit `1dd1818c`); action = invalidate + re-run these 11 through `ovp2 read-source` (retryable now, was not on 07-02). If a source still fails twice post-fix, add a JSON-repair/re-ask backlog item. Effort: ~1 h operator wall-clock at 6-way. | [ ] |
| 2 | provider empty response (2b) | 3 | **Fix path exists, execute** (same retry) | Same as #1 — non-deterministic provider anomaly, expected to pass on retry. | [ ] |
| 3 | oversize input (2c) | 2 | **Waive (recommended)** with backlog item | 0.8 MB dumps exceed provider context by design; chunked-ingest is a P1 feature, not a Level-3 blocker. Waiver rationale: 2/1012 (0.2%), both low-value scrape dumps. Backlog: input chunking policy in `read-source`. | [ ] |
| 4 | empty/stub body (2d) | 2 | **Fixed by system evolution** — route through enrichment | §3: web-fetch/github enrichment (197/200 live-verified 2026-07-09) now fills these before reader. Action: re-queue the 2 sources through the daily lane. | [ ] |
| 5 | blocked `84fbf6dc` (provider 1026) | 1 | **Waive (recommended)** | Provider content-policy refusal after 3 attempts; alternative = `--retry-blocked` on another provider. Not a pipeline defect. | [ ] |
| 6 | 02-Pinboard archive not in 07-02 run | ~390 planned / 211 currently queued | **Reclassified, not waived** — consumed via daily lane | Plan Q3 allowed整类处置; operator's P.2 decision (07-09) routes pinboard backlog through `--since`/capped daily runs (211 queued in index, cap 10/run). Criterion #1 ("no unprocessed backlog") tracks this; it is not a *failure* class. | [ ] |
| 7 | transport failures | 0 | — nothing to waive | Pilot's 66 transport failures (12-way) did not recur; 07-02 failures.tsv contains zero transport rows. Note honestly: concurrency actually used on 07-02 is unrecorded (runner script lost). | n/a |

Criterion #2 wording check: every failed/blocked source above is **classified** (transport vs
content defect vs pipeline bug — with zero blanket-blamed on the network); real defects have a
fix pointer or an explicit written waiver rationale. What remains for full closure is operator
sign-off on the recommended waivers (#3, #5) and executing the two cheap retry batches (#1, #2, #4).

## Appendix — reproduction commands (run 2026-07-09)

```bash
R=~/Documents/obsidian-vault-pipeline/.run/m32-stage123-20260702

wc -l $R/stage1-reader/meta/sources.txt                      # 1012 attempted
ls $R/stage1-reader-v2/status | wc -l                        # 1012 status files
ls -d $R/stage1-reader-v2/packs/* | wc -l                    # 1012 pack dirs
wc -l $R/stage1-reader-summary/ok-packs.tsv                  # 994 ok rows (no header)
wc -l $R/stage1-reader-summary/failures.tsv                  # 19 = header + 18 failures
cat  $R/stage1-reader-summary/failure-categories.txt         # card 6 / ctx 2 / no_text 3 / unit 5 / zero 2
cat  $R/stage1-reader-v2/summary-resume.txt                  # done=1012 success=994 non_ok=18
grep -c "03-Processed" $R/stage1-reader/meta/sources.txt     # 1012
grep -c "02-Pinboard"  $R/stage1-reader/meta/sources.txt     # 0

ls -d ~/Documents/obsidian-vault-pipeline/.run/m35-review-rebuild-20260707-994/packs/* | wc -l   # 994

python3 -c "import json; d=json.load(open('$HOME/Documents/ovp-vault/.ovp/index/index.json')); print(d['totals'])"
# packs=1070, processed=76, queued=211, needs_content=16, blocked=1, failed=0  (1070 = 994 + 76)

grep -E "enrich:|github:" ~/Documents/obsidian-vault-pipeline/.run/p2p3-daily-20260709.log
# enrich: 200 needs-content URL(s), 197 enriched, 3 failed · github: 140 repo URL(s), 139 enriched, 1 failed
```

Set-difference check: the 1012 pack dirs minus the 994 `ok-packs.tsv` basenames = exactly the
18 sha8 ids in `failures.tsv` (verified by script on 2026-07-09).
