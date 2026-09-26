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
| Human corrections, the crystal store's `StoreEvent` ledger (review decisions) and the evolution decision record fail loud on any bad line | `TornLines::Fail` (`read_patch_ledger`, `ovp_intake::read_jsonl_strict` for every `StoreEvent` reader); strict `ovp_evolve::ledger::read_entries` | `fail_policy_rejects_a_torn_line`, `strict_reader_rejects_a_marked_torn_line_that_the_default_reader_skips`, `human_patch_drift_skips_overlay_and_corrupt_ledger_fails_loudly` |

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
