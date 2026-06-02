//! OVP Next domain layer for the v1 article path.
//!
//! Defines the typed body for records flowing through the pipeline
//! (`DomainBody`) plus its four variant types. Transforms, source, and
//! sink land in C4-C6. The contract-assertion testing engine lives
//! under `testing/` behind the `testing` feature (added in C7).

pub mod body;
pub mod canonical;
pub mod canonical_slug;
pub mod concept_registry;
pub mod evergreen;
pub mod evergreen_note;
pub mod interpreted;
pub mod knowledge_index;
pub mod moc;
pub mod paper_doc;
pub mod prompt;
pub mod response;
pub mod sinks;
pub mod source_doc;
pub mod sources;
pub mod transforms;
/// M14a experimental Grounded Unit extraction spike (parallel, deletable; not
/// wired into the typed pipeline). See `docs/stage-m14a-grounded-units.md`.
pub mod units;
pub mod vault_layout;

#[cfg(feature = "testing")]
pub mod testing;

pub use body::DomainBody;
pub use canonical::{CanonicalConcept, CanonicalParseError};
pub use canonical_slug::{CanonicalSlug, SlugError};
pub use concept_registry::{ConceptRegistry, RegistryError};
pub use evergreen::EvergreenConcept;
pub use evergreen_note::{content_hash, reconcile_evergreen_write, EvergreenNote};
pub use interpreted::{
    ConceptKind, Dimensions, ExtractedConcept, Explanation, InterpretationSchema, InterpretedDoc,
};
pub use knowledge_index::{
    extract_wikilinks, KnowledgeIndex, KnowledgeIndexBuilder, KnowledgeIndexEntry,
};
pub use moc::MocBuilder;
pub use paper_doc::{PaperDoc, PaperSections};
pub use prompt::{PromptId, PromptRequest};
pub use response::{ModelResponse, ResponseContent};
pub use sinks::{ArticleVaultPlanSink, EvergreenSink, PaperVaultPlanSink};
pub use source_doc::{PaperMeta, SourceDoc, SourceKind};
pub use sources::{InboxScanSource, MarkdownInboxSource};
pub use vault_layout::VaultLayout;
pub use transforms::{
    ArticleParser, ConceptResolver, EvergreenConceptWriter, LLMInvoker, PaperParser,
    PaperPromptBuilder, PromptBuilder, RouteBySourceKind, SourceResolver, ARTICLE_PROMPT_ID,
    ARTICLE_SCHEMA_VERSION, DEFAULT_ARTICLE_MAX_TOKENS, DEFAULT_ARTICLE_MODEL,
    DEFAULT_PAPER_MAX_TOKENS, DEFAULT_PAPER_MODEL, PAPER_PROMPT_ID, PAPER_SCHEMA_VERSION,
};
