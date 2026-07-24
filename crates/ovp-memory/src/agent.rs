//! Ask-agent runtime — the tool loop (candidate `ask_agent-v1`, surface:
//! runtime). Model → tool_calls? → execute → observe → loop, over the A1a
//! tool protocol, with the session transcript as the audit authority.
//!
//! NOT wired into any product path at this version (`no_product_wiring`):
//! `POST /api/ask` and `ask.rs` are unchanged; only tests call this engine.
//! Rollout (feature flag → paired eval → default switch) is A3d.
//!
//! Budget contracts (A0 §5.2, guardrails in the candidate spec):
//! - the wall-clock **deadline is authoritative** — every model call and tool
//!   execution gets only the REMAINING budget, and no call ever STARTS after
//!   exhaustion (`deadline_authority`); `max_rounds` is auxiliary;
//! - a tool's 2nd consecutive invalid-arguments failure stops the loop
//!   (`invalid_args_breaker`) — the observed competitor failure mode is an
//!   agent burning rounds re-sending the same malformed call;
//! - every model call's token usage is recorded (`token_accounting`);
//! - the deadline arrives as a caller-supplied parameter, which IS the
//!   nested-propagation interface for a future MCP-embedded ask
//!   (`nested_deadline_interface`).

use std::time::{Duration, Instant};

use ovp_llm::{
    AssistantBlock, ModelClient, ModelMessage, ModelRequest, StopReason, ToolDef,
    ToolResultBlock,
};

use crate::agent_transcript::{SessionStore, StoreError, TranscriptEvent};

/// What a tool execution produced. `InvalidArgs` is distinguished because it
/// feeds the circuit breaker; `Failed` is an execution error (fed back as
/// is_error, does not trip the breaker).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ToolOutcome {
    Ok(String),
    InvalidArgs(String),
    Failed(String),
}

/// The executor the runtime drives. A2 supplies the real vault tools; tests
/// use mocks. `remaining` is the turn's remaining budget — long-running tools must
/// respect it (the runtime re-checks after every call regardless).
pub trait ToolExecutor {
    fn definitions(&self) -> Vec<ToolDef>;
    fn execute(&mut self, name: &str, input: &serde_json::Value, remaining: Duration)
        -> ToolOutcome;
}

/// Why the turn stopped. `NeedUser` is reserved for A3 policy (the runtime
/// cannot distinguish "asking the user" from a final answer without it).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StoppedReason {
    Final,
    NeedUser,
    MaxRounds,
    Timeout,
    ToolError,
    Refusal,
    /// Model-side failure (transport/decode/unknown stop). Unknown stop
    /// reasons land here — `unknown_stop_not_final` (fail-closed).
    ModelError,
}

impl StoppedReason {
    pub fn as_str(&self) -> &'static str {
        match self {
            StoppedReason::Final => "final",
            StoppedReason::NeedUser => "need_user",
            StoppedReason::MaxRounds => "max_rounds",
            StoppedReason::Timeout => "timeout",
            StoppedReason::ToolError => "tool_error",
            StoppedReason::Refusal => "refusal",
            StoppedReason::ModelError => "model_error",
        }
    }
}

/// One tool execution in the answer's trail (compact — full payloads live in
/// the transcript).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolTraceEntry {
    pub tool: String,
    pub tool_call_id: String,
    pub is_error: bool,
    pub summary: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentOutcome {
    pub turn_id: String,
    pub answer: String,
    pub stopped_reason: StoppedReason,
    pub rounds: usize,
    pub tool_trace: Vec<ToolTraceEntry>,
    pub input_tokens_total: u32,
    pub output_tokens_total: u32,
    /// True when this outcome was REPLAYED from a completed turn matching the
    /// caller's idempotency key — nothing was executed.
    pub idempotent_replay: bool,
}

pub struct AgentConfig {
    pub model: String,
    pub system: String,
    pub max_tokens: u32,
    pub temperature: Option<f32>,
    /// Authoritative wall-clock budget for the whole turn.
    pub deadline: Duration,
    /// Auxiliary round cap (a round = one tool-execution batch).
    pub max_rounds: usize,
    /// Per-tool-result byte cap; longer results are truncated with a marker.
    pub max_result_bytes: usize,
    /// Consecutive invalid-arguments failures of ONE tool that stop the loop.
    pub invalid_args_breaker: usize,
    /// Char cap for the model-context projection of prior turns.
    pub projection_max_chars: usize,
}

impl Default for AgentConfig {
    fn default() -> Self {
        Self {
            model: String::new(),
            system: String::new(),
            max_tokens: 1024,
            temperature: None,
            deadline: Duration::from_secs(120),
            max_rounds: 6,
            max_result_bytes: 32 * 1024,
            invalid_args_breaker: 2,
            projection_max_chars: 60_000,
        }
    }
}

#[derive(Debug)]
pub enum AgentError {
    /// Another turn is running on this session (or the store is locked).
    SessionBusy,
    Store(String),
}

impl std::fmt::Display for AgentError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AgentError::SessionBusy => write!(f, "session busy — a turn is already running"),
            AgentError::Store(d) => write!(f, "agent store: {d}"),
        }
    }
}

impl std::error::Error for AgentError {}

/// Run one agent turn on `store`'s session. Mid-turn failures (model errors,
/// timeouts, breakers) are NOT `Err` — they complete the turn with the
/// matching `stopped_reason` so the audit trail is always whole. `Err` is
/// reserved for pre-turn conditions: a busy session or store IO.
pub fn run_agent_turn(
    client: &mut dyn ModelClient,
    tools: &mut dyn ToolExecutor,
    store: &mut SessionStore,
    question: &str,
    idempotency_key: Option<&str>,
    cfg: &AgentConfig,
) -> Result<AgentOutcome, AgentError> {
    // The whole-turn budget starts NOW — refresh, lock acquisition, and
    // compaction all spend from it (`deadline_authority` covers the turn,
    // not just the loop).
    let started = Instant::now();
    // Idempotent replay BEFORE locking: a completed turn answers retries even
    // while a different (new) turn holds the lock. Refresh the read-only view
    // first — a long-lived store must see keys committed by other processes.
    store
        .refresh_readonly()
        .map_err(|e| AgentError::Store(e.to_string()))?;
    if let Some(key) = idempotency_key
        && let Some(done) = store.completed_turn_for_key(key)
    {
        return Ok(AgentOutcome {
            tool_trace: replayed_trace(store, &done.turn_id),
            turn_id: done.turn_id,
            answer: done.answer,
            stopped_reason: parse_stopped(&done.stopped_reason),
            rounds: done.rounds,
            input_tokens_total: done.input_tokens_total,
            output_tokens_total: done.output_tokens_total,
            idempotent_replay: true,
        });
    }

    let lock = store.lock().map_err(|e| match e {
        StoreError::SessionBusy { .. } => AgentError::SessionBusy,
        StoreError::Io(d) => AgentError::Store(d),
    })?;
    // Under the lock: refresh state (a turn may have committed since `open`,
    // which would stale our turn id + projection) and physically compact any
    // crash-torn tail — the only place a rewrite cannot race an appender.
    store
        .reload_under_lock(&lock)
        .map_err(|e| AgentError::Store(e.to_string()))?;
    // Re-check idempotency against the FRESH view (the same key may have
    // completed between the pre-lock check and lock acquisition).
    if let Some(key) = idempotency_key
        && let Some(done) = store.completed_turn_for_key(key)
    {
        return Ok(AgentOutcome {
            tool_trace: replayed_trace(store, &done.turn_id),
            turn_id: done.turn_id,
            answer: done.answer,
            stopped_reason: parse_stopped(&done.stopped_reason),
            rounds: done.rounds,
            input_tokens_total: done.input_tokens_total,
            output_tokens_total: done.output_tokens_total,
            idempotent_replay: true,
        });
    }

    let remaining = |started: Instant| cfg.deadline.saturating_sub(started.elapsed());

    let turn_id = store.next_turn_id();
    let mut events: Vec<TranscriptEvent> = vec![TranscriptEvent::TurnStarted {
        turn_id: turn_id.clone(),
        question: question.to_string(),
        idempotency_key: idempotency_key.map(str::to_string),
    }];

    // Conversation = capped projection of prior turns + this turn's messages.
    let mut messages = store.projection(cfg.projection_max_chars);
    let user_msg = ModelMessage::User { content: question.to_string() };
    messages.push(user_msg.clone());
    events.push(TranscriptEvent::Message { turn_id: turn_id.clone(), message: user_msg });

    let tool_defs = tools.definitions();
    let mut rounds = 0usize;
    let mut in_total = 0u32;
    let mut out_total = 0u32;
    let mut trace: Vec<ToolTraceEntry> = Vec::new();
    let mut last_text = String::new();
    // Per-tool CONSECUTIVE invalid-args counter (`invalid_args_breaker`).
    let mut invalid_streak: std::collections::BTreeMap<String, usize> =
        std::collections::BTreeMap::new();

    let stopped = 'turn: loop {
        // `deadline_authority`: no model call STARTS after exhaustion.
        if remaining(started).is_zero() {
            break 'turn StoppedReason::Timeout;
        }
        let request = ModelRequest {
            model: cfg.model.clone(),
            system: (!cfg.system.is_empty()).then(|| cfg.system.clone()),
            messages: messages.clone(),
            max_tokens: cfg.max_tokens,
            temperature: cfg.temperature,
            tools: (!tool_defs.is_empty()).then(|| tool_defs.clone()),
            cache_namespace: Some("ask_agent/v1".into()),
        };
        let reply = match client.call(&request) {
            Ok(r) => r,
            // Mid-turn model failure ends the turn with an honest reason —
            // audited (`ModelFailed`), and the trail still gets TurnFinished.
            Err(e) => {
                events.push(TranscriptEvent::ModelFailed {
                    turn_id: turn_id.clone(),
                    round: rounds,
                    detail: e.to_string(),
                });
                break 'turn StoppedReason::ModelError;
            }
        };
        events.push(TranscriptEvent::ModelCalled {
            turn_id: turn_id.clone(),
            round: rounds,
            input_tokens: reply.usage.input_tokens,
            output_tokens: reply.usage.output_tokens,
            // Attribute the spend to the model that actually replied — a
            // client-side override can differ from cfg.model.
            src: if reply.model.is_empty() { cfg.model.clone() } else { reply.model.clone() },
            scope: "turn".into(),
        });
        in_total += reply.usage.input_tokens;
        out_total += reply.usage.output_tokens;
        // `deadline_authority` for the MODEL side too: the trait carries no
        // per-call timeout (propagating remaining into the transport is the
        // live-client integration's A1a-v2 concern), so a reply that lands
        // after the deadline is LATE — audited, usage counted, never
        // delivered as an answer or executed.
        if remaining(started).is_zero() {
            events.push(TranscriptEvent::ReplyDiscarded {
                turn_id: turn_id.clone(),
                round: rounds,
                reason: "model reply arrived after the turn deadline".into(),
            });
            break 'turn StoppedReason::Timeout;
        }
        if !reply.text.is_empty() {
            last_text = reply.text.clone();
        }

        let calls: Vec<(String, String, serde_json::Value)> = reply
            .executable_tool_calls()
            .map(|calls| {
                calls
                    .iter()
                    .map(|c| (c.id.to_string(), c.name.to_string(), c.input.clone()))
                    .collect()
            })
            .unwrap_or_default();

        if calls.is_empty() {
            // Fail closed on a CONTRADICTORY shape: tool_use blocks under a
            // final stop reason (EndTurn/StopSequence). Treating it as Final
            // would silently ignore the calls and deliver an incomplete
            // answer as success.
            let has_tool_blocks = reply
                .blocks
                .as_deref()
                .unwrap_or_default()
                .iter()
                .any(|b| matches!(b, ovp_llm::ReplyBlock::ToolUse { .. }));
            if has_tool_blocks
                && matches!(reply.stop_reason, StopReason::EndTurn | StopReason::StopSequence)
            {
                events.push(TranscriptEvent::ReplyDiscarded {
                    turn_id: turn_id.clone(),
                    round: rounds,
                    reason: format!(
                        "contradictory reply: tool_use blocks under stop_reason {:?}",
                        reply.stop_reason
                    ),
                });
                break 'turn StoppedReason::ModelError;
            }
            // No executable tools → the reply is the turn's terminal state.
            // Record the terminal assistant text as a Message event: later
            // turns project THIS turn and must see its answer, or multi-turn
            // continuity breaks and the audit is incomplete.
            if !reply.text.is_empty() {
                let final_msg = ModelMessage::Assistant { content: reply.text.clone() };
                messages.push(final_msg.clone());
                events.push(TranscriptEvent::Message {
                    turn_id: turn_id.clone(),
                    message: final_msg,
                });
            }
            break 'turn match reply.stop_reason {
                StopReason::Refusal => StoppedReason::Refusal,
                // `unknown_stop_not_final`: fail-closed.
                StopReason::Unknown => StoppedReason::ModelError,
                // ToolUse with zero executable calls cannot happen (the parse
                // layer rejects truncated tool turns); treat defensively.
                StopReason::ToolUse => StoppedReason::ModelError,
                // A MaxTokens text answer is TRUNCATED — surfacing it as a
                // clean Final would present an incomplete answer as success
                // (`is_final_success` is false for MaxTokens). The partial
                // text still reaches the caller via `answer`.
                StopReason::MaxTokens => StoppedReason::ModelError,
                StopReason::EndTurn | StopReason::StopSequence => StoppedReason::Final,
            };
        }

        // `duplicate_ids_fail_closed`: a reply reusing a call id executes
        // NOTHING (the A1a request validator would reject the follow-up).
        {
            let mut seen = std::collections::BTreeSet::new();
            if calls.iter().any(|(id, _, _)| !seen.insert(id.clone())) {
                events.push(TranscriptEvent::ReplyDiscarded {
                    turn_id: turn_id.clone(),
                    round: rounds,
                    reason: "duplicate tool_use ids within one reply".into(),
                });
                break 'turn StoppedReason::ToolError;
            }
        }

        // `max_rounds_auxiliary`: refuse the batch BEFORE recording the
        // assistant tool turn — a recorded tool_use with no results would
        // make every later projection protocol-invalid.
        if rounds + 1 > cfg.max_rounds {
            events.push(TranscriptEvent::ReplyDiscarded {
                turn_id: turn_id.clone(),
                round: rounds,
                reason: format!("tool batch refused: max_rounds ({}) reached", cfg.max_rounds),
            });
            break 'turn StoppedReason::MaxRounds;
        }
        rounds += 1;

        // Record the assistant tool turn VERBATIM from the provider blocks —
        // interleaved text/tool_use order is part of the protocol and of
        // deterministic replay (concatenating text would reorder it).
        let assistant_msg = ModelMessage::AssistantBlocks {
            blocks: reply
                .blocks
                .as_deref()
                .unwrap_or_default()
                .iter()
                .map(|b| match b {
                    ovp_llm::ReplyBlock::Text { text } => {
                        AssistantBlock::Text { text: text.clone() }
                    }
                    ovp_llm::ReplyBlock::ToolUse { id, name, input } => {
                        AssistantBlock::ToolUse {
                            id: id.clone(),
                            name: name.clone(),
                            input: input.clone(),
                        }
                    }
                })
                .collect(),
        };
        messages.push(assistant_msg.clone());
        events.push(TranscriptEvent::Message { turn_id: turn_id.clone(), message: assistant_msg });

        // Execute the calls — failed ones become is_error results so the
        // round is always fully answered (`all_results_fed_back`). A mid-
        // batch deadline exhaustion fills the REMAINING calls with timeout
        // error results: the recorded assistant turn must always get its
        // complete, adjacent result batch or the transcript would be
        // protocol-invalid for every later projection.
        let mut results: Vec<ToolResultBlock> = Vec::new();
        let mut breaker_tripped = false;
        let mut timed_out = false;
        for (id, name, input) in &calls {
            let left = remaining(started);
            let outcome = if left.is_zero() || timed_out {
                timed_out = true;
                ToolOutcome::Failed("skipped: turn deadline exhausted".into())
            } else if tool_defs.iter().any(|d| &d.name == name) {
                tools.execute(name, input, left)
            } else {
                // A hallucinated tool name is an arguments-class failure: it
                // feeds back once and counts toward the breaker.
                ToolOutcome::InvalidArgs(format!("unknown tool `{name}`"))
            };
            // `deadline_authority` at the RESULT level too: a tool that
            // overruns the remaining budget produced LATE data — audit it,
            // but never feed it to the model as a success (A0 §5.2: late
            // results are side-log material, not deliveries).
            let finished_late = remaining(started).is_zero() && !timed_out;
            let (mut content, is_error, was_invalid_args) = match outcome {
                ToolOutcome::Ok(body) => (body, false, false),
                ToolOutcome::InvalidArgs(detail) => {
                    let n = invalid_streak.entry(name.clone()).or_insert(0);
                    *n += 1;
                    if *n >= cfg.invalid_args_breaker {
                        breaker_tripped = true;
                    }
                    (format!("invalid arguments: {detail}"), true, true)
                }
                ToolOutcome::Failed(detail) => (format!("tool failed: {detail}"), true, false),
            };
            // The breaker counts CONSECUTIVE invalid-args per tool: any other
            // outcome — success OR an execution failure — resets the streak.
            if !was_invalid_args {
                invalid_streak.remove(name);
            }
            // Audit keeps the FULL RAW result (the transcript is complete by
            // contract); only the model-facing copy is capped.
            let raw_bytes = content.len();
            let truncated = raw_bytes > cfg.max_result_bytes;
            events.push(TranscriptEvent::ToolCalled {
                turn_id: turn_id.clone(),
                tool_call_id: id.clone(),
                tool: name.clone(),
                is_error: is_error || finished_late,
                result_bytes: raw_bytes,
                content: content.clone(),
                truncated,
            });
            trace.push(ToolTraceEntry {
                tool: name.clone(),
                tool_call_id: id.clone(),
                is_error: is_error || finished_late,
                // Late data is AUDIT-ONLY: the caller-facing trail (and any
                // idempotent replay built from it) gets the discard marker,
                // never the content.
                summary: if finished_late {
                    "late: discarded (audit only)".to_string()
                } else {
                    content.chars().take(120).collect()
                },
            });
            let (model_content, model_is_error) = if finished_late {
                timed_out = true;
                (
                    "tool result arrived after the turn deadline; discarded \
                     (kept in the audit transcript only)"
                        .to_string(),
                    true,
                )
            } else {
                if truncated {
                    // The marker counts AGAINST the cap — the model-facing
                    // copy must never exceed it.
                    const MARKER: &str = "\n[truncated]";
                    let keep = cfg.max_result_bytes.saturating_sub(MARKER.len());
                    content.truncate(floor_char_boundary(&content, keep));
                    content.push_str(MARKER);
                    if content.len() > cfg.max_result_bytes {
                        // Degenerate tiny caps: the marker alone overflows.
                        content.truncate(floor_char_boundary(&content, cfg.max_result_bytes));
                    }
                }
                (content, is_error)
            };
            results.push(ToolResultBlock {
                tool_call_id: id.clone(),
                content: model_content,
                is_error: model_is_error,
            });
        }

        // The COMPLETE batch is always recorded (adjacency), then a timeout
        // ends the turn.
        let results_msg = ModelMessage::ToolResults { results };
        messages.push(results_msg.clone());
        events.push(TranscriptEvent::Message { turn_id: turn_id.clone(), message: results_msg });

        if timed_out {
            break 'turn StoppedReason::Timeout;
        }

        if breaker_tripped {
            break 'turn StoppedReason::ToolError;
        }
        // Loop: the model observes the results and decides the next step.
    };

    let answer = last_text;
    events.push(TranscriptEvent::TurnFinished {
        turn_id: turn_id.clone(),
        stopped_reason: stopped.as_str().to_string(),
        answer: answer.clone(),
        rounds,
        input_tokens_total: in_total,
        output_tokens_total: out_total,
    });
    store
        .commit_turn(events)
        .map_err(|e| AgentError::Store(e.to_string()))?;

    Ok(AgentOutcome {
        turn_id,
        answer,
        stopped_reason: stopped,
        rounds,
        tool_trace: trace,
        input_tokens_total: in_total,
        output_tokens_total: out_total,
        idempotent_replay: false,
    })
}

/// Rebuild a replayed outcome's tool trace from the turn's audit rows — a
/// retry must not lose the provenance trail the original response carried.
fn replayed_trace(store: &SessionStore, turn_id: &str) -> Vec<ToolTraceEntry> {
    store
        .tool_calls_for_turn(turn_id)
        .into_iter()
        .map(|(tool, tool_call_id, is_error, summary)| ToolTraceEntry {
            tool,
            tool_call_id,
            is_error,
            summary,
        })
        .collect()
}

fn parse_stopped(s: &str) -> StoppedReason {
    match s {
        "final" => StoppedReason::Final,
        "need_user" => StoppedReason::NeedUser,
        "max_rounds" => StoppedReason::MaxRounds,
        "timeout" => StoppedReason::Timeout,
        "tool_error" => StoppedReason::ToolError,
        "refusal" => StoppedReason::Refusal,
        _ => StoppedReason::ModelError,
    }
}

/// Largest byte index ≤ `max` that is a char boundary (stable-Rust version of
/// `str::floor_char_boundary`) — result truncation must not split UTF-8.
fn floor_char_boundary(s: &str, max: usize) -> usize {
    if max >= s.len() {
        return s.len();
    }
    let mut i = max;
    while i > 0 && !s.is_char_boundary(i) {
        i -= 1;
    }
    i
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_llm::{CallError, ModelReply, ReplyBlock, Usage};
    use std::collections::VecDeque;

    // ---- scripted model client: replies served in order ----
    struct Scripted {
        replies: VecDeque<Result<ModelReply, CallError>>,
        pub requests: Vec<ModelRequest>,
    }

    impl Scripted {
        fn new(replies: Vec<Result<ModelReply, CallError>>) -> Self {
            Self { replies: replies.into(), requests: Vec::new() }
        }
    }

    impl ModelClient for Scripted {
        fn call(&mut self, request: &ModelRequest) -> Result<ModelReply, CallError> {
            self.requests.push(request.clone());
            self.replies.pop_front().unwrap_or(Err(CallError::Unexpected {
                detail: "script exhausted".into(),
            }))
        }
    }

    fn text_reply(text: &str) -> Result<ModelReply, CallError> {
        Ok(ModelReply {
            model: "m".into(),
            text: text.into(),
            stop_reason: StopReason::EndTurn,
            usage: Usage { input_tokens: 10, output_tokens: 5 },
            blocks: Some(vec![ReplyBlock::Text { text: text.into() }]),
            raw_stop_reason: None,
        })
    }

    fn tool_reply(calls: &[(&str, &str)]) -> Result<ModelReply, CallError> {
        Ok(ModelReply {
            model: "m".into(),
            text: String::new(),
            stop_reason: StopReason::ToolUse,
            usage: Usage { input_tokens: 20, output_tokens: 8 },
            blocks: Some(
                calls
                    .iter()
                    .map(|(id, name)| ReplyBlock::ToolUse {
                        id: (*id).into(),
                        name: (*name).into(),
                        input: serde_json::json!({"q": "x"}),
                    })
                    .collect(),
            ),
            raw_stop_reason: None,
        })
    }

    // ---- mock tools ----
    #[derive(Clone)]
    enum Behavior {
        Ok(&'static str),
        InvalidArgs,
        Fail,
        Slow(Duration),
        Big(usize),
        /// Per-call scripted outcomes (last one repeats).
        Script(Vec<ToolOutcome>),
    }

    struct MockTools {
        behaviors: std::collections::BTreeMap<String, Behavior>,
        pub calls: Vec<String>,
    }

    impl MockTools {
        fn new(b: &[(&str, Behavior)]) -> Self {
            Self {
                behaviors: b.iter().map(|(n, x)| (n.to_string(), x.clone())).collect(),
                calls: Vec::new(),
            }
        }
    }

    impl ToolExecutor for MockTools {
        fn definitions(&self) -> Vec<ToolDef> {
            self.behaviors
                .keys()
                .map(|name| ToolDef {
                    name: name.clone(),
                    version: "v1".into(),
                    description: "test tool".into(),
                    input_schema: serde_json::json!({"type": "object"}),
                })
                .collect()
        }

        fn execute(
            &mut self,
            name: &str,
            _input: &serde_json::Value,
            _remaining: Duration,
        ) -> ToolOutcome {
            self.calls.push(name.to_string());
            match self.behaviors.get(name) {
                Some(Behavior::Ok(s)) => ToolOutcome::Ok((*s).to_string()),
                Some(Behavior::InvalidArgs) => ToolOutcome::InvalidArgs("bad".into()),
                Some(Behavior::Fail) => ToolOutcome::Failed("boom".into()),
                Some(Behavior::Slow(d)) => {
                    std::thread::sleep(*d);
                    ToolOutcome::Ok("slow done".into())
                }
                Some(Behavior::Big(n)) => ToolOutcome::Ok("x".repeat(*n)),
                Some(Behavior::Script(seq)) => {
                    let n = self.calls.iter().filter(|c| c == &name).count() - 1;
                    seq.get(n.min(seq.len() - 1)).cloned().unwrap_or(ToolOutcome::Failed("empty script".into()))
                }
                None => ToolOutcome::Failed("unrouted".into()),
            }
        }
    }

    fn cfg() -> AgentConfig {
        AgentConfig { model: "m".into(), system: "sys".into(), ..AgentConfig::default() }
    }

    fn store(dir: &std::path::Path) -> SessionStore {
        SessionStore::open(dir, "s1").unwrap()
    }

    // 0-tool: model answers directly; single round-trip, transcript complete.
    #[test]
    fn zero_tool_direct_answer() {
        let dir = tempfile::tempdir().unwrap();
        let mut client = Scripted::new(vec![text_reply("direct")]);
        let mut tools = MockTools::new(&[("search", Behavior::Ok("hit"))]);
        let mut st = store(dir.path());
        let out =
            run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &cfg()).unwrap();
        assert_eq!(out.answer, "direct");
        assert_eq!(out.stopped_reason, StoppedReason::Final);
        assert_eq!(out.rounds, 0);
        assert!(tools.calls.is_empty());
        assert_eq!(out.input_tokens_total, 10);
        // Turn is durable: reopening sees one complete turn.
        let st2 = store(dir.path());
        assert_eq!(st2.next_turn_id(), "t2");
    }

    // 1-tool then answer: results fed back, second request carries them.
    #[test]
    fn one_tool_then_final() {
        let dir = tempfile::tempdir().unwrap();
        let mut client =
            Scripted::new(vec![tool_reply(&[("c1", "search")]), text_reply("answer")]);
        let mut tools = MockTools::new(&[("search", Behavior::Ok("hit"))]);
        let mut st = store(dir.path());
        let out =
            run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &cfg()).unwrap();
        assert_eq!(out.answer, "answer");
        assert_eq!(out.rounds, 1);
        assert_eq!(tools.calls, vec!["search"]);
        // `all_results_fed_back`: 2nd request ends with ToolResults for c1.
        let second = &client.requests[1];
        match second.messages.last().unwrap() {
            ModelMessage::ToolResults { results } => {
                assert_eq!(results.len(), 1);
                assert_eq!(results[0].tool_call_id, "c1");
                assert!(!results[0].is_error);
            }
            other => panic!("expected ToolResults, got {other:?}"),
        }
    }

    // 2 tools in one round: both execute, both results fed back (one failed).
    #[test]
    fn two_tools_one_failed_all_results_fed_back() {
        let dir = tempfile::tempdir().unwrap();
        let mut client = Scripted::new(vec![
            tool_reply(&[("c1", "search"), ("c2", "broken")]),
            text_reply("done"),
        ]);
        let mut tools =
            MockTools::new(&[("search", Behavior::Ok("hit")), ("broken", Behavior::Fail)]);
        let mut st = store(dir.path());
        let out =
            run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &cfg()).unwrap();
        assert_eq!(out.stopped_reason, StoppedReason::Final);
        let second = &client.requests[1];
        match second.messages.last().unwrap() {
            ModelMessage::ToolResults { results } => {
                assert_eq!(results.len(), 2);
                assert!(!results[0].is_error);
                assert!(results[1].is_error, "failed execution must be is_error");
            }
            other => panic!("expected ToolResults, got {other:?}"),
        }
    }

    // Unknown tool name: fed back once as invalid-args; second unknown trips
    // the breaker (`invalid_args_breaker`).
    #[test]
    fn unknown_tool_feeds_back_then_breaker_stops() {
        let dir = tempfile::tempdir().unwrap();
        let mut client = Scripted::new(vec![
            tool_reply(&[("c1", "ghost")]),
            tool_reply(&[("c2", "ghost")]),
            text_reply("never reached"),
        ]);
        let mut tools = MockTools::new(&[("search", Behavior::Ok("hit"))]);
        let mut st = store(dir.path());
        let out =
            run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &cfg()).unwrap();
        assert_eq!(out.stopped_reason, StoppedReason::ToolError);
        assert_eq!(out.rounds, 2);
        assert!(tools.calls.is_empty(), "unknown tool must never reach the executor");
        // The first unknown WAS fed back (2nd model call happened).
        assert_eq!(client.requests.len(), 2);
    }

    // Consecutive invalid args on one tool trips the breaker; success resets.
    #[test]
    fn invalid_args_breaker_and_reset() {
        let dir = tempfile::tempdir().unwrap();
        // bad → ok → bad → bad = breaker (streak resets after ok).
        let mut client = Scripted::new(vec![
            tool_reply(&[("c1", "flaky")]),
            tool_reply(&[("c2", "good")]),
            tool_reply(&[("c3", "flaky")]),
            tool_reply(&[("c4", "flaky")]),
            text_reply("never"),
        ]);
        let mut tools = MockTools::new(&[
            ("flaky", Behavior::InvalidArgs),
            ("good", Behavior::Ok("fine")),
        ]);
        let mut st = store(dir.path());
        let mut c = cfg();
        c.max_rounds = 10;
        let out = run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &c).unwrap();
        // flaky: bad(streak1) → good resets NOTHING for flaky (different tool),
        // so flaky's streak continues: bad(streak2) = breaker at round 3.
        assert_eq!(out.stopped_reason, StoppedReason::ToolError);
        assert_eq!(client.requests.len(), 3, "breaker fires on flaky's 2nd consecutive failure");
    }

    // Total timeout: a slow tool exhausts the deadline; no further model call.
    #[test]
    fn total_timeout_stops_loop() {
        let dir = tempfile::tempdir().unwrap();
        let mut client = Scripted::new(vec![
            tool_reply(&[("c1", "slow")]),
            text_reply("never reached"),
        ]);
        let mut tools =
            MockTools::new(&[("slow", Behavior::Slow(Duration::from_millis(600)))]);
        let mut st = store(dir.path());
        let mut c = cfg();
        // Generous margins: the clock starts at function entry, so lock/fs
        // preprocessing must fit well inside the deadline under parallel load.
        c.deadline = Duration::from_millis(300);
        let out = run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &c).unwrap();
        assert_eq!(out.stopped_reason, StoppedReason::Timeout);
        assert_eq!(client.requests.len(), 1, "no model call may START after the deadline");
        // The timed-out turn is still a complete audited turn.
        let st2 = store(dir.path());
        assert_eq!(st2.next_turn_id(), "t2");
    }

    // Max rounds: scripted infinite tool loop stops at the cap.
    #[test]
    fn max_rounds_auxiliary_cap() {
        let dir = tempfile::tempdir().unwrap();
        let replies: Vec<_> = (0..10)
            .map(|i| tool_reply(&[(format!("c{i}").leak() as &str, "search")]))
            .collect();
        let mut client = Scripted::new(replies);
        let mut tools = MockTools::new(&[("search", Behavior::Ok("hit"))]);
        let mut st = store(dir.path());
        let mut c = cfg();
        c.max_rounds = 3;
        let out = run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &c).unwrap();
        assert_eq!(out.stopped_reason, StoppedReason::MaxRounds);
        assert_eq!(out.rounds, 3, "the over-cap batch is refused BEFORE recording");
        // The transcript must contain no unanswered tool_use: every recorded
        // AssistantBlocks turn is followed by its ToolResults.
        let msgs: Vec<_> = st
            .events()
            .iter()
            .filter_map(|e| match e {
                TranscriptEvent::Message { message, .. } => Some(message.clone()),
                _ => None,
            })
            .collect();
        for (i, m) in msgs.iter().enumerate() {
            if matches!(m, ModelMessage::AssistantBlocks { .. }) {
                assert!(
                    matches!(msgs.get(i + 1), Some(ModelMessage::ToolResults { .. })),
                    "tool turn at {i} lacks its adjacent results"
                );
            }
        }
    }

    // Duplicate tool_use ids in ONE reply: nothing executes, fail-closed.
    #[test]
    fn duplicate_ids_fail_closed() {
        let dir = tempfile::tempdir().unwrap();
        let mut client =
            Scripted::new(vec![tool_reply(&[("dup", "search"), ("dup", "search")])]);
        let mut tools = MockTools::new(&[("search", Behavior::Ok("hit"))]);
        let mut st = store(dir.path());
        let out =
            run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &cfg()).unwrap();
        assert_eq!(out.stopped_reason, StoppedReason::ToolError);
        assert!(tools.calls.is_empty());
        assert!(
            st.events()
                .iter()
                .any(|e| matches!(e, TranscriptEvent::ReplyDiscarded { reason, .. } if reason.contains("duplicate"))),
            "the discarded reply is audited"
        );
    }

    // Refusal and unknown stop reasons: never final-success.
    #[test]
    fn refusal_and_unknown_stops() {
        let dir = tempfile::tempdir().unwrap();
        let refusal = Ok(ModelReply {
            stop_reason: StopReason::Refusal,
            ..text_reply("cannot").unwrap()
        });
        let mut client = Scripted::new(vec![refusal]);
        let mut tools = MockTools::new(&[]);
        let mut st = store(dir.path());
        let out =
            run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &cfg()).unwrap();
        assert_eq!(out.stopped_reason, StoppedReason::Refusal);

        let unknown = Ok(ModelReply {
            stop_reason: StopReason::Unknown,
            ..text_reply("???").unwrap()
        });
        let mut client = Scripted::new(vec![unknown]);
        let mut st2 = SessionStore::open(dir.path(), "s2").unwrap();
        let out =
            run_agent_turn(&mut client, &mut tools, &mut st2, "q?", None, &cfg()).unwrap();
        assert_eq!(out.stopped_reason, StoppedReason::ModelError, "unknown stop ≠ final");
    }

    // Oversized tool result is truncated at a char boundary with a marker.
    #[test]
    fn tool_result_truncated_to_cap() {
        let dir = tempfile::tempdir().unwrap();
        let mut client =
            Scripted::new(vec![tool_reply(&[("c1", "big")]), text_reply("ok")]);
        let mut tools = MockTools::new(&[("big", Behavior::Big(100_000))]);
        let mut st = store(dir.path());
        let mut c = cfg();
        c.max_result_bytes = 1000;
        let out = run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &c).unwrap();
        assert_eq!(out.stopped_reason, StoppedReason::Final);
        match client.requests[1].messages.last().unwrap() {
            ModelMessage::ToolResults { results } => {
                assert!(results[0].content.len() < 1100);
                assert!(results[0].content.ends_with("[truncated]"));
            }
            other => panic!("expected ToolResults, got {other:?}"),
        }
        // The AUDIT row keeps the full raw result — only the model copy caps.
        let audit = st
            .events()
            .iter()
            .find_map(|e| match e {
                TranscriptEvent::ToolCalled { content, truncated, result_bytes, .. } => {
                    Some((content.len(), *truncated, *result_bytes))
                }
                _ => None,
            })
            .unwrap();
        assert_eq!(audit.0, 100_000, "audit content is the raw result");
        assert!(audit.1, "truncated flag marks the capped model copy");
        assert_eq!(audit.2, 100_000);
    }

    // Concurrent same-session submit: the lock makes the second SessionBusy.
    #[test]
    fn concurrent_same_session_is_busy() {
        let dir = tempfile::tempdir().unwrap();
        let st = store(dir.path());
        let _held = st.lock().unwrap();
        let mut client = Scripted::new(vec![text_reply("x")]);
        let mut tools = MockTools::new(&[]);
        let mut st2 = store(dir.path());
        let err = run_agent_turn(&mut client, &mut tools, &mut st2, "q?", None, &cfg())
            .unwrap_err();
        assert!(matches!(err, AgentError::SessionBusy));
        assert!(client.requests.is_empty(), "busy session must run nothing");
    }

    // Idempotency: same key replays the completed turn without running —
    // INCLUDING its tool trace (a retry must not lose provenance).
    #[test]
    fn idempotent_retry_replays_without_running() {
        let dir = tempfile::tempdir().unwrap();
        let mut client =
            Scripted::new(vec![tool_reply(&[("c1", "search")]), text_reply("first")]);
        let mut tools = MockTools::new(&[("search", Behavior::Ok("hit"))]);
        let mut st = store(dir.path());
        let first =
            run_agent_turn(&mut client, &mut tools, &mut st, "q?", Some("k1"), &cfg())
                .unwrap();
        assert!(!first.idempotent_replay);

        let mut client2 = Scripted::new(vec![text_reply("SHOULD NOT RUN")]);
        let replay =
            run_agent_turn(&mut client2, &mut tools, &mut st, "q?", Some("k1"), &cfg())
                .unwrap();
        assert!(replay.idempotent_replay);
        assert_eq!(replay.answer, "first");
        assert_eq!(replay.turn_id, first.turn_id);
        assert!(client2.requests.is_empty(), "retry must not produce a second turn");
        assert_eq!(replay.tool_trace.len(), 1, "replay rebuilds the original trace");
        assert_eq!(replay.tool_trace[0].tool, "search");
        // A DIFFERENT key runs normally.
        let mut client3 = Scripted::new(vec![text_reply("second")]);
        let out =
            run_agent_turn(&mut client3, &mut tools, &mut st, "q2?", Some("k2"), &cfg())
                .unwrap();
        assert!(!out.idempotent_replay);
        assert_eq!(out.turn_id, "t2");
    }

    // Crash recovery: a torn tail (no TurnFinished) is compacted on open.
    #[test]
    fn crash_recovery_drops_torn_turn() {
        let dir = tempfile::tempdir().unwrap();
        {
            let mut client = Scripted::new(vec![text_reply("whole")]);
            let mut tools = MockTools::new(&[]);
            let mut st = store(dir.path());
            run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &cfg()).unwrap();
        }
        // Simulate a crash mid-turn: append turn events WITHOUT TurnFinished
        // (raw lines in the on-disk wrapper shape: schema + session_id + event).
        let path = dir.path().join("s1.jsonl");
        let torn = concat!(
            r#"{"schema":"ovp.ask_transcript/v1","session_id":"s1","event":"turn_started","turn_id":"t2","question":"torn"}"#,
            "\n",
            r#"{"schema":"ovp.ask_transcript/v1","session_id":"s1","event":"message","turn_id":"t2","message":{"role":"user","content":"torn"}}"#,
            "\n"
        )
        .to_string();
        use std::io::Write as _;
        std::fs::OpenOptions::new()
            .append(true)
            .open(&path)
            .unwrap()
            .write_all(torn.as_bytes())
            .unwrap();

        // A read-only open IGNORES the torn tail in memory but must NOT
        // rewrite the file (it could race a concurrent appender).
        let st = store(dir.path());
        assert_eq!(st.next_turn_id(), "t2", "torn turn ignored in memory");
        assert!(
            std::fs::read_to_string(&path).unwrap().contains("torn"),
            "open() must not rewrite pre-lock"
        );
        // Running a turn (which compacts UNDER THE LOCK) purges the torn tail.
        let mut client = Scripted::new(vec![text_reply("fresh")]);
        let mut tools = MockTools::new(&[]);
        let mut st = store(dir.path());
        let out =
            run_agent_turn(&mut client, &mut tools, &mut st, "again?", None, &cfg()).unwrap();
        assert_eq!(out.turn_id, "t2");
        assert!(
            !std::fs::read_to_string(&path).unwrap().contains("torn"),
            "locked compaction removed the torn tail"
        );
    }

    // Projection cap: oldest turns trimmed WHOLE; pairs never split.
    #[test]
    fn projection_trims_whole_turns_oldest_first() {
        let dir = tempfile::tempdir().unwrap();
        let mut tools = MockTools::new(&[("search", Behavior::Ok("hit"))]);
        let mut st = store(dir.path());
        // Turn 1: tool turn (User + AssistantBlocks + ToolResults + final).
        let mut client = Scripted::new(vec![
            tool_reply(&[("c1", "search")]),
            text_reply("one"),
        ]);
        run_agent_turn(&mut client, &mut tools, &mut st, "first?", None, &cfg()).unwrap();
        // Turn 2: plain answer.
        let mut client = Scripted::new(vec![text_reply("two")]);
        run_agent_turn(&mut client, &mut tools, &mut st, "second?", None, &cfg()).unwrap();

        // Unlimited: both turns present, pair intact.
        let full = st.projection(1_000_000);
        let has_tool_use = full.iter().any(|m| matches!(m, ModelMessage::AssistantBlocks { .. }));
        let has_results = full.iter().any(|m| matches!(m, ModelMessage::ToolResults { .. }));
        assert!(has_tool_use && has_results);

        // Tight cap: turn 1 (the big tool turn) is dropped WHOLE — no orphan
        // ToolResults without its AssistantBlocks.
        let turn2_len: usize = st
            .projection(1_000_000)
            .iter()
            .rev()
            .take(2) // turn 2 = [User, (assistant text is not stored as Message? it is…)]
            .map(|m| serde_json::to_string(m).unwrap().len())
            .sum();
        let capped = st.projection(turn2_len + 8);
        assert!(!capped.is_empty(), "newest turn survives the cap");
        let orphan_results = capped
            .iter()
            .any(|m| matches!(m, ModelMessage::ToolResults { .. }))
            && !capped
                .iter()
                .any(|m| matches!(m, ModelMessage::AssistantBlocks { .. }));
        assert!(!orphan_results, "a ToolResults must never appear without its assistant turn");
    }

    // Token accounting: usage summed across model calls.
    #[test]
    fn token_accounting_sums_all_calls() {
        let dir = tempfile::tempdir().unwrap();
        let mut client =
            Scripted::new(vec![tool_reply(&[("c1", "search")]), text_reply("done")]);
        let mut tools = MockTools::new(&[("search", Behavior::Ok("hit"))]);
        let mut st = store(dir.path());
        let out =
            run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &cfg()).unwrap();
        assert_eq!(out.input_tokens_total, 30); // 20 (tool round) + 10 (final)
        assert_eq!(out.output_tokens_total, 13); // 8 + 5
        // And the transcript carries per-call events, attributed to the model
        // that actually replied (reply.model = "m"), scoped to the turn.
        let usage_rows: Vec<_> = st
            .events()
            .iter()
            .filter_map(|e| match e {
                TranscriptEvent::ModelCalled { src, scope, .. } => {
                    Some((src.clone(), scope.clone()))
                }
                _ => None,
            })
            .collect();
        assert_eq!(usage_rows.len(), 2);
        assert!(usage_rows.iter().all(|(s, sc)| s == "m" && sc == "turn"));
    }

    // Model transport failure mid-turn: honest model_error, audited turn.
    #[test]
    fn model_failure_ends_turn_honestly() {
        let dir = tempfile::tempdir().unwrap();
        let mut client = Scripted::new(vec![Err(CallError::Transport {
            detail: "conn reset".into(),
        })]);
        let mut tools = MockTools::new(&[]);
        let mut st = store(dir.path());
        let out =
            run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &cfg()).unwrap();
        assert_eq!(out.stopped_reason, StoppedReason::ModelError);
        let st2 = store(dir.path());
        assert_eq!(st2.next_turn_id(), "t2", "failed turn is still a complete audit record");
        assert!(
            st2.events()
                .iter()
                .any(|e| matches!(e, TranscriptEvent::ModelFailed { detail, .. } if detail.contains("conn reset"))),
            "the failure reason is audited"
        );
    }

    // Mid-batch deadline exhaustion: remaining calls get timeout is_error
    // results; the batch stays COMPLETE and adjacent, then the turn times out.
    #[test]
    fn mid_batch_timeout_completes_the_result_batch() {
        let dir = tempfile::tempdir().unwrap();
        let mut client = Scripted::new(vec![tool_reply(&[("c1", "slow"), ("c2", "fast")])]);
        let mut tools = MockTools::new(&[
            ("slow", Behavior::Slow(Duration::from_millis(600))),
            ("fast", Behavior::Ok("quick")),
        ]);
        let mut st = store(dir.path());
        let mut c = cfg();
        c.deadline = Duration::from_millis(300);
        let out = run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &c).unwrap();
        assert_eq!(out.stopped_reason, StoppedReason::Timeout);
        // Both calls answered — the skipped one as a timeout error.
        let results_msg = st
            .events()
            .iter()
            .find_map(|e| match e {
                TranscriptEvent::Message { message: ModelMessage::ToolResults { results }, .. } => {
                    Some(results.clone())
                }
                _ => None,
            })
            .expect("the batch must be recorded despite the timeout");
        assert_eq!(results_msg.len(), 2);
        // The slow tool FINISHED LATE: its data is discarded for the model
        // (error result) but preserved raw in the audit row (A0: late results
        // are side-log material, never deliveries).
        assert!(results_msg[0].is_error, "late result must not be delivered as success");
        assert!(results_msg[0].content.contains("after the turn deadline"));
        assert!(results_msg[1].is_error, "skipped call must be a timeout error result");
        assert!(results_msg[1].content.contains("deadline exhausted"));
        let audit_raw = st
            .events()
            .iter()
            .find_map(|e| match e {
                TranscriptEvent::ToolCalled { tool, content, .. } if tool == "slow" => {
                    Some(content.clone())
                }
                _ => None,
            })
            .unwrap();
        assert_eq!(audit_raw, "slow done", "the late result survives in the audit");
        // The caller-facing trail must NOT leak the late content.
        assert!(out.tool_trace[0].summary.contains("discarded"));
        assert!(!out.tool_trace[0].summary.contains("slow done"));
    }

    // MaxTokens without tools: a truncated answer must not present as Final.
    #[test]
    fn max_tokens_text_is_not_final() {
        let dir = tempfile::tempdir().unwrap();
        let truncated = Ok(ModelReply {
            stop_reason: StopReason::MaxTokens,
            ..text_reply("partial ans").unwrap()
        });
        let mut client = Scripted::new(vec![truncated]);
        let mut tools = MockTools::new(&[]);
        let mut st = store(dir.path());
        let out =
            run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &cfg()).unwrap();
        assert_eq!(out.stopped_reason, StoppedReason::ModelError);
        assert_eq!(out.answer, "partial ans", "the partial text still reaches the caller");
    }

    // The terminal assistant answer is recorded — later turns project it.
    #[test]
    fn terminal_answer_recorded_for_next_turn() {
        let dir = tempfile::tempdir().unwrap();
        let mut client = Scripted::new(vec![text_reply("the answer")]);
        let mut tools = MockTools::new(&[]);
        let mut st = store(dir.path());
        run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &cfg()).unwrap();
        let proj = st.projection(1_000_000);
        assert!(
            proj.iter().any(|m| matches!(
                m,
                ModelMessage::Assistant { content } if content == "the answer"
            )),
            "multi-turn continuity requires the prior answer in the projection"
        );
    }

    // Interleaved provider blocks survive verbatim into the recorded turn.
    #[test]
    fn assistant_block_order_preserved_verbatim() {
        let dir = tempfile::tempdir().unwrap();
        let interleaved = Ok(ModelReply {
            model: "m".into(),
            text: "a b".into(),
            stop_reason: StopReason::ToolUse,
            usage: Usage { input_tokens: 1, output_tokens: 1 },
            blocks: Some(vec![
                ReplyBlock::Text { text: "a".into() },
                ReplyBlock::ToolUse {
                    id: "c1".into(),
                    name: "search".into(),
                    input: serde_json::json!({}),
                },
                ReplyBlock::Text { text: "b".into() },
                ReplyBlock::ToolUse {
                    id: "c2".into(),
                    name: "search".into(),
                    input: serde_json::json!({}),
                },
            ]),
            raw_stop_reason: None,
        });
        let mut client = Scripted::new(vec![interleaved, text_reply("done")]);
        let mut tools = MockTools::new(&[("search", Behavior::Ok("hit"))]);
        let mut st = store(dir.path());
        run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &cfg()).unwrap();
        let recorded = &client.requests[1].messages;
        let blocks = recorded
            .iter()
            .find_map(|m| match m {
                ModelMessage::AssistantBlocks { blocks } => Some(blocks.clone()),
                _ => None,
            })
            .unwrap();
        let shape: Vec<&str> = blocks
            .iter()
            .map(|b| match b {
                AssistantBlock::Text { .. } => "text",
                AssistantBlock::ToolUse { .. } => "tool",
            })
            .collect();
        assert_eq!(shape, vec!["text", "tool", "text", "tool"], "provider order is protocol");
    }

    // A model reply that lands after the deadline is LATE: usage counted,
    // discard audited, nothing delivered or executed.
    #[test]
    fn late_model_reply_is_discarded() {
        struct SlowClient(Duration);
        impl ModelClient for SlowClient {
            fn call(&mut self, _r: &ModelRequest) -> Result<ModelReply, CallError> {
                std::thread::sleep(self.0);
                text_reply("too late")
            }
        }
        let dir = tempfile::tempdir().unwrap();
        let mut client = SlowClient(Duration::from_millis(600));
        let mut tools = MockTools::new(&[("search", Behavior::Ok("hit"))]);
        let mut st = store(dir.path());
        let mut c = cfg();
        c.deadline = Duration::from_millis(300);
        let out = run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &c).unwrap();
        assert_eq!(out.stopped_reason, StoppedReason::Timeout);
        assert!(out.answer.is_empty(), "a late reply is never delivered");
        assert!(tools.calls.is_empty());
        assert!(st.events().iter().any(|e| matches!(
            e,
            TranscriptEvent::ReplyDiscarded { reason, .. } if reason.contains("after the turn deadline")
        )));
        // The spend still happened and is accounted.
        assert!(out.input_tokens_total > 0);
    }

    // A key completed by ANOTHER writer replays even while the session lock is
    // held by someone else and our store was opened before the commit.
    #[test]
    fn stale_prelock_view_still_replays_completed_key() {
        let dir = tempfile::tempdir().unwrap();
        // Store A opened EARLY (stale view).
        let mut stale = store(dir.path());
        // Another store completes a turn with key K.
        {
            let mut client = Scripted::new(vec![text_reply("done elsewhere")]);
            let mut tools = MockTools::new(&[]);
            let mut other = store(dir.path());
            run_agent_turn(&mut client, &mut tools, &mut other, "q?", Some("K"), &cfg())
                .unwrap();
        }
        // A third party currently holds the lock.
        let holder = store(dir.path());
        let _held = holder.lock().unwrap();
        // The stale store must REPLAY (refresh-before-lookup), not SessionBusy.
        let mut client = Scripted::new(vec![text_reply("SHOULD NOT RUN")]);
        let mut tools = MockTools::new(&[]);
        let out = run_agent_turn(&mut client, &mut tools, &mut stale, "q?", Some("K"), &cfg())
            .unwrap();
        assert!(out.idempotent_replay);
        assert_eq!(out.answer, "done elsewhere");
        assert!(client.requests.is_empty());
    }

    // Every on-disk line carries schema + session_id (audit rows must stay
    // versioned/correlatable when separated from the filename).
    #[test]
    fn every_line_carries_schema_and_session() {
        let dir = tempfile::tempdir().unwrap();
        let mut client = Scripted::new(vec![text_reply("x")]);
        let mut tools = MockTools::new(&[]);
        let mut st = store(dir.path());
        run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &cfg()).unwrap();
        let body = std::fs::read_to_string(dir.path().join("s1.jsonl")).unwrap();
        for line in body.lines() {
            let v: serde_json::Value = serde_json::from_str(line).unwrap();
            assert_eq!(v["schema"], "ovp.ask_transcript/v1", "{line}");
            assert_eq!(v["session_id"], "s1", "{line}");
        }
    }

    // Contradictory reply shape (tool_use blocks under a final stop) fails
    // closed instead of silently dropping the calls.
    #[test]
    fn tool_blocks_under_final_stop_fail_closed() {
        let dir = tempfile::tempdir().unwrap();
        let contradictory = Ok(ModelReply {
            model: "m".into(),
            text: "looks done".into(),
            stop_reason: StopReason::EndTurn,
            usage: Usage { input_tokens: 1, output_tokens: 1 },
            blocks: Some(vec![
                ReplyBlock::Text { text: "looks done".into() },
                ReplyBlock::ToolUse {
                    id: "c1".into(),
                    name: "search".into(),
                    input: serde_json::json!({}),
                },
            ]),
            raw_stop_reason: None,
        });
        let mut client = Scripted::new(vec![contradictory]);
        let mut tools = MockTools::new(&[("search", Behavior::Ok("hit"))]);
        let mut st = store(dir.path());
        let out =
            run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &cfg()).unwrap();
        assert_eq!(out.stopped_reason, StoppedReason::ModelError);
        assert!(tools.calls.is_empty());
        assert!(st.events().iter().any(|e| matches!(
            e,
            TranscriptEvent::ReplyDiscarded { reason, .. } if reason.contains("contradictory")
        )));
    }

    // An empty/unstamped lock file is conservatively BUSY — a racing creator
    // may sit between create_new and the pid write; stealing would let two
    // turns run concurrently.
    #[test]
    fn unstamped_lock_is_busy_not_stale() {
        let dir = tempfile::tempdir().unwrap();
        let st = store(dir.path());
        std::fs::write(dir.path().join("s1.lock"), "").unwrap();
        match st.lock() {
            Err(crate::agent_transcript::StoreError::SessionBusy { holder_pid }) => {
                assert_eq!(holder_pid, 0)
            }
            Err(other) => panic!("expected SessionBusy, got {other:?}"),
            Ok(_) => panic!("an unstamped lock must not be stolen"),
        }
    }

    // An execution failure BETWEEN invalid-args failures resets the streak:
    // the breaker fires only on CONSECUTIVE invalid-args.
    #[test]
    fn execution_failure_resets_invalid_args_streak() {
        let dir = tempfile::tempdir().unwrap();
        let mut client = Scripted::new(vec![
            tool_reply(&[("c1", "moody")]),
            tool_reply(&[("c2", "moody")]),
            tool_reply(&[("c3", "moody")]),
            text_reply("survived"),
        ]);
        let mut tools = MockTools::new(&[(
            "moody",
            Behavior::Script(vec![
                ToolOutcome::InvalidArgs("bad".into()),
                ToolOutcome::Failed("boom".into()),
                ToolOutcome::InvalidArgs("bad".into()),
            ]),
        )]);
        let mut st = store(dir.path());
        let mut c = cfg();
        c.max_rounds = 10;
        let out = run_agent_turn(&mut client, &mut tools, &mut st, "q?", None, &c).unwrap();
        assert_eq!(
            out.stopped_reason,
            StoppedReason::Final,
            "invalid → failed → invalid is NOT consecutive; the loop survives"
        );
        assert_eq!(out.answer, "survived");
    }
}
