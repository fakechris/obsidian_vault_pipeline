# TLA+ models

Bug-finding models of cross-process protocols. They are **not** proofs of the Rust
code: each model covers only the invariants written in it, and can drift from the
implementation. When you change code named in the table below, update the model in
the same PR and re-run the check.

## Running

```bash
brew install openjdk      # keg-only; the script finds it without touching PATH
scripts/check-tla.sh      # downloads tla2tools v1.7.4 (SHA-256 pinned) on first run
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
