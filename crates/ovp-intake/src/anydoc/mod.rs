//! In-process office and document extraction engine (`anydoc`), inspired by
//! Tencent WeKnora's architecture.
//!
//! Provides pure-Rust in-process extraction for Microsoft Office (`.docx`,
//! `.pptx`, `.xlsx`) and Adobe PDF (`.pdf`), converting rich document structures
//! (headings, paragraphs, bullet lists, markdown tables, slides, worksheets)
//! into clean GitHub-Flavored Markdown (GFM).
//!
//! # Safety & Resource Governance
//! Adheres to the WeKnora defense pattern against malformed documents and
//! decompression bombs:
//! - Explicit file size boundaries (`max_file_size_bytes`)
//! - Bounded PDF operator traversal (`max_pdf_ops`) to defeat $O(n^2)$ cyclic backtracks
//! - Bounded PDF stream expansion and decompression recursion limits
//! - Zero external subprocess execution (no LibreOffice, Python, or external daemon)

pub mod docx;
pub mod pdf;
pub mod pptx;
pub mod xlsx;

use std::fmt;
use std::path::Path;

/// Supported office and document formats.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum OfficeFormat {
    Docx,
    Pptx,
    Xlsx,
    Pdf,
    Unknown,
}

impl OfficeFormat {
    pub fn from_extension(ext: &str) -> Self {
        match ext.to_ascii_lowercase().as_str() {
            "docx" => Self::Docx,
            "pptx" => Self::Pptx,
            "xlsx" => Self::Xlsx,
            "pdf" => Self::Pdf,
            _ => Self::Unknown,
        }
    }

    pub fn from_path(path: &Path) -> Self {
        path.extension()
            .and_then(|e| e.to_str())
            .map(Self::from_extension)
            .unwrap_or(Self::Unknown)
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Docx => "docx",
            Self::Pptx => "pptx",
            Self::Xlsx => "xlsx",
            Self::Pdf => "pdf",
            Self::Unknown => "unknown",
        }
    }

    pub fn is_supported(&self) -> bool {
        !matches!(self, Self::Unknown)
    }
}

impl fmt::Display for OfficeFormat {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.as_str())
    }
}

/// Metadata extracted from document properties (core.xml / PDF info dict).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DocumentMetadata {
    pub title: Option<String>,
    pub author: Option<String>,
    pub created: Option<String>,
    pub modified: Option<String>,
    pub section_count: usize,
    pub word_count: usize,
}

/// The extracted document in GFM Markdown format.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParsedDocument {
    pub title: String,
    pub markdown: String,
    pub format: OfficeFormat,
    pub metadata: DocumentMetadata,
    pub warnings: Vec<String>,
}

/// Configuration and safety bounds for the extraction engine.
#[derive(Debug, Clone)]
pub struct AnydocOptions {
    /// Maximum allowed file size in bytes (default: 64 MB).
    pub max_file_size_bytes: usize,
    /// Maximum allowed PDF pages to process (default: 500).
    pub max_pdf_pages: usize,
    /// Maximum allowed PDF content stream operators to interpret (default: 100,000).
    /// Defense against algorithmic complexity attacks / malformed TJ recursion.
    pub max_pdf_ops: usize,
    /// Whether to extract tables into GFM markdown table format (default: true).
    pub extract_tables: bool,
    /// Whether to preserve basic bold/italic inline formatting (default: true).
    pub preserve_formatting: bool,
}

impl Default for AnydocOptions {
    fn default() -> Self {
        Self {
            max_file_size_bytes: 64 * 1024 * 1024,
            max_pdf_pages: 500,
            max_pdf_ops: 100_000,
            extract_tables: true,
            preserve_formatting: true,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AnydocError {
    Io(String),
    Zip(String),
    Xml(String),
    Pdf(String),
    UnsupportedFormat(String),
    FileTooLarge { size: usize, limit: usize },
    BudgetExceeded(String),
}

impl fmt::Display for AnydocError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(msg) => write!(f, "I/O error: {msg}"),
            Self::Zip(msg) => write!(f, "Zip archive error: {msg}"),
            Self::Xml(msg) => write!(f, "XML parsing error: {msg}"),
            Self::Pdf(msg) => write!(f, "PDF parsing error: {msg}"),
            Self::UnsupportedFormat(ext) => write!(f, "Unsupported document format: {ext}"),
            Self::FileTooLarge { size, limit } => {
                write!(f, "File size {size} bytes exceeds limit {limit} bytes")
            }
            Self::BudgetExceeded(msg) => write!(f, "Resource budget exceeded: {msg}"),
        }
    }
}

impl std::error::Error for AnydocError {}

/// Ingestion trait for office and document extractors.
pub trait OfficeIngestor {
    fn can_ingest(&self, path: &Path) -> bool;
    fn ingest_file(&self, path: &Path, opts: &AnydocOptions) -> Result<ParsedDocument, AnydocError>;
    fn ingest_bytes(
        &self,
        bytes: &[u8],
        format: OfficeFormat,
        opts: &AnydocOptions,
    ) -> Result<ParsedDocument, AnydocError>;
}

/// Unified in-process document extraction engine.
#[derive(Debug, Clone, Default)]
pub struct AnydocEngine;

impl AnydocEngine {
    pub fn new() -> Self {
        Self
    }

    pub fn ingest_file(&self, path: &Path, opts: &AnydocOptions) -> Result<ParsedDocument, AnydocError> {
        <Self as OfficeIngestor>::ingest_file(self, path, opts)
    }

    pub fn ingest_bytes(
        &self,
        bytes: &[u8],
        format: OfficeFormat,
        opts: &AnydocOptions,
    ) -> Result<ParsedDocument, AnydocError> {
        <Self as OfficeIngestor>::ingest_bytes(self, bytes, format, opts)
    }

    /// Formats the parsed document as standardized 01-Raw Markdown content
    /// with YAML frontmatter, ready to be ingested by the reader pipeline.
    pub fn format_as_raw_markdown(
        parsed: &ParsedDocument,
        source_rel_path: &str,
        date: &str,
    ) -> String {
        let title_clean = parsed.title.replace('"', "\\\"");
        let author_line = parsed
            .metadata
            .author
            .as_deref()
            .map(|a| format!("author: \"{}\"\n", a.replace('"', "\\\"")))
            .unwrap_or_default();

        let mut out = String::with_capacity(parsed.markdown.len() + 256);
        out.push_str("---\n");
        out.push_str(&format!("title: \"{title_clean}\"\n"));
        out.push_str(&format!("source_file: \"{source_rel_path}\"\n"));
        out.push_str(&format!("format: \"{}\"\n", parsed.format));
        out.push_str(&author_line);
        out.push_str("tags:\n");
        out.push_str("  - ovp/office\n");
        out.push_str(&format!("  - format/{}\n", parsed.format));
        out.push_str(&format!("extracted_date: \"{date}\"\n"));
        out.push_str("---\n\n");
        out.push_str(&parsed.markdown);
        if !parsed.markdown.ends_with('\n') {
            out.push('\n');
        }
        out
    }
}

impl OfficeIngestor for AnydocEngine {
    fn can_ingest(&self, path: &Path) -> bool {
        OfficeFormat::from_path(path).is_supported()
    }

    fn ingest_file(&self, path: &Path, opts: &AnydocOptions) -> Result<ParsedDocument, AnydocError> {
        let format = OfficeFormat::from_path(path);
        if !format.is_supported() {
            return Err(AnydocError::UnsupportedFormat(
                path.extension()
                    .and_then(|e| e.to_str())
                    .unwrap_or("none")
                    .to_string(),
            ));
        }

        let metadata = std::fs::metadata(path).map_err(|e| AnydocError::Io(e.to_string()))?;
        let size = metadata.len() as usize;
        if size > opts.max_file_size_bytes {
            return Err(AnydocError::FileTooLarge {
                size,
                limit: opts.max_file_size_bytes,
            });
        }

        let bytes = std::fs::read(path).map_err(|e| AnydocError::Io(e.to_string()))?;
        let mut doc = self.ingest_bytes(&bytes, format, opts)?;
        if doc.title.trim().is_empty()
            && let Some(stem) = path.file_stem().and_then(|s| s.to_str())
        {
            doc.title = stem.to_string();
        }
        Ok(doc)
    }

    fn ingest_bytes(
        &self,
        bytes: &[u8],
        format: OfficeFormat,
        opts: &AnydocOptions,
    ) -> Result<ParsedDocument, AnydocError> {
        if bytes.len() > opts.max_file_size_bytes {
            return Err(AnydocError::FileTooLarge {
                size: bytes.len(),
                limit: opts.max_file_size_bytes,
            });
        }

        match format {
            OfficeFormat::Docx => docx::parse_docx(bytes, opts),
            OfficeFormat::Pptx => pptx::parse_pptx(bytes, opts),
            OfficeFormat::Xlsx => xlsx::parse_xlsx(bytes, opts),
            OfficeFormat::Pdf => pdf::parse_pdf(bytes, opts),
            OfficeFormat::Unknown => Err(AnydocError::UnsupportedFormat("unknown".into())),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_format_detection() {
        assert_eq!(OfficeFormat::from_path(Path::new("test.docx")), OfficeFormat::Docx);
        assert_eq!(OfficeFormat::from_path(Path::new("TEST.DOCX")), OfficeFormat::Docx);
        assert_eq!(OfficeFormat::from_path(Path::new("deck.pptx")), OfficeFormat::Pptx);
        assert_eq!(OfficeFormat::from_path(Path::new("sheet.xlsx")), OfficeFormat::Xlsx);
        assert_eq!(OfficeFormat::from_path(Path::new("paper.pdf")), OfficeFormat::Pdf);
        assert_eq!(OfficeFormat::from_path(Path::new("notes.md")), OfficeFormat::Unknown);
        assert!(OfficeFormat::Docx.is_supported());
        assert!(!OfficeFormat::Unknown.is_supported());
    }

    #[test]
    fn test_format_as_raw_markdown() {
        let doc = ParsedDocument {
            title: "Executive Summary".into(),
            markdown: "# Executive Summary\n\nKey takeaways from the quarterly review.".into(),
            format: OfficeFormat::Docx,
            metadata: DocumentMetadata {
                title: Some("Executive Summary".into()),
                author: Some("Jane Doe".into()),
                created: None,
                modified: None,
                section_count: 1,
                word_count: 10,
            },
            warnings: Vec::new(),
        };

        let raw = AnydocEngine::format_as_raw_markdown(&doc, "Clippings/report.docx", "2026-06-09");
        assert!(raw.starts_with("---\n"));
        assert!(raw.contains("title: \"Executive Summary\"\n"));
        assert!(raw.contains("source_file: \"Clippings/report.docx\"\n"));
        assert!(raw.contains("format: \"docx\"\n"));
        assert!(raw.contains("author: \"Jane Doe\"\n"));
        assert!(raw.contains("tags:\n  - ovp/office\n  - format/docx\n"));
        assert!(raw.contains("extracted_date: \"2026-06-09\"\n"));
        assert!(raw.ends_with("Key takeaways from the quarterly review.\n"));
    }

    #[test]
    fn test_file_too_large_rejection() {
        let bytes = vec![0u8; 1024];
        let opts = AnydocOptions { max_file_size_bytes: 512, ..Default::default() };

        let res = AnydocEngine::new().ingest_bytes(&bytes, OfficeFormat::Docx, &opts);
        assert!(matches!(res, Err(AnydocError::FileTooLarge { size: 1024, limit: 512 })));
    }
}

