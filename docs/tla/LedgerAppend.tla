--------------------------- MODULE LedgerAppend ---------------------------
(***************************************************************************)
(* Model of the JSONL ledger append / read pair:                           *)
(*   append_jsonl  crates/ovp-intake/src/vaultops.rs:74-98                 *)
(*   read_jsonl    crates/ovp-intake/src/vaultops.rs:133-150               *)
(* (same shape: append_patch_record crates/ovp-domain/src/crystal/         *)
(*  patch.rs:770-800, ovp-evolve ledger::append_entry).                    *)
(*                                                                         *)
(* `writeln!(f, "{line}")` on an unbuffered File is TWO write(2) calls --  *)
(* the formatted record, then "\n" (std::fmt writes each piece through     *)
(* write_all). O_APPEND makes each call land at EOF atomically, but not    *)
(* the pair. Empirically (3 procs x 20k appends via writeln! + O_APPEND on *)
(* APFS): 7146 lines contained `}{` and 7539 blank lines.                  *)
(*                                                                         *)
(* read_jsonl treats ANY malformed line as a hard error for the whole      *)
(* ledger (intake / daily / index all stop -- see CLAUDE.md).              *)
(*                                                                         *)
(* The file is a sequence of tokens: a record body, "NL", or "torn" (a     *)
(* partially persisted body after power loss). A line = the tokens between *)
(* NLs; it parses iff it is empty or exactly one record body.              *)
(*                                                                         *)
(* Mode = "two_writes"  -- the code as written.                            *)
(* Mode = "one_write"   -- fix A: build line+"\n" and write_all it once.   *)
(* Mode = "one_write_repair" -- fix A + B: before appending (under the run *)
(*        lock), truncate an unterminated tail back to the last "\n";      *)
(*        the reader ignores an unterminated final line (never acked).     *)
(* Mode = "one_write_prefix" -- fix A + B' (the implemented fix): the    *)
(*        appender reads the last byte and, if it is not "\n", prefixes   *)
(*        its record with "\n" + a marker line (no truncation, so no lock *)
(*        needed). The reader skips a torn line ONLY when it is the final  *)
(*        unterminated segment or is directly followed by a marker line;   *)
(*        any other truncated line still fails (it may be real corruption).*)
(* Serial = TRUE  -- appenders are serialized (run.lock works as intended; *)
(*                   a crashed holder's lock is reclaimed).                *)
(* Serial = FALSE -- concurrent appenders (RunLock double-hold race, or    *)
(*                   writers that take no lock at all).                    *)
(* PowerLoss = TRUE adds a machine crash: unsynced data may be lost and    *)
(* the last unsynced record may be partially persisted.                    *)
(***************************************************************************)
EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS Procs, Mode, Serial, PowerLoss

NL   == "NL"
Torn == "torn"
Mark == "mark"   \* {"ovp_jsonl":"torn-line-above"}

VARIABLES file, durable, pc, lockHolder, acked, powerLost, needNL
vars == <<file, durable, pc, lockHolder, acked, powerLost, needNL>>

None == "none"

Init ==
    /\ file = <<>>
    /\ durable = 0
    /\ pc = [p \in Procs |-> "idle"]
    /\ lockHolder = None
    /\ acked = {}
    /\ powerLost = FALSE
    /\ needNL = [p \in Procs |-> FALSE]

Goto(p, l) == pc' = [pc EXCEPT ![p] = l]

\* ---- parsing -----------------------------------------------------------------
\* Positions of NL tokens.
NLs(f) == {i \in 1..Len(f) : f[i] = NL}
\* Line k = tokens strictly between consecutive NLs (0 and Len+1 as sentinels).
Bounds(f) == {0} \cup NLs(f)
NextNL(f, i) == LET later == {j \in NLs(f) : j > i}
                IN IF later = {} THEN Len(f) + 1
                   ELSE CHOOSE j \in later : \A k \in later : j <= k
LineAfter(f, i) == SubSeq(f, i + 1, NextNL(f, i) - 1)
Terminated(f, i) == NextNL(f, i) <= Len(f)
OkLine(l) == Len(l) = 0 \/ (Len(l) = 1 /\ l[1] \in Procs)

\* read_jsonl: every line, including an unterminated final one, must parse.
StrictParses(f) == \A i \in Bounds(f) : OkLine(LineAfter(f, i))
\* fix B reader: an unterminated final line is a torn, never-acked write.
TolerantParses(f) == \A i \in Bounds(f) : Terminated(f, i) => OkLine(LineAfter(f, i))
\* fix B' reader: a line that is a torn record fragment is skipped anywhere.
PrefixTolerantParses(f) == \A i \in Bounds(f) :
    LET l == LineAfter(f, i) IN
        \/ OkLine(l)
        \/ l = <<Mark>>
        \/ /\ l = <<Torn>>
           /\ \/ ~Terminated(f, i)
              \/ LineAfter(f, NextNL(f, i)) = <<Mark>>
Parses(f) == CASE Mode = "one_write_repair" -> TolerantParses(f)
               [] Mode = "one_write_prefix" -> PrefixTolerantParses(f)
               [] OTHER                     -> StrictParses(f)

\* Records that appear as a whole, terminated line.
GoodRecs(f) == {f[i] : i \in {j \in 1..Len(f) : f[j] \in Procs
                                /\ (j = 1 \/ f[j-1] = NL)
                                /\ j < Len(f) /\ f[j+1] = NL}}

\* ---- appender ----------------------------------------------------------------
Start(p) ==
    /\ pc[p] = "idle"
    /\ IF Serial THEN lockHolder = None /\ lockHolder' = p ELSE UNCHANGED lockHolder
    /\ Goto(p, CASE Mode = "one_write_repair" -> "repair"
                 [] Mode = "one_write_prefix" -> "check"
                 [] OTHER                     -> "write")
    /\ UNCHANGED <<file, durable, acked, powerLost, needNL>>

\* fix B': read the last byte (a separate syscall from the write)
Check(p) ==
    /\ pc[p] = "check"
    /\ needNL' = [needNL EXCEPT ![p] = Len(file) > 0 /\ file[Len(file)] # NL]
    /\ Goto(p, "write")
    /\ UNCHANGED <<file, durable, lockHolder, acked, powerLost>>

LastNL(f) == IF NLs(f) = {} THEN 0 ELSE CHOOSE j \in NLs(f) : \A k \in NLs(f) : k <= j

\* fix B: set_len(offset just past the last '\n') when the file does not end in '\n'
Repair(p) ==
    /\ pc[p] = "repair"
    /\ file' = SubSeq(file, 1, LastNL(file))
    /\ durable' = IF durable > LastNL(file) THEN LastNL(file) ELSE durable
    /\ Goto(p, "write")
    /\ UNCHANGED <<lockHolder, acked, powerLost, needNL>>

Write(p) ==
    /\ pc[p] = "write"
    /\ IF Mode = "two_writes"
         THEN /\ file' = Append(file, p)            \* write_all(line)
              /\ Goto(p, "newline")
         ELSE /\ file' = file \o (IF needNL[p] THEN <<NL, Mark, NL, p, NL>> ELSE <<p, NL>>)
              /\ Goto(p, "sync")
    /\ UNCHANGED <<durable, lockHolder, acked, powerLost, needNL>>

Newline(p) ==
    /\ pc[p] = "newline"
    /\ file' = Append(file, NL)                     \* write_all("\n")
    /\ Goto(p, "sync")
    /\ UNCHANGED <<durable, lockHolder, acked, powerLost, needNL>>

\* sync_data: everything written so far is durable; return Ok (acked)
Sync(p) ==
    /\ pc[p] = "sync"
    /\ durable' = Len(file)
    /\ acked' = acked \cup {p}
    /\ lockHolder' = IF lockHolder = p THEN None ELSE lockHolder
    /\ Goto(p, "done")
    /\ UNCHANGED <<file, powerLost, needNL>>

Running(p) == pc[p] \in {"repair", "check", "write", "newline", "sync"}

\* SIGKILL / panic=abort: completed syscalls stay in the page cache.
Crash(p) ==
    /\ Running(p)
    /\ Goto(p, "crashed")
    /\ lockHolder' = IF lockHolder = p THEN None ELSE lockHolder
    /\ UNCHANGED <<file, durable, acked, powerLost, needNL>>

\* Machine crash (once): keep a prefix >= durable, maybe with a torn record.
Power ==
    /\ PowerLoss /\ ~powerLost
    /\ \E n \in durable..Len(file), torn \in BOOLEAN :
          /\ torn => (n < Len(file) /\ file[n+1] \in Procs)
          /\ file' = IF torn THEN Append(SubSeq(file, 1, n), Torn) ELSE SubSeq(file, 1, n)
    /\ durable' = Len(file')
    /\ pc' = [p \in Procs |-> IF Running(p) THEN "crashed" ELSE pc[p]]
    /\ lockHolder' = None
    /\ powerLost' = TRUE
    /\ UNCHANGED <<acked, needNL>>

Next ==
    \/ \E p \in Procs : Start(p) \/ Repair(p) \/ Check(p) \/ Write(p) \/ Newline(p) \/ Sync(p) \/ Crash(p)
    \/ Power
    \/ ((\A p \in Procs : pc[p] \in {"done", "crashed"}) /\ UNCHANGED vars)

Spec == Init /\ [][Next]_vars

\* ---- Properties ----------------------------------------------------------------
TypeOK == /\ durable \in 0..Len(file)
          /\ acked \subseteq Procs

\* read_jsonl never returns the whole-ledger hard error.
LedgerParses == Parses(file)

\* Every append that returned Ok is a whole line in the ledger.
AckedDurable == acked \subseteq GoodRecs(file)

\* ---- Reachability sanity checks (each SHOULD be violated) ----------------------
NeverTwoAcked   == Cardinality(acked) < 2
NeverCrashMid   == ~\E p \in Procs : pc[p] = "crashed" /\ acked = {} /\ Len(file) > 0
NeverPowerLoss  == ~powerLost
=============================================================================
