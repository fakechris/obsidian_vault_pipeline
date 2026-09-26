-------------------------- MODULE SourceWorkQueue --------------------------
(***************************************************************************)
(* Model of `.ovp/source-work-queue.json` shared by the elected worker     *)
(* portal and any other process that opens the queue (a second portal,    *)
(* `ovp2 source-work backfill`), crates/ovp-memory/src/source_work_queue.rs.*)
(*                                                                         *)
(* Every process keeps an in-memory copy and persists the WHOLE file       *)
(* (unique tmp + rename, so each persist is atomic). Locked operations     *)
(* (enqueue, claim_next, finish_task) take QUEUE_WRITE_LOCK and reload     *)
(* from disk first. The question is what the UNLOCKED writers do.          *)
(*                                                                         *)
(* Legacy = TRUE (before INV-686):                                         *)
(*   - open() runs restart recovery (running -> queued) and persists,      *)
(*     unlocked, even while another process's worker runs that item;      *)
(*   - snapshot() requeues an item "running" for 12 minutes (time is       *)
(*     nondeterministic here) and persists, unlocked;                      *)
(*   - the worker's mark_task_skipped_if_not_wanted persists its possibly  *)
(*     stale in-memory copy, unlocked.                                     *)
(* Legacy = FALSE (current): open/snapshot only read; recovery happens in  *)
(*   claim_next (worker only, locked); the skip mark is locked + reloads.  *)
(*                                                                         *)
(* Items: x is queued at start, y is enqueued by the other process.        *)
(***************************************************************************)
EXTENDS Naturals

CONSTANTS Legacy

Items  == {"x", "y"}
Absent == "absent"
Worker == "w"
Other  == "o"
Procs  == {Worker, Other}
None   == "none"

VARIABLES file,       \* item -> status on disk
          mem,        \* proc -> item -> status (in-memory copy)
          lock,       \* QUEUE_WRITE_LOCK holder
          wpc, opc,   \* program counters
          running,    \* item the worker is executing (None if idle)
          execs,      \* item -> times the worker STARTED executing it
          yAcked      \* the other process's enqueue of y returned Ok

vars == <<file, mem, lock, wpc, opc, running, execs, yAcked>>

Init ==
    /\ file = [i \in Items |-> IF i = "x" THEN "queued" ELSE Absent]
    /\ mem = [p \in Procs |-> file]
    /\ lock = None
    /\ wpc = "claim"
    /\ opc = "open"
    /\ running = None
    /\ execs = [i \in Items |-> 0]
    /\ yAcked = FALSE

Requeue(f) == [i \in Items |-> IF f[i] = "running" THEN "queued" ELSE f[i]]

\* ---- worker (the elected portal) ------------------------------------------
\* claim_next: locked, reload, (current code: recover abandoned running items),
\* take the first queued item if nothing is running.
Claim ==
    /\ wpc = "claim" /\ lock = None
    /\ LET f0 == IF Legacy THEN file ELSE Requeue(file)
           q  == {i \in Items : f0[i] = "queued"}
       IN
       IF \E i \in Items : f0[i] = "running"
         THEN /\ mem' = [mem EXCEPT ![Worker] = f0]
              /\ UNCHANGED <<file, running, execs, wpc>>
         ELSE IF q = {}
           THEN /\ mem' = [mem EXCEPT ![Worker] = f0]
                /\ UNCHANGED <<file, running, execs, wpc>>
           ELSE LET i == CHOOSE j \in q : TRUE IN
                /\ file' = [f0 EXCEPT ![i] = "running"]
                /\ mem' = [mem EXCEPT ![Worker] = [f0 EXCEPT ![i] = "running"]]
                /\ running' = i
                /\ execs' = [execs EXCEPT ![i] = @ + 1]
                /\ wpc' = "mark"
    /\ UNCHANGED <<lock, opc, yAcked>>

\* mark_task_skipped_if_not_wanted, right after the claim.
Mark ==
    /\ wpc = "mark"
    /\ IF Legacy
         THEN /\ file' = mem[Worker]                      \* unlocked, stale copy
              /\ UNCHANGED mem
         ELSE /\ lock = None                              \* locked + reload
              /\ file' = file
              /\ mem' = [mem EXCEPT ![Worker] = file]
    /\ wpc' = "finish"
    /\ UNCHANGED <<lock, opc, running, execs, yAcked>>

\* finish_task: locked, reload, mark done.
Finish ==
    /\ wpc = "finish" /\ lock = None
    /\ file' = [file EXCEPT ![running] = "done"]
    /\ mem' = [mem EXCEPT ![Worker] = [file EXCEPT ![running] = "done"]]
    /\ running' = None
    /\ wpc' = "claim"
    /\ UNCHANGED <<lock, opc, execs, yAcked>>

\* ---- the other process ------------------------------------------------------
\* open(): read (restart recovery applied to the in-memory copy) ...
Open ==
    /\ opc = "open"
    /\ mem' = [mem EXCEPT ![Other] = IF Legacy THEN Requeue(file) ELSE file]
    /\ opc' = IF Legacy /\ \E i \in Items : file[i] = "running"
               THEN "open_persist" ELSE "enqueue"
    /\ UNCHANGED <<file, lock, wpc, running, execs, yAcked>>

\* ... then, legacy only, persist that copy: a separate, UNLOCKED step.
OpenPersist ==
    /\ opc = "open_persist"
    /\ file' = mem[Other]
    /\ opc' = "enqueue"
    /\ UNCHANGED <<mem, lock, wpc, running, execs, yAcked>>

Enqueue ==
    /\ opc = "enqueue" /\ lock = None
    /\ file' = [file EXCEPT !["y"] = "queued"]
    /\ mem' = [mem EXCEPT ![Other] = [file EXCEPT !["y"] = "queued"]]
    /\ yAcked' = TRUE
    /\ opc' = "snapshot"
    /\ UNCHANGED <<lock, wpc, running, execs>>

\* Portal GET polling: reload, and (legacy) the 12-minute stale rule may fire
\* on a running item at any time ...
Snapshot ==
    /\ opc = "snapshot"
    /\ IF Legacy /\ \E i \in Items : file[i] = "running"
         THEN \/ /\ mem' = [mem EXCEPT ![Other] = Requeue(file)]
                 /\ opc' = "snapshot_persist"
              \/ /\ mem' = [mem EXCEPT ![Other] = file]
                 /\ opc' = "idle"
         ELSE /\ mem' = [mem EXCEPT ![Other] = file]
              /\ opc' = "idle"
    /\ UNCHANGED <<file, lock, wpc, running, execs, yAcked>>

\* ... and persists the requeued copy: a separate, UNLOCKED step.
SnapshotPersist ==
    /\ opc = "snapshot_persist"
    /\ file' = mem[Other]
    /\ opc' = "idle"
    /\ UNCHANGED <<mem, lock, wpc, running, execs, yAcked>>

\* Locked operations are modeled as single atomic steps (take lock, reload,
\* mutate, persist, release), so `lock` never stays held between steps.
Next == Claim \/ Mark \/ Finish \/ Open \/ OpenPersist \/ Enqueue \/ Snapshot \/ SnapshotPersist
        \/ (wpc = "claim" /\ opc = "idle" /\ UNCHANGED vars)

Spec == Init /\ [][Next]_vars

\* ---- Properties --------------------------------------------------------------
\* No article is executed twice while its first run is live (no crashes are
\* modeled, so every re-execution is a spurious one).
NoDoubleExecution == \A i \in Items : execs[i] <= 1

\* An acknowledged enqueue is never erased from the durable queue.
NoLostEnqueue == yAcked => file["y"] # Absent

\* Sanity (expected VIOLATED): both items do get executed.
NeverBothExecuted == ~(execs["x"] = 1 /\ execs["y"] = 1)
=============================================================================
