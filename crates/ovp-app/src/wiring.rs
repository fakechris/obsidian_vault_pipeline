use std::collections::HashMap;
use std::path::{Path, PathBuf};

use ovp_core::RunId;
use ovp_domain::ConceptRegistry;
use ovp_llm::ModelClient;

/// The runtime dependencies + per-run values that cannot live in a static
/// manifest: the `run_id`, the date/area stamps, the input path, and the
/// *actual* effect objects (`ModelClient`s) and `ConceptRegistry`s that the
/// manifest binds to **by name**.
///
/// Clients and registries are stored under names so a manifest can say
/// `config = { client = "default_llm" }` and the assembler binds the live
/// object the app registered under that name. A `ModelClient` is not `Clone`,
/// so it is *taken* (moved) into its node exactly once; a `ConceptRegistry`
/// is `Clone`, so it can be shared across nodes.
pub struct AppWiring {
    run_id: RunId,
    date_stamp: String,
    area: String,
    input_path: Option<PathBuf>,
    clients: HashMap<String, Box<dyn ModelClient>>,
    registries: HashMap<String, ConceptRegistry>,
}

impl AppWiring {
    /// New wiring for a run. `date_stamp` defaults empty and `area` to `"ai"`;
    /// set them with the builder methods.
    pub fn new(run_id: RunId) -> Self {
        Self {
            run_id,
            date_stamp: String::new(),
            area: "ai".to_string(),
            input_path: None,
            clients: HashMap::new(),
            registries: HashMap::new(),
        }
    }

    pub fn with_date_stamp(mut self, date_stamp: impl Into<String>) -> Self {
        self.date_stamp = date_stamp.into();
        self
    }

    pub fn with_area(mut self, area: impl Into<String>) -> Self {
        self.area = area.into();
        self
    }

    pub fn with_input_path(mut self, path: impl Into<PathBuf>) -> Self {
        self.input_path = Some(path.into());
        self
    }

    /// Register an effect client under `name` (e.g. `"default_llm"`). The
    /// `effect.llm_invoker` node binds it via `config = { client = "..." }`.
    pub fn with_client(mut self, name: impl Into<String>, client: Box<dyn ModelClient>) -> Self {
        self.clients.insert(name.into(), client);
        self
    }

    /// Register a `ConceptRegistry` under `name` (e.g. `"default"`). The
    /// `transform.concept_resolver` node binds it via `config = { registry = "..." }`.
    pub fn with_registry(mut self, name: impl Into<String>, registry: ConceptRegistry) -> Self {
        self.registries.insert(name.into(), registry);
        self
    }

    pub fn run_id(&self) -> &RunId {
        &self.run_id
    }

    pub fn date_stamp(&self) -> &str {
        &self.date_stamp
    }

    pub fn area(&self) -> &str {
        &self.area
    }

    pub(crate) fn input_path(&self) -> Option<&Path> {
        self.input_path.as_deref()
    }

    /// Move the named client out of the wiring (bound once per assembly).
    pub(crate) fn take_client(&mut self, name: &str) -> Option<Box<dyn ModelClient>> {
        self.clients.remove(name)
    }

    pub(crate) fn registry(&self, name: &str) -> Option<&ConceptRegistry> {
        self.registries.get(name)
    }
}
