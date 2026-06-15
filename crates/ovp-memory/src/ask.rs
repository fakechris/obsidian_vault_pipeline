//! `ask` — retrieval-augmented Q&A over OVP product state.
//!
//! Pipeline: query (JSON index substring) → context assembly (reader card
//! titles + crystal claims) → LLM → cited answer.
//!
//! Ephemeral reuse surface: answers are NOT durable truth, NOT in ledger.
//! Optionally persisted to `.ovp/chats/<timestamp>.md` for session continuity.

use std::path::{Path, PathBuf};

use ovp_index::model::IndexModel;
use ovp_index::query::{run_query, Query};
use ovp_llm::{ModelClient, ModelMessage, ModelRequest};

pub struct AskArgs {
    pub question: String,
    pub max_context_hits: usize,
    pub max_tokens: u32,
    pub model_name: String,
    pub save_chat: bool,
}

impl Default for AskArgs {
    fn default() -> Self {
        Self {
            question: String::new(),
            max_context_hits: 20,
            max_tokens: 2048,
            model_name: "claude-sonnet-4-20250514".into(),
            save_chat: false,
        }
    }
}

pub struct AskResult {
    pub answer: String,
    pub context_hits: usize,
    pub chat_file: Option<PathBuf>,
}

pub fn ask(
    model: &IndexModel,
    client: &mut dyn ModelClient,
    args: &AskArgs,
    vault_root: &Path,
) -> Result<AskResult, String> {
    let query = Query { term: Some(args.question.clone()), ..Default::default() };
    let hits = run_query(model, &query);
    let context_hits = hits.len().min(args.max_context_hits);

    let mut context = String::new();
    for hit in hits.iter().take(args.max_context_hits) {
        context.push_str(&format!("[{}] {}\n", hit.kind, hit.line));
    }

    let system = "You are a knowledge assistant for OVP (Obsidian Vault Pipeline). \
        Answer questions using ONLY the provided context from the index. \
        Cite sources when possible using [kind] markers. \
        If the context doesn't contain enough information, say so.".to_string();

    let user_msg = format!(
        "Context from OVP index ({context_hits} hits):\n\n{context}\n\n---\n\nQuestion: {}",
        args.question
    );

    let request = ModelRequest {
        model: args.model_name.clone(),
        system: Some(system),
        messages: vec![ModelMessage::User { content: user_msg }],
        max_tokens: args.max_tokens,
        temperature: Some(0.4),
        cache_namespace: Some("ask/v1".into()),
    };

    let reply = client.call(&request).map_err(|e| format!("ask LLM: {e}"))?;

    let chat_file = if args.save_chat {
        let ts = chrono_like_timestamp();
        let chat_path = vault_root.join(".ovp").join("chats").join(format!("{ts}.md"));
        if let Some(parent) = chat_path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| format!("create chats dir: {e}"))?;
        }
        let chat_content = format!(
            "# Ask — {}\n\n**Q:** {}\n\n**A:** {}\n\n---\n\nContext hits: {context_hits}\n",
            ts, args.question, reply.text
        );
        std::fs::write(&chat_path, chat_content).map_err(|e| format!("write chat: {e}"))?;
        Some(chat_path)
    } else {
        None
    };

    Ok(AskResult { answer: reply.text, context_hits, chat_file })
}

fn chrono_like_timestamp() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    format!("{}", now.as_secs())
}
