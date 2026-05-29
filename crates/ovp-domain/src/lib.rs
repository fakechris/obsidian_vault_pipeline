//! OVP Next domain layer for the v1 article path.
//!
//! Defines the typed body for records flowing through the pipeline
//! (`DomainBody`) plus its four variant types. Transforms, source, and
//! sink land in C4-C6. The contract-assertion testing engine lives
//! under `testing/` behind the `testing` feature (added in C7).

pub mod body;
pub mod interpreted;
pub mod paper_doc;
pub mod prompt;
pub mod response;
pub mod sinks;
pub mod source_doc;
pub mod sources;
pub mod transforms;
pub mod vault_layout;

#[cfg(feature = "testing")]
pub mod testing;

pub use body::DomainBody;
pub use interpreted::{Dimensions, Explanation, InterpretedDoc};
pub use paper_doc::{PaperDoc, PaperSections};
pub use prompt::{PromptId, PromptRequest};
pub use response::{ModelResponse, ResponseContent};
pub use sinks::{ArticleVaultPlanSink, PaperVaultPlanSink};
pub use source_doc::{PaperMeta, SourceDoc, SourceKind};
pub use sources::{InboxScanSource, MarkdownInboxSource};
pub use vault_layout::VaultLayout;
pub use transforms::{
    ArticleParser, ConceptResolver, LLMInvoker, PaperParser, PaperPromptBuilder, PromptBuilder,
    RouteBySourceKind, SourceResolver, ARTICLE_PROMPT_ID, ARTICLE_SCHEMA_VERSION,
    DEFAULT_ARTICLE_MAX_TOKENS, DEFAULT_ARTICLE_MODEL, DEFAULT_PAPER_MAX_TOKENS,
    DEFAULT_PAPER_MODEL, PAPER_PROMPT_ID, PAPER_SCHEMA_VERSION,
};
