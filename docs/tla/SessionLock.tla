--------------------------- MODULE SessionLock ---------------------------
(***************************************************************************)
(* Model of SessionStore::lock / SessionLock::drop in                      *)
(* crates/ovp-memory/src/agent_transcript.rs (the per-chat-session pid     *)
(* lock `<session>.lock` shared by the desktop in-process portal, the      *)
(* ovp-mcp process and CLI agent turns).                                   *)
(*                                                                         *)
(* Unlike RunLock, stale reclaim does not remove-then-create; it renames   *)
(* the lock to a per-process grave (`<session>.stale-<pid>`) on the theory *)
(* that "rename() arbitrates -- exactly one mover wins". The question this *)
(* model asks: does rename-by-PATH arbitrate between contenders that       *)
(* judged the SAME stale owner dead at different times?                    *)
(*                                                                         *)
(* One action = one syscall-sized step: create_new, write!(pid) through    *)
(* the handle, read_to_string, `kill -0`, rename, remove_file. Paths and   *)
(* inodes are separate: `lockAt` / `graveAt[p]` name an inode (0 = no      *)
(* entry), `data[i]` is that inode's content, so a write through a handle  *)
(* lands on its inode no matter which path now names it.                   *)
(*                                                                         *)
(* Any process may crash at any step (no Drop). PIDs are never reused.     *)
(*                                                                         *)
(* Mode = "grave"  -- the code as written.                                 *)
(* Mode = "flock"  -- recommended fix: File::try_lock (flock/LockFileEx)  *)
(*                    on a lock file that is never deleted. The kernel     *)
(*                    drops the lock when the holder dies, so there is no  *)
(*                    stale-owner reclaim step to race.                    *)
(* Mode = "verify" -- candidate patch: after winning the rename, re-read   *)
(*                    the grave; if it no longer holds the PID we judged   *)
(*                    dead, we stole a LIVE lock: put it back with a       *)
(*                    no-replace link(grave, lock) and report busy.        *)
(***************************************************************************)
EXTENDS Naturals, FiniteSets

CONSTANTS Procs, Ghost, Mode, MaxIno

Pids  == Procs \cup {Ghost}
Empty == "empty"                \* created by create_new, pid not yet written
Inos  == 1..MaxIno

VARIABLES lockAt,   \* inode named by <session>.lock (0 = absent)
          graveAt,  \* p -> inode named by <session>.stale-<p> (0 = absent)
          data,     \* inode -> content (a pid or Empty)
          nextIno,
          alive, pc,
          attempt,  \* loop counter of `for attempt in 0..2`
          seen,     \* pid read by read_to_string (or Empty/"none")
          mine,     \* inode this process created via create_new
          holds

vars == <<lockAt, graveAt, data, nextIno, alive, pc, attempt, seen, mine, holds>>

Dead(c) == c \in Pids /\ ~alive[c]

Init ==
    /\ nextIno = 2
    /\ data = [i \in Inos |-> IF i = 1 THEN Ghost ELSE Empty]
    /\ lockAt \in {0, 1}               \* stale lock of a crashed turn, or none
    /\ graveAt = [p \in Procs |-> 0]
    /\ alive = [q \in Pids |-> q # Ghost]
    /\ pc = [p \in Procs |-> "create"]
    /\ attempt = [p \in Procs |-> 0]
    /\ seen = [p \in Procs |-> "none"]
    /\ mine = [p \in Procs |-> 0]
    /\ holds = [p \in Procs |-> FALSE]

Goto(p, l) == pc' = [pc EXCEPT ![p] = l]

\* OpenOptions::new().write(true).create_new(true).open(lock_path)
FlockTry(p) ==
    /\ pc[p] = "create"
    /\ IF \E q \in Procs : alive[q] /\ holds[q]
         THEN Goto(p, "busy") /\ UNCHANGED holds
         ELSE /\ holds' = [holds EXCEPT ![p] = TRUE]
              /\ Goto(p, "hold")
    /\ UNCHANGED <<lockAt, graveAt, data, nextIno, alive, attempt, seen, mine>>

FlockRelease(p) ==
    /\ pc[p] = "hold"
    /\ holds' = [holds EXCEPT ![p] = FALSE]
    /\ Goto(p, "done")
    /\ UNCHANGED <<lockAt, graveAt, data, nextIno, alive, attempt, seen, mine>>

Create(p) ==
    /\ pc[p] = "create"
    /\ IF lockAt = 0
         THEN /\ nextIno <= MaxIno
              /\ lockAt' = nextIno
              /\ mine' = [mine EXCEPT ![p] = nextIno]
              /\ nextIno' = nextIno + 1
              /\ Goto(p, "stamp")
         ELSE /\ Goto(p, "read")
              /\ UNCHANGED <<lockAt, mine, nextIno>>
    /\ UNCHANGED <<graveAt, data, alive, attempt, seen, holds>>

\* write!(f, "{pid}") + sync_data through the create_new handle
Stamp(p) ==
    /\ pc[p] = "stamp"
    /\ data' = [data EXCEPT ![mine[p]] = p]
    /\ holds' = [holds EXCEPT ![p] = TRUE]          \* lock() returns Ok(SessionLock)
    /\ Goto(p, "hold")
    /\ UNCHANGED <<lockAt, graveAt, nextIno, alive, attempt, seen, mine>>

\* fs::read_to_string(lock_path) ... parse::<u32>()
Read(p) ==
    /\ pc[p] = "read"
    /\ seen' = [seen EXCEPT ![p] = IF lockAt = 0 THEN "none" ELSE data[lockAt]]
    /\ Goto(p, "probe")
    /\ UNCHANGED <<lockAt, graveAt, data, nextIno, alive, attempt, mine, holds>>

\* missing/empty => busy; pid_alive => busy; attempt 1 => busy; else rename
Probe(p) ==
    /\ pc[p] = "probe"
    /\ Goto(p, IF Dead(seen[p]) /\ attempt[p] = 0 THEN "rename" ELSE "busy")
    /\ UNCHANGED <<lockAt, graveAt, data, nextIno, alive, attempt, seen, mine, holds>>

\* fs::rename(lock_path, grave) -- moves WHATEVER inode the path names now
Rename(p) ==
    /\ pc[p] = "rename"
    /\ IF lockAt = 0
         THEN /\ Goto(p, "busy")                        \* NotFound: lost the race
              /\ UNCHANGED <<lockAt, graveAt>>
         ELSE /\ graveAt' = [graveAt EXCEPT ![p] = lockAt]
              /\ lockAt' = 0
              /\ Goto(p, IF Mode = "verify" THEN "verify" ELSE "rm_grave")
    /\ UNCHANGED <<data, nextIno, alive, attempt, seen, mine, holds>>

\* ---- candidate patch only ---------------------------------------------------
Verify(p) ==
    /\ pc[p] = "verify"
    /\ Goto(p, IF data[graveAt[p]] = seen[p] THEN "rm_grave" ELSE "restore")
    /\ UNCHANGED <<lockAt, graveAt, data, nextIno, alive, attempt, seen, mine, holds>>

\* link(grave, lock_path) (fails if lock_path exists), then unlink(grave)
Restore(p) ==
    /\ pc[p] = "restore"
    /\ lockAt' = IF lockAt = 0 THEN graveAt[p] ELSE lockAt
    /\ graveAt' = [graveAt EXCEPT ![p] = 0]
    /\ Goto(p, "busy")
    /\ UNCHANGED <<data, nextIno, alive, attempt, seen, mine, holds>>

\* let _ = fs::remove_file(&grave); continue (attempt 1)
RmGrave(p) ==
    /\ pc[p] = "rm_grave"
    /\ graveAt' = [graveAt EXCEPT ![p] = 0]
    /\ attempt' = [attempt EXCEPT ![p] = 1]
    /\ Goto(p, "create")
    /\ UNCHANGED <<lockAt, data, nextIno, alive, seen, mine, holds>>

\* Drop: let _ = fs::remove_file(&self.path) -- by path
Release(p) ==
    /\ pc[p] = "hold"
    /\ lockAt' = 0
    /\ holds' = [holds EXCEPT ![p] = FALSE]
    /\ Goto(p, "done")
    /\ UNCHANGED <<graveAt, data, nextIno, alive, attempt, seen, mine>>

Crash(p) ==
    /\ pc[p] \notin {"done", "busy", "crashed"}
    /\ alive' = [alive EXCEPT ![p] = FALSE]
    /\ holds' = [holds EXCEPT ![p] = FALSE]
    /\ Goto(p, "crashed")
    /\ UNCHANGED <<lockAt, graveAt, data, nextIno, attempt, seen, mine>>

Step(p) ==
  IF Mode = "flock" THEN FlockTry(p) \/ FlockRelease(p) \/ Crash(p) ELSE
    \/ Create(p) \/ Stamp(p) \/ Read(p) \/ Probe(p) \/ Rename(p)
    \/ Verify(p) \/ Restore(p) \/ RmGrave(p) \/ Release(p) \/ Crash(p)

Terminated == \A p \in Procs : pc[p] \in {"done", "busy", "crashed"}
Next == (\E p \in Procs : Step(p)) \/ (Terminated /\ UNCHANGED vars)
Spec == Init /\ [][Next]_vars

\* ---- Properties ----------------------------------------------------------------
TypeOK ==
    /\ lockAt \in 0..MaxIno
    /\ holds \in [Procs -> BOOLEAN]

\* The one guarantee the lock exists for: one live turn per session.
Mutex == Cardinality({p \in Procs : alive[p] /\ holds[p]}) <= 1

\* A live holder's lock inode is still what <session>.lock names.
HolderOwnsFile == \A p \in Procs : (alive[p] /\ holds[p]) => lockAt = mine[p]

\* ---- Reachability sanity checks (each SHOULD be violated) ----------------------
\* Two processes both judge the SAME stale owner dead (the race window opens).
NeverTwoRenamers == Cardinality({p \in Procs : pc[p] = "rename"}) <= 1
\* A stale lock is actually reclaimed and held by someone.
NeverReclaims  == ~\E p \in Procs : attempt[p] = 1 /\ holds[p]
\* flock mode: the lock is taken again after a holder crashed.
NeverRetakenAfterCrash == ~\E p, q \in Procs : pc[p] = "crashed" /\ holds[q]
=============================================================================
