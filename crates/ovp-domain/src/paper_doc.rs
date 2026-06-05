use serde::{Deserialize, Serialize};

/// The structured result of `PaperParser`. Papers have a different output
/// shape than articles (10 numbered sections vs. the 6 article
/// dimensions), so they get their own type + `DomainBody::InterpretedPaper`
/// variant rather than overloading `InterpretedDoc`.
///
/// `arxiv_id`, `authors`, `categories` come from the source's `PaperMeta`
/// (reliable frontmatter), not the LLM echo. The LLM supplies the title,
/// tags, and the ten section bodies.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PaperDoc {
    pub title: String,
    pub source_url: String,
    pub arxiv_id: String,
    pub authors: Vec<String>,
    pub categories: Vec<String>,
    /// ISO 8601 date the interpretation was produced.
    pub date: String,
    /// Paper publication date, if known (distinct from `date`).
    pub source_date: Option<String>,
    pub tags: Vec<String>,
    pub sections: PaperSections,
}

/// The ten sections of a paper deep-dive, matching the legacy template.
/// Named fields (not a map) — invariant #3. Each holds that section's
/// markdown body; the sink renders them under fixed Chinese headings.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PaperSections {
    /// 元信息
    pub metadata: String,
    /// 一句话核心贡献
    pub core_contribution: String,
    /// 研究背景与动机
    pub background: String,
    /// 方法详解
    pub method: String,
    /// 实验设计
    pub experiments: String,
    /// 核心洞察
    pub key_insights: String,
    /// 方法复现指南
    pub reproduction: String,
    /// 局限性与未来工作
    pub limitations: String,
    /// 关联研究
    pub related_work: String,
    /// 个人思考
    pub personal_notes: String,
}

impl PaperSections {
    /// The ten section headings, in render order. Used by the sink to
    /// render and by the contract engine to assert presence.
    pub const HEADINGS: [&'static str; 10] = [
        "元信息",
        "一句话核心贡献",
        "研究背景与动机",
        "方法详解",
        "实验设计",
        "核心洞察",
        "方法复现指南",
        "局限性与未来工作",
        "关联研究",
        "个人思考",
    ];

    /// Section bodies paired with their headings, in render order.
    pub fn ordered(&self) -> [(&'static str, &str); 10] {
        [
            ("元信息", &self.metadata),
            ("一句话核心贡献", &self.core_contribution),
            ("研究背景与动机", &self.background),
            ("方法详解", &self.method),
            ("实验设计", &self.experiments),
            ("核心洞察", &self.key_insights),
            ("方法复现指南", &self.reproduction),
            ("局限性与未来工作", &self.limitations),
            ("关联研究", &self.related_work),
            ("个人思考", &self.personal_notes),
        ]
    }
}
