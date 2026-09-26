# TLA+ models

Bug-finding models of cross-process protocols. They are **not** proofs of the Rust
code: each model covers only the invariants written in it, and can drift from the
implementation. When you change code named in the table below, update the model in
the same PR and re-run the check.

## Running

```bash
brew install openjdk      # keg-only; the script finds it without touching PATH
scripts/check-tla.sh      # downloads tla2tools v1.7.4 (SHA-256 pinned) on first run;
                          # TLC logs and state dirs go to .run/tla/<timestamp>/
```

`models.txt` lists every `(module, config, expectation)`. `ok` means TLC must finish
with no error. Any other word names an invariant that TLC **must** report as violated.
These negative controls prove the model still catches the bug it was built for, and
that the paths a green run claims to cover are actually reachable. Without them, a
green run can be vacuous.

## Models

### `RunLock.tla` — `.ovp/run.lock` single-writer guarantee

| Config | Models | Expect |
|---|---|---|
| `RunLock.cfg` | current code | all of `Mutex`, `HolderOwnsFile`, `GuardMutex`, `GuardOwnsFile` hold (3 processes, exhaustive) |
| `RunLockLegacy.cfg` | code before INV-678 | `Mutex` violated (2 processes, 27 steps) |
| `RunLockSanity*.cfg` | reachability controls | the stale-lock reclaim and the stale-guard clear both happen |

**Bug found (INV-678).** Setup: `run.lock` and `run.lock.reclaim` both hold a dead PID,
which happens when a process died mid-reclaim.
1. Two processes both judge the guard stale.
2. A removes the guard and creates its own.
3. B's `remove_file` then deletes **A's fresh guard**, and B creates its own. Both are
   now inside the reclaim section.
4. The same remove-by-path step repeats on `run.lock`, so B deletes **A's fresh lock**.
   Both processes hold `run.lock`.

The fix has two parts. `claim_guard` never takes over an existing guard. A stale guard
is cleared only by a process that won `run.lock` through the ordinary `create_new`
path, and there is at most one such process.

| Obligation | Code (`crates/ovp-intake/src/vaultops.rs`) | Test |
|---|---|---|
| Existing guard is never taken over | `RunLock::claim_guard` | `run_lock_refuses_to_take_over_a_stranded_reclaim_guard` |
| Holder clears only a dead-owner guard | `RunLock::clear_stale_guard` | `run_lock_holder_clears_a_stale_reclaim_guard` |
| Stale lock deleted only under guard, after re-check | `RunLock::reclaim_under_guard` | `run_lock_reclaims_stale_lock_from_dead_process` |
| Live or unreadable owner is never reclaimed | `RunLock::owner_is_dead` | `run_lock_refuses_live_owner_and_unreadable_pid` |

Out of scope for this model:
- PID reuse. The code treats reuse as "alive", which is the conservative direction.
- A crash between `create_new` and the PID write. That leaves an empty lock file,
  which reads as alive and needs manual deletion. It is a liveness problem, not a
  safety problem.

Replacing the PID files with an OS lock (`File::try_lock`, stable since Rust 1.89, on a
lock file that is never deleted) would remove both limitations and the whole reclaim
protocol. It needs an MSRV bump and Windows CI validation.

### `LedgerAppend.tla` — JSONL ledgers always parse, and acknowledged appends survive

| Config | Models | Expect |
|---|---|---|
| `LedgerAppendPrefix.cfg` / `…PrefixConcurrent.cfg` | current code: one write per record; a torn tail is closed with `"\n"` + a marker line; the reader skips a torn line only if a marker follows it or it is the unterminated final segment; serialized / concurrent appenders; SIGKILL + power loss | `LedgerParses`, `AckedDurable` hold (3 processes, exhaustive) |
| `LedgerAppend.cfg` / `…Concurrent.cfg` | code before INV-684: `writeln!` = two writes | `LedgerParses` violated |
| `LedgerAppendOneWritePower.cfg` | single write alone, under power loss | `LedgerParses` violated, so the torn-tail handling is needed too |
| `LedgerAppendOneWrite.cfg`, `LedgerAppendRepair.cfg` | alternatives considered (repair = truncate the torn tail; rejected because not every appender holds `run.lock`) | ok |
| `LedgerAppendSanity*.cfg` | reachability controls | power loss, a mid-append crash, and two acked appends all happen |

**Bug found (INV-684).** `writeln!(f, "{line}")` on an unbuffered `File` issues two
`write(2)` calls: the record, then `"\n"`. A SIGKILL between them, or a concurrent
appender landing in between, leaves a `}{` or blank line. `read_jsonl` then fails
the whole ledger, and intake, daily and index stop.

| Obligation | Code | Test |
|---|---|---|
| One write per record; a torn tail is closed with `TORN_MARKER`, never glued to | `ovp_domain::jsonl::append_line` (used by `ovp_intake::vaultops::append_jsonl`, `crystal::patch::append_patch_record`); plain-newline copy in `ovp_evolve::ledger::append_entry` | `concurrent_appenders_never_produce_malformed_lines`, `torn_tail_is_skipped_and_never_glued_to` |
| Skip a torn line only with evidence (marker follows, or unterminated tail) | `ovp_domain::jsonl::parse_ledger` with `TornLines::Skip` (`read_jsonl`) | `terminated_truncated_line_without_marker_still_fails`, `corrupt_line_that_is_not_a_prefix_still_fails` |
| Human corrections and the evolution decision record fail loud on any bad line | `TornLines::Fail` (`read_patch_ledger`); strict `ovp_evolve::ledger::read_entries` | `fail_policy_rejects_a_torn_line`, `human_patch_drift_skips_overlay_and_corrupt_ledger_fails_loudly` |

Why not skip every truncated line: codex review pointed out that serde's `Eof`
classification alone does not prove a torn append (`{"candidate_id":` followed by a
newline is also `Eof`). A sync tool truncating an acknowledged record looks the same
too. Hence the marker as evidence, and the loud `Fail` policy for ledgers of human
input.

The model does not represent the `Fail` readers: those ledgers deliberately stop
under a power-loss tear, as before, and the operator deletes the torn line (plus its
marker line). Also out of scope: a power loss that persists garbage or NUL bytes
rather than a prefix. That still fails the read loudly.

Known limitations (codex review, accepted):
- A second power loss that tears the repair write itself can persist the `"\n"` but
  not the marker. The old fragment is then terminated and unmarked, and the ledger
  fails loud, which is the pre-INV-684 behavior. This needs two power losses, the
  second one inside that one write.
- A short write (disk full, file-size limit) is not retried. `write_once` returns an
  error and leaves a torn tail for the next append to mark. A concurrent appender
  landing right after a short write can still glue onto the fragment.

### `SessionLock.tla` — one turn per chat session

| Config | Models | Expect |
|---|---|---|
| `SessionLockFlock.cfg` | current code: `File::try_lock` on a never-deleted `<session>.lock` | `Mutex` holds (3 processes, exhaustive) |
| `SessionLock.cfg` | code before INV-685: PID file, stale lock reclaimed by renaming it to a per-process grave | `Mutex` violated (2 processes) |
| `SessionLockVerify.cfg` | rejected patch: re-read the grave and link it back if it turns out live | `Mutex` violated (needs 3 processes; passes with 2) |
| `SessionLockSanity*.cfg` | reachability controls | two contenders reach the rename, a stale lock is reclaimed, the flock is retaken after a crash |

**Bug found (INV-685).** `rename(lock, grave)` moves whatever file is at the path
*now*. A and B both judge the same dead PID stale. A renames the stale lock away and
creates a fresh one. B's rename then moves **A's fresh lock** into B's grave, and B
creates its own. Both hold the session. The code comment "rename() arbitrates,
exactly one mover wins" is true only of a single rename, not of the check that
came before it.

| Obligation | Code (`crates/ovp-memory/src/agent_transcript.rs`) | Test |
|---|---|---|
| Another process's live holder excludes us; its death frees the lock with no reclaim step | `SessionStore::lock` (`File::try_lock`) | `session_lock_excludes_another_process_until_it_dies` (re-runs the test binary as the holder, then kills it) |
| Two stores in one process serialize | `held_in_process` | `same_process_second_store_is_busy_and_released_on_drop` |
| The lock file is never deleted; a leftover file is not a held lock | `SessionLock::drop` | `leftover_lock_file_is_not_busy` |

The model does not cover filesystems where `try_lock` is unsupported (it returns an
error and the turn fails loudly), or a mix of old and new binaries during an upgrade.
In that mix, the old binary treats the never-deleted file as a stale PID lock and
reclaims it by rename, so the two versions do not exclude each other. Upgrade the
sidecar and the desktop app together.

### `RegistryWrite.tla` — `.ovp/schedule.json` is never torn and no edit is lost

| Config | Models | Expect |
|---|---|---|
| `RegistryWrite.cfg` | current code: the portal takes `scheduler.lock` like every CLI writer; per-call tmp names | `RegistryParses`, `NoLostUpdate` hold (3 writers) |
| `RegistryWriteLegacyTorn.cfg` | before INV-686: unlocked portal, fixed `schedule.json.tmp` | `RegistryParses` violated |
| `RegistryWriteUniqueTmpOnly.cfg` | unique tmp names but an unlocked portal | `NoLostUpdate` violated, so the lock is needed too |

**Bug found (INV-686, item 1).** `std::fs::write` opens with `O_TRUNC` by path. A
second writer using the same fixed tmp name truncates the inode the first writer
is still filling, and the first writer's `rename` then publishes a half-written
registry. The registry fails validation at load time, so **every** tick stops.

| Obligation | Code | Test |
|---|---|---|
| Per-call tmp name | `ovp_scheduler::write_json_atomic` | `concurrent_saves_never_publish_a_torn_registry` (fails 3/3 with the fixed name) |
| Portal edit under `scheduler.lock`; busy → 409, not a hang | `ovp_server::handle_schedule_features` | `schedule_features_takes_the_scheduler_lock` |

### `SourceWorkQueue.tla` — no article runs twice, no enqueue is lost

| Config | Models | Expect |
|---|---|---|
| `SourceWorkQueue.cfg` | current code: `open`/`snapshot` only read; recovery happens in `claim_next` under the write lock; the skip-mark is locked and reloads | `NoDoubleExecution`, `NoLostEnqueue` hold |
| `SourceWorkQueueLegacyDouble.cfg` | before INV-686 | `NoDoubleExecution` violated |
| `SourceWorkQueueLegacyLost.cfg` | before INV-686 | `NoLostEnqueue` violated |

**Bug found (INV-686, item 2).** The worker runs article x. Then:
1. `ovp2 source-work backfill` (or a second portal) calls `open()`, reads x as
   `running`, and requeues it in memory.
2. The worker finishes x.
3. `open()` persists its stale copy, unlocked, so x is back to `queued`.
4. The worker claims x again, and the article's LLM work runs twice.

The worker's unlocked skip-mark could likewise persist a stale copy over another
process's enqueue.

The fix: readers never write. Recovery runs in `claim_next`, under the lock, and
only for claims that are not this process's own (`QueueItem::claim_token`, unique
per process run). Such a claim is recovered when this process is the elected
worker, or when the claimer's PID (`claimed_by`) is verifiably dead. The worker
condition is what survives PID reuse after a reboot (CodeRabbit on #506). For
that to hold, the worker election lock itself (`WORKER_LOCK`) is an
`ovp_intake::OsLock` (`File::try_lock`), not a PID file. A PID-file election
would refuse forever after a reboot reused the dead worker's PID (codex on #506).
This replaces the 12-minute timeout, which could requeue a live long-running item.
Because recovery covers only dead claimers, the worker itself must never leave a
claim `running`. codex review found three paths that could (a failed terminal
write, an early `continue`, a panic outside the per-task `catch_unwind`); all three
now reach a retried terminal write.
A live claimer keeps the one-article-at-a-time gate closed.

| Obligation | Code (`crates/ovp-memory/src/source_work_queue.rs`) | Test |
|---|---|---|
| Opening the queue elsewhere never touches a live item | `SourceWorkQueue::open`, `snapshot` | `opening_the_queue_elsewhere_never_requeues_a_live_item` |
| Recover only claims that are not our own (elected worker, or a dead claimer PID), keeping the retry budget | `claim_next` + `recover_interrupted`, `process_claim_token` | `abandoned_running_recovery_preserves_attempts_and_not_before`, `elected_worker_recovers_a_claim_whose_pid_was_reused`, `a_live_claimer_keeps_its_item_even_for_a_new_worker`, `claim_requeues_*`, `claim_promotes_*` |
| Worker-side writes are locked and reload first | `mark_task_skipped_if_not_wanted`, `fail_still_running` | `skip_mark_keeps_a_concurrent_enqueue` |
| A live worker never leaves its own claim `running` (recovery covers only dead claimers). Every exit path, early returns and panics included, retries the terminal write before the next claim | `ovp_server::source_work_queue_worker` / `run_source_work_item`, `fail_still_running` → `Result` | `fail_still_running_reports_a_failed_write_and_succeeds_on_retry` |
| A reader opened mid-run keeps reloading | `maybe_reload_from_disk` | `a_reader_opened_mid_run_keeps_seeing_updates` |

INV-686 item 3 (the daily heartbeat) was not modeled. The run lock is now held by
`ovp-cli daily::run` until after the heartbeat's terminal write.
