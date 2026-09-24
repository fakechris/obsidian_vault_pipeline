---------------------------- MODULE RunLock ----------------------------
(***************************************************************************)
(* Model of RunLock::acquire_named / reclaim_under_guard / claim_guard /   *)
(* Drop in crates/ovp-intake/src/vaultops.rs.                              *)
(*                                                                         *)
(* One action = one syscall-sized step of the Rust code: create_new,       *)
(* writeln of the PID, read_to_string, `kill -0`, remove_file. Files carry *)
(* an inode number so a write through a handle whose path was since        *)
(* unlinked lands on the orphaned inode, not on the new file at that path. *)
(*                                                                         *)
(* Any running process may crash at any step (SIGKILL / Ctrl-C: no Drop).  *)
(* PIDs are never reused (the code treats reuse as "alive", which only     *)
(* makes it more conservative).                                            *)
(*                                                                         *)
(* Legacy = TRUE  models the code before INV-678: claim_guard reclaimed a  *)
(*                stale guard by remove_file + create_new, which lets two  *)
(*                processes both hold run.lock (TLC: Mutex violated).      *)
(* Legacy = FALSE models the current code: claim_guard refuses a stale     *)
(*                guard, and a process that acquired run.lock through the  *)
(*                ordinary create_new path clears a stale guard it finds.  *)
(*                                                                         *)
(* This is a bug-finding model, not a proof of the Rust code.              *)
(***************************************************************************)
EXTENDS Naturals, FiniteSets

CONSTANTS Procs,            \* concurrent `ovp2` invocations
          Ghost,            \* PID of a process that died before the model starts
          Legacy

Pids   == Procs \cup {Ghost}
Absent == [ino |-> 0, content |-> "absent"]
Empty  == "empty"            \* created by create_new, PID not yet written

VARIABLES lock,     \* .ovp/run.lock          [ino, content]
          guard,    \* .ovp/run.lock.reclaim  [ino, content]
          nextIno,
          alive,    \* pid -> BOOLEAN
          pc,
          seen,     \* PID last read from a file (per process)
          myLock,   \* inode of the run.lock this process created
          myGuard,  \* inode of the guard this process created
          holds     \* process believes it holds run.lock

vars == <<lock, guard, nextIno, alive, pc, seen, myLock, myGuard, holds>>

\* owner_is_dead: unreadable / empty / non-PID content => "alive" (conservative)
Dead(c) == c \in Pids /\ ~alive[c]

Init ==
    /\ lock  \in {Absent, [ino |-> 1, content |-> Ghost]}
    /\ guard \in {Absent, [ino |-> 2, content |-> Ghost]}
    /\ nextIno = 3
    /\ alive = [p \in Pids |-> p # Ghost]
    /\ pc    = [p \in Procs |-> "start"]
    /\ seen  = [p \in Procs |-> "absent"]
    /\ myLock  = [p \in Procs |-> 0]
    /\ myGuard = [p \in Procs |-> 0]
    /\ holds = [p \in Procs |-> FALSE]

Goto(p, l) == pc' = [pc EXCEPT ![p] = l]

ReadInto(p, f, next) ==
    /\ seen' = [seen EXCEPT ![p] = f.content]
    /\ Goto(p, next)
    /\ UNCHANGED <<lock, guard, nextIno, alive, myLock, myGuard, holds>>

\* ---- acquire_named: first try_create -------------------------------------
Start(p) ==
    /\ pc[p] = "start"
    /\ IF lock.content = "absent"
         THEN /\ lock' = [ino |-> nextIno, content |-> Empty]
              /\ myLock' = [myLock EXCEPT ![p] = nextIno]
              /\ nextIno' = nextIno + 1
              /\ holds' = [holds EXCEPT ![p] = TRUE]
              /\ Goto(p, "stamp_then_hold")
         ELSE /\ Goto(p, "read_lock")
              /\ UNCHANGED <<lock, myLock, nextIno, holds>>
    /\ UNCHANGED <<guard, alive, seen, myGuard>>

\* writeln!(f, pid) through the handle from create_new
StampLock(p, next) ==
    /\ lock' = IF lock.ino = myLock[p] THEN [lock EXCEPT !.content = p] ELSE lock
    /\ Goto(p, next)
    /\ UNCHANGED <<guard, nextIno, alive, seen, myLock, myGuard, holds>>

StampThenHold(p) ==
    pc[p] = "stamp_then_hold" /\ StampLock(p, IF Legacy THEN "hold" ELSE "hc_read")

\* ---- clear_stale_guard (current code only): run by a holder of run.lock --
HcRead(p)  == pc[p] = "hc_read" /\ ReadInto(p, guard, "hc_probe")
HcProbe(p) ==
    /\ pc[p] = "hc_probe"
    /\ Goto(p, IF Dead(seen[p]) THEN "hc_remove" ELSE "hold")
    /\ UNCHANGED <<lock, guard, nextIno, alive, seen, myLock, myGuard, holds>>
HcRemove(p) ==
    /\ pc[p] = "hc_remove"
    /\ guard' = Absent
    /\ Goto(p, "hold")
    /\ UNCHANGED <<lock, nextIno, alive, seen, myLock, myGuard, holds>>

\* owner_is_dead(run.lock) before trying the guard
ReadLock(p)  == pc[p] = "read_lock" /\ ReadInto(p, lock, "probe_lock")
ProbeLock(p) ==
    /\ pc[p] = "probe_lock"
    /\ Goto(p, IF Dead(seen[p]) THEN "g_create" ELSE "fail")
    /\ UNCHANGED <<lock, guard, nextIno, alive, seen, myLock, myGuard, holds>>

\* ---- claim_guard -----------------------------------------------------------
CreateGuard(p, onExists) ==
    /\ IF guard.content = "absent"
         THEN /\ guard' = [ino |-> nextIno, content |-> Empty]
              /\ myGuard' = [myGuard EXCEPT ![p] = nextIno]
              /\ nextIno' = nextIno + 1
              /\ Goto(p, "g_stamp")
         ELSE /\ Goto(p, onExists)
              /\ UNCHANGED <<guard, myGuard, nextIno>>
    /\ UNCHANGED <<lock, alive, seen, myLock, holds>>

GCreate(p)  == pc[p] = "g_create"  /\ CreateGuard(p, "g_read")
GCreate2(p) == pc[p] = "g_create2" /\ CreateGuard(p, "fail")

GStamp(p) ==
    /\ pc[p] = "g_stamp"
    /\ guard' = IF guard.ino = myGuard[p] THEN [guard EXCEPT !.content = p] ELSE guard
    /\ Goto(p, "rc_read")
    /\ UNCHANGED <<lock, nextIno, alive, seen, myLock, myGuard, holds>>

GRead(p)  == pc[p] = "g_read" /\ ReadInto(p, guard, "g_probe")
GProbe(p) ==
    /\ pc[p] = "g_probe"
    /\ Goto(p, IF Legacy /\ Dead(seen[p]) THEN "g_remove" ELSE "fail")
    /\ UNCHANGED <<lock, guard, nextIno, alive, seen, myLock, myGuard, holds>>
GRemove(p) ==
    /\ pc[p] = "g_remove"
    /\ guard' = Absent
    /\ Goto(p, "g_create2")
    /\ UNCHANGED <<lock, nextIno, alive, seen, myLock, myGuard, holds>>

\* ---- reclaim_under_guard ---------------------------------------------------
RcRead(p)  == pc[p] = "rc_read" /\ ReadInto(p, lock, "rc_probe")
RcProbe(p) ==
    /\ pc[p] = "rc_probe"
    /\ Goto(p, IF Dead(seen[p]) THEN "rc_remove" ELSE "rc_release_fail")
    /\ UNCHANGED <<lock, guard, nextIno, alive, seen, myLock, myGuard, holds>>
RcRemove(p) ==
    /\ pc[p] = "rc_remove"
    /\ lock' = Absent
    /\ Goto(p, "rc_create")
    /\ UNCHANGED <<guard, nextIno, alive, seen, myLock, myGuard, holds>>
RcCreate(p) ==
    /\ pc[p] = "rc_create"
    /\ IF lock.content = "absent"
         THEN /\ lock' = [ino |-> nextIno, content |-> Empty]
              /\ myLock' = [myLock EXCEPT ![p] = nextIno]
              /\ nextIno' = nextIno + 1
              /\ holds' = [holds EXCEPT ![p] = TRUE]
              /\ Goto(p, "rc_stamp")
         ELSE /\ Goto(p, "rc_release_fail")
              /\ UNCHANGED <<lock, myLock, nextIno, holds>>
    /\ UNCHANGED <<guard, alive, seen, myGuard>>
RcStamp(p) == pc[p] = "rc_stamp" /\ StampLock(p, "rc_release_ok")

\* `let _ = remove_file(&guard)` -- unconditional, by path
ReleaseGuard(p, from, next) ==
    /\ pc[p] = from
    /\ guard' = Absent
    /\ Goto(p, next)
    /\ UNCHANGED <<lock, nextIno, alive, seen, myLock, myGuard, holds>>

\* ---- Drop ------------------------------------------------------------------
\* `let _ = remove_file(&self.path)` -- unconditional, by path
Release(p) ==
    /\ pc[p] = "hold"
    /\ lock' = Absent
    /\ holds' = [holds EXCEPT ![p] = FALSE]
    /\ Goto(p, "done")
    /\ UNCHANGED <<guard, nextIno, alive, seen, myLock, myGuard>>

Crash(p) ==
    /\ pc[p] \notin {"done", "fail", "crashed"}
    /\ alive' = [alive EXCEPT ![p] = FALSE]
    /\ holds' = [holds EXCEPT ![p] = FALSE]
    /\ Goto(p, "crashed")
    /\ UNCHANGED <<lock, guard, nextIno, seen, myLock, myGuard>>

Step(p) ==
    \/ Start(p) \/ StampThenHold(p) \/ ReadLock(p) \/ ProbeLock(p)
    \/ HcRead(p) \/ HcProbe(p) \/ HcRemove(p)
    \/ GCreate(p) \/ GCreate2(p) \/ GStamp(p) \/ GRead(p) \/ GProbe(p) \/ GRemove(p)
    \/ RcRead(p) \/ RcProbe(p) \/ RcRemove(p) \/ RcCreate(p) \/ RcStamp(p)
    \/ ReleaseGuard(p, "rc_release_ok", "hold")
    \/ ReleaseGuard(p, "rc_release_fail", "fail")
    \/ Release(p)
    \/ Crash(p)

Terminated == \A p \in Procs : pc[p] \in {"done", "fail", "crashed"}

Next == (\E p \in Procs : Step(p)) \/ (Terminated /\ UNCHANGED vars)

Spec == Init /\ [][Next]_vars

\* ---- Properties --------------------------------------------------------------
TypeOK ==
    /\ holds \in [Procs -> BOOLEAN]
    /\ alive \in [Pids -> BOOLEAN]

\* The single-writer guarantee RunLock exists to provide.
Mutex == Cardinality({p \in Procs : alive[p] /\ holds[p]}) <= 1

\* A live holder's lock file has not been deleted / replaced under it.
HolderOwnsFile == \A p \in Procs : (alive[p] /\ holds[p]) => lock.ino = myLock[p]

\* At most one live process is inside the reclaim critical section.
InGuard(p) == pc[p] \in {"g_stamp", "rc_read", "rc_probe", "rc_remove",
                         "rc_create", "rc_stamp", "rc_release_ok", "rc_release_fail"}
GuardMutex == Cardinality({p \in Procs : alive[p] /\ InGuard(p)}) <= 1

\* A live process's guard is never deleted by someone else.
GuardOwnsFile == \A p \in Procs : (alive[p] /\ InGuard(p)) => guard.ino = myGuard[p]

\* Sanity (expected to be VIOLATED): the interesting paths are reachable.
NeverReclaims     == \A p \in Procs : pc[p] # "rc_release_ok"
NeverClearsGuard  == \A p \in Procs : pc[p] # "hc_remove"
=============================================================================
