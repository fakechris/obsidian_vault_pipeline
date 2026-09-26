--------------------------- MODULE RegistryWrite ---------------------------
(***************************************************************************)
(* Model of read-modify-write of `.ovp/schedule.json`:                     *)
(*   load_registry / save_registry -> write_json_atomic                    *)
(*     crates/ovp-scheduler/src/lib.rs                                     *)
(*   writers: `schedule init|install|enable|disable` (ovp-cli, under       *)
(*     scheduler.lock) and the portal's handle_schedule_features           *)
(*     (crates/ovp-server/src/lib.rs, historically WITHOUT the lock).      *)
(*                                                                         *)
(* write_json_atomic = std::fs::write(tmp) + rename(tmp, path).            *)
(* std::fs::write opens with O_CREAT|O_TRUNC BY PATH: a second writer      *)
(* using the same fixed tmp name truncates and writes the SAME inode the   *)
(* first writer is still filling. Its body lands in more than one write,   *)
(* modeled as "partial" then complete.                                     *)
(*                                                                         *)
(* A registry is the set of writers whose edit it contains. Each writer    *)
(* loads the current set, adds itself, and publishes.                      *)
(*                                                                         *)
(* PortalLocked = FALSE, UniqueTmp = FALSE : code before INV-686.          *)
(* PortalLocked = TRUE,  UniqueTmp = TRUE  : current code.                 *)
(***************************************************************************)
EXTENDS Naturals, FiniteSets

CONSTANTS Writers, Portal, PortalLocked, UniqueTmp, MaxIno

Inos    == 1..MaxIno
Whole(e) == [torn |-> FALSE, edits |-> e]
Torn     == [torn |-> TRUE, edits |-> {}]

VARIABLES regIno,    \* inode named by schedule.json
          tmpAt,     \* tmp path -> inode (0 = absent)
          data,      \* inode -> [torn: BOOLEAN, edits: SUBSET Writers]
          nextIno,
          lockHolder,
          pc,
          loaded,    \* what each writer read
          myTmp      \* inode each writer opened for its tmp write

vars == <<regIno, tmpAt, data, nextIno, lockHolder, pc, loaded, myTmp>>

None == "none"
TmpPath(w) == IF UniqueTmp THEN w ELSE "shared"
TmpPaths   == IF UniqueTmp THEN Writers ELSE {"shared"}
Locks(w)   == w # Portal \/ PortalLocked

Init ==
    /\ regIno = 1
    /\ data = [i \in Inos |-> Whole({})]
    /\ tmpAt = [t \in TmpPaths |-> 0]
    /\ nextIno = 2
    /\ lockHolder = None
    /\ pc = [w \in Writers |-> "start"]
    /\ loaded = [w \in Writers |-> {}]
    /\ myTmp = [w \in Writers |-> 0]

Goto(w, l) == pc' = [pc EXCEPT ![w] = l]

Start(w) ==
    /\ pc[w] = "start"
    /\ IF Locks(w) THEN lockHolder = None /\ lockHolder' = w ELSE UNCHANGED lockHolder
    /\ Goto(w, "load")
    /\ UNCHANGED <<regIno, tmpAt, data, nextIno, loaded, myTmp>>

\* load_registry. A torn registry fails the parse, and the writer gives up.
Load(w) ==
    /\ pc[w] = "load"
    /\ IF data[regIno].torn
         THEN /\ Goto(w, "release")
              /\ UNCHANGED loaded
         ELSE /\ loaded' = [loaded EXCEPT ![w] = data[regIno].edits]
              /\ Goto(w, "open_tmp")
    /\ UNCHANGED <<regIno, tmpAt, data, nextIno, lockHolder, myTmp>>

\* std::fs::write, step 1: open(tmp, O_CREAT|O_TRUNC) by path.
OpenTmp(w) ==
    /\ pc[w] = "open_tmp"
    /\ nextIno <= MaxIno
    /\ LET t == TmpPath(w)
           ino == IF tmpAt[t] = 0 THEN nextIno ELSE tmpAt[t]
       IN /\ tmpAt' = [tmpAt EXCEPT ![t] = ino]
          /\ nextIno' = IF tmpAt[t] = 0 THEN nextIno + 1 ELSE nextIno
          /\ data' = [data EXCEPT ![ino] = Torn]
          /\ myTmp' = [myTmp EXCEPT ![w] = ino]
    /\ Goto(w, "fill_tmp")
    /\ UNCHANGED <<regIno, lockHolder, loaded>>

\* std::fs::write, step 2: the rest of the body lands through the handle.
FillTmp(w) ==
    /\ pc[w] = "fill_tmp"
    /\ data' = [data EXCEPT ![myTmp[w]] = Whole(loaded[w] \cup {w})]
    /\ Goto(w, "rename")
    /\ UNCHANGED <<regIno, tmpAt, nextIno, lockHolder, loaded, myTmp>>

\* rename(tmp, schedule.json): publishes whatever inode is at the tmp path NOW.
Rename(w) ==
    /\ pc[w] = "rename"
    /\ LET t == TmpPath(w) IN
       IF tmpAt[t] = 0
         THEN /\ Goto(w, "release")               \* ENOENT: someone else renamed it
              /\ UNCHANGED <<regIno, tmpAt>>
         ELSE /\ regIno' = tmpAt[t]
              /\ tmpAt' = [tmpAt EXCEPT ![t] = 0]
              /\ Goto(w, "acked")
    /\ UNCHANGED <<data, nextIno, lockHolder, loaded, myTmp>>

Acked(w) ==
    /\ pc[w] = "acked"
    /\ lockHolder' = IF lockHolder = w THEN None ELSE lockHolder
    /\ Goto(w, "done")
    /\ UNCHANGED <<regIno, tmpAt, data, nextIno, loaded, myTmp>>

Release(w) ==
    /\ pc[w] = "release"
    /\ lockHolder' = IF lockHolder = w THEN None ELSE lockHolder
    /\ Goto(w, "failed")
    /\ UNCHANGED <<regIno, tmpAt, data, nextIno, loaded, myTmp>>

Step(w) == Start(w) \/ Load(w) \/ OpenTmp(w) \/ FillTmp(w) \/ Rename(w) \/ Acked(w) \/ Release(w)

Terminated == \A w \in Writers : pc[w] \in {"done", "failed"}
Next == (\E w \in Writers : Step(w)) \/ (Terminated /\ UNCHANGED vars)
Spec == Init /\ [][Next]_vars

\* ---- Properties --------------------------------------------------------------
\* A tick (or any reader) can always parse the published registry.
RegistryParses == ~data[regIno].torn

\* An edit whose save returned Ok is in the published registry, unless a later
\* acknowledged save replaced it. (Serialized writers each load the previous
\* one's result, so every acked edit survives.)
NoLostUpdate == \A w \in Writers : pc[w] = "done" => w \in data[regIno].edits
                                                   \/ data[regIno].torn

\* Sanity (expected VIOLATED): two writers really do overlap.
NeverTwoInFlight == Cardinality({w \in Writers : pc[w] \in {"open_tmp", "fill_tmp", "rename"}}) <= 1
=============================================================================
