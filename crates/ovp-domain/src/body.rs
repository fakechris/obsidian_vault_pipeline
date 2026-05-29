use serde::{Deserialize, Serialize};

use crate::interpreted::InterpretedDoc;
use crate::paper_doc::PaperDoc;
use crate::prompt::PromptRequest;
use crate::response::ModelResponse;
use crate::source_doc::SourceDoc;

/// The single body type for all records flowing through the v1 article
/// pipeline. `Record<DomainBody>` is the universal envelope; each
/// transform matches the variant it expects and emits a different one.
///
/// Wrong-variant records are dropped by the receiving transform with a
/// `transform.<name>.wrong_variant` reason — the runner stays homogeneous
/// in B, so we get type-checked routing at the manifest level instead of
/// trying to make every edge typed.
/// Variants are `Box`-ed so the enum stays a small word-sized
/// discriminator regardless of which body is heaviest. `InterpretedDoc`
/// in particular carries a multi-hundred-byte Dimensions struct;
/// without boxing every Record would reserve that much space.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum DomainBody {
    Source(Box<SourceDoc>),
    Prompt(Box<PromptRequest>),
    Model(Box<ModelResponse>),
    /// Article interpretation (six dimensions).
    Interpreted(Box<InterpretedDoc>),
    /// Paper interpretation (ten sections). Distinct shape, distinct sink.
    InterpretedPaper(Box<PaperDoc>),
}

impl DomainBody {
    /// Short label for use in events, error messages, and CLI output.
    pub fn variant_name(&self) -> &'static str {
        match self {
            DomainBody::Source(_) => "source",
            DomainBody::Prompt(_) => "prompt",
            DomainBody::Model(_) => "model",
            DomainBody::Interpreted(_) => "interpreted",
            DomainBody::InterpretedPaper(_) => "interpreted_paper",
        }
    }
}
