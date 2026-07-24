//! Ask intent routing — decide *what kind of job* the user is asking for
//! before retrieval runs.
//!
//! Heuristic (no extra LLM call): pattern match on the current question plus
//! light history awareness so short follow-ups ("不记得了，你搜 vault") stay
//! on the same job as the previous turn.

use crate::ask::AskHistoryTurn;

/// What the user is trying to accomplish with this turn.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AskIntent {
    /// "What can you do / can you full-text search?" — answer about Ask itself.
    MetaCapability,
    /// "Find that article about X" — locate sources/packs, not synthesize claims.
    FindSource,
    /// Open-ended chat / brainstorm; broader recall, less pedantic tone.
    Explore,
    /// Default: grounded Q&A over claims/cards/units with citations.
    GroundedQa,
}

impl AskIntent {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::MetaCapability => "meta_capability",
            Self::FindSource => "find_source",
            Self::Explore => "explore",
            Self::GroundedQa => "grounded_qa",
        }
    }
}

/// Classify the current user turn.
pub fn classify_intent(question: &str, history: &[AskHistoryTurn]) -> AskIntent {
    let q = question.trim();
    if q.is_empty() {
        return AskIntent::GroundedQa;
    }
    let lower = q.to_lowercase();

    if looks_like_meta(&lower) {
        return AskIntent::MetaCapability;
    }

    // Short follow-ups after a find-source turn stay on find-source.
    if !history.is_empty()
        && looks_like_find_followup(&lower)
        && history
            .last()
            .is_some_and(|t| looks_like_find(&t.question.to_lowercase()) || prior_was_find(history))
    {
        return AskIntent::FindSource;
    }

    if looks_like_find(&lower) {
        return AskIntent::FindSource;
    }

    if looks_like_explore(&lower) {
        return AskIntent::Explore;
    }

    // If the prior user turns were clearly "find that article" and this turn
    // is a short clarification, keep hunting.
    if history.len() >= 1
        && q.chars().count() < 40
        && prior_was_find(history)
        && !looks_like_meta(&lower)
    {
        return AskIntent::FindSource;
    }

    AskIntent::GroundedQa
}

fn prior_was_find(history: &[AskHistoryTurn]) -> bool {
    history
        .iter()
        .rev()
        .take(3)
        .any(|t| looks_like_find(&t.question.to_lowercase()))
}

fn looks_like_meta(ql: &str) -> bool {
    // Capability / meta questions about Ask itself.
    let ability = [
        "你的能力",
        "你能不能",
        "你是否能",
        "你是否可以",
        "你可以吗",
        "能不能检索",
        "能否检索",
        "是否能够",
        "你作为",
        "你做为",
        "作为 ai",
        "作为ai",
        "你能搜索",
        "你能全文",
        "还是只能",
        "只能在",
        "只能基于",
        "can you search",
        "can you full",
        "what can you",
        "your capability",
        "your capabilities",
        "are you able",
        "do you only",
        "only based on memory",
        "full-text",
        "full text",
        "全文 rag",
        "全文rag",
        "全文检索",
        "全文搜索",
        "搜索vault全文",
        "vault全文",
    ];
    if ability.iter().any(|p| ql.contains(p)) {
        return true;
    }
    // "你能…吗" about search/retrieve without asking for a specific doc.
    if (ql.contains("你能") || ql.contains("你会"))
        && (ql.contains("搜索") || ql.contains("检索") || ql.contains("search") || ql.contains("rag"))
        && !ql.contains("帮我找")
    {
        return true;
    }
    false
}

fn looks_like_find(ql: &str) -> bool {
    let patterns = [
        "帮我找",
        "找一下",
        "找一篇",
        "找那篇",
        "找到那",
        "这篇文章",
        "那篇文章",
        "有一篇文章",
        "有没有一篇",
        "哪篇文章",
        "定位一下",
        "搜一下这",
        "帮我搜",
        "find the article",
        "find that article",
        "find the post",
        "find an article",
        "locate the",
        "looking for an article",
        "looking for a post",
        "which article",
        "那篇讲",
        "之前有一篇",
    ];
    patterns.iter().any(|p| ql.contains(p))
}

fn looks_like_find_followup(ql: &str) -> bool {
    let patterns = [
        "不记得",
        "想不起来",
        "忘了",
        "用vault",
        "搜vault",
        "搜索vault",
        "在vault",
        "再找",
        "再搜",
        "继续找",
        "全文",
        "自己搜",
        "你搜",
        "帮我搜",
        "search the vault",
        "search vault",
        "try the vault",
        "use the vault",
        "don't remember",
        "dont remember",
        "i forgot",
    ];
    patterns.iter().any(|p| ql.contains(p))
}

fn looks_like_explore(ql: &str) -> bool {
    let patterns = [
        "聊聊",
        "随便聊",
        "你觉得",
        "怎么看",
        "有什么想法",
        "讨论一下",
        "brainstorm",
        "what do you think",
        "curious about",
        "tell me about",
        "漫谈",
    ];
    patterns.iter().any(|p| ql.contains(p))
}

/// Strip task-wrapper phrases so lexical search focuses on content tokens
/// ("帮我找一下讲 Transformer 的文章" → "讲 Transformer 的文章").
pub fn content_query_for_find(question: &str) -> String {
    let mut q = question.to_string();
    const STRIP: &[&str] = &[
        "帮我找一下",
        "帮我找一找",
        "帮我找",
        "请帮我找",
        "找一下",
        "找一找",
        "这篇文章帮我找一下",
        "这篇文章",
        "那篇文章",
        "之前有一篇文章",
        "之前有一篇",
        "有一篇文章",
        "就是讲",
        "帮我搜一下",
        "帮我搜",
        "please find",
        "find me",
        "find the article",
        "find that article",
        "looking for",
    ];
    for p in STRIP {
        // Byte-index replace; all patterns are valid UTF-8 substrings.
        while let Some(idx) = q.find(p) {
            q = format!("{} {}", &q[..idx], &q[idx + p.len()..]);
        }
        // ASCII wrappers also match case-insensitively.
        if p.is_ascii() {
            loop {
                let lower = q.to_lowercase();
                let p_low = p.to_lowercase();
                let Some(idx) = lower.find(&p_low) else { break };
                q = format!("{} {}", &q[..idx], &q[idx + p.len()..]);
            }
        }
    }
    q.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Honest capability blurb — no vault RAG. Language follows the question.
pub fn meta_capability_answer(question: &str) -> String {
    let zh = question.chars().any(|c| {
        matches!(
            c as u32,
            0x4E00..=0x9FFF | 0x3400..=0x4DBF | 0xF900..=0xFAFF
        )
    });
    if zh {
        r#"## 我作为 Ask 现在能做什么（直接说明，不查 vault）

我是 vault 上的助手，会先判断你的任务类型，再选检索面：

| 任务 | 我会怎么做 |
|---|---|
| **找资料 / 找文章** | 在资料库的**源标题、URL、路径、阅读包、相关片段**里找，尽量给你可点开的源链接 |
| **知识问答（我们信什么）** | 在**结晶主张 / 卡片 / 单元**证据索引里检索，回答带可核查引用 |
| **闲聊 / 探索** | 更宽的召回 + 更口语的回答，允许说「不确定」 |
| **问我自己的能力** | 像现在这样直接说明，**不会**把 vault 里关于 RAG 的文章当成答案 |

### 边界（诚实）

- 找文章时：优先看索引里的源元数据与已切出来的 unit/card；**未必**等同于扫遍每一篇 md 的每一个字节。
- 知识问答时：只基于证据索引切片，**不是**无界全文自由读写。
- 我**不会**编造 vault 里不存在的文章或引用。

如果你是要**找某篇具体文章**，直接描述特征（主题、人名、关键词、大概时间）即可；如果是**问知识库主张**，直接提问就好。"#
        .to_string()
    } else {
        r#"## What I can do as Ask (about me — not from vault evidence)

I route each turn by job type, then search differently:

| Job | What I do |
|---|---|
| **Find a source / article** | Search **source titles, URLs, paths, reader packs, related excerpts** and point you at openable library links |
| **Grounded knowledge Q&A** | Retrieve **durable claims / cards / units** and answer with checkable citations |
| **Explore / chat** | Broader recall, more conversational tone; uncertainty is OK |
| **Ask about my capabilities** | Answer directly like this — I **won't** treat vault articles *about* RAG as the answer |

### Limits

- Find-source is strong on titles/metadata and indexed excerpts — not a guarantee of full-byte scans of every markdown file.
- Grounded Q&A uses the evidence index slices, not unbounded full-text freestyle.
- I won't invent articles or citations that aren't in the vault.

For a **specific article hunt**, describe distinctive clues; for **what the knowledge base believes**, just ask."#
        .to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ask::AskHistoryTurn;

    #[test]
    fn classifies_find_article() {
        let q = "之前有一篇文章，就是讲一个知名的毕业生，他通过学习把 Attention 和 Transformer 完全自己写了一遍，帮我找一下。";
        assert_eq!(classify_intent(q, &[]), AskIntent::FindSource);
    }

    #[test]
    fn classifies_meta_capability() {
        assert_eq!(
            classify_intent("你能搜索vault全文吗，还是只能在 Memory 这层搜索", &[]),
            AskIntent::MetaCapability
        );
        assert_eq!(
            classify_intent(
                "我是问你的能力，你作为 AI ask 是否能够检索到全文",
                &[]
            ),
            AskIntent::MetaCapability
        );
    }

    #[test]
    fn followup_stays_on_find() {
        let history = vec![AskHistoryTurn {
            question: "帮我找那篇手写 Transformer 被 OpenAI 录取的文章".into(),
            answer: "没找到…".into(),
        }];
        assert_eq!(
            classify_intent("不记得了，你可以用vault", &history),
            AskIntent::FindSource
        );
    }

    #[test]
    fn grounded_qa_default() {
        assert_eq!(
            classify_intent("知识库对 agent memory 有什么主张？", &[]),
            AskIntent::GroundedQa
        );
    }

    #[test]
    fn explore_tone() {
        assert_eq!(
            classify_intent("聊聊你对上下文工程的看法", &[]),
            AskIntent::Explore
        );
    }

    #[test]
    fn content_query_strips_wrappers() {
        let q = content_query_for_find("帮我找一下讲 Transformer 和简历的文章");
        assert!(q.contains("Transformer"));
        assert!(!q.contains("帮我找"));
    }
}
