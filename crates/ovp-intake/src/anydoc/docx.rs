//! In-process Microsoft Word (.docx) to GitHub Flavored Markdown (GFM) extractor.

use std::io::{Cursor, Read};
use quick_xml::events::Event;
use quick_xml::Reader;
use zip::ZipArchive;

use super::{AnydocError, AnydocOptions, DocumentMetadata, OfficeFormat, ParsedDocument};

pub fn parse_docx(bytes: &[u8], opts: &AnydocOptions) -> Result<ParsedDocument, AnydocError> {
    let reader = Cursor::new(bytes);
    let mut zip = ZipArchive::new(reader).map_err(|e| AnydocError::Zip(e.to_string()))?;

    let metadata = extract_core_properties(&mut zip);
    let mut doc_xml = String::new();
    {
        let mut file = zip
            .by_name("word/document.xml")
            .map_err(|e| AnydocError::Zip(format!("missing word/document.xml: {e}")))?;
        file.read_to_string(&mut doc_xml)
            .map_err(|e| AnydocError::Io(e.to_string()))?;
    }

    let (markdown, warnings) = convert_document_xml(&doc_xml, opts)?;
    let title = metadata
        .title
        .clone()
        .filter(|t| !t.trim().is_empty())
        .unwrap_or_else(|| {
            // Fall back to first heading or first line
            for line in markdown.lines() {
                let trimmed = line.trim();
                if let Some(h) = trimmed.strip_prefix('#') {
                    return h.trim_start_matches('#').trim().to_string();
                } else if !trimmed.is_empty() && !trimmed.starts_with('|') {
                    return trimmed.chars().take(60).collect();
                }
            }
            "Untitled Document".to_string()
        });

    let word_count = markdown.split_whitespace().count();
    let mut meta = metadata;
    meta.word_count = word_count;

    Ok(ParsedDocument {
        title,
        markdown,
        format: OfficeFormat::Docx,
        metadata: meta,
        warnings,
    })
}

fn extract_core_properties<R: std::io::Read + std::io::Seek>(
    zip: &mut ZipArchive<R>,
) -> DocumentMetadata {
    let mut meta = DocumentMetadata::default();
    let Ok(mut file) = zip.by_name("docProps/core.xml") else {
        return meta;
    };
    let mut content = String::new();
    if file.read_to_string(&mut content).is_err() {
        return meta;
    }

    let mut reader = Reader::from_str(&content);
    reader.config_mut().trim_text(true);
    let mut current_tag = String::new();

    let mut buf = Vec::new();
    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => {
                let local_name = String::from_utf8_lossy(e.local_name().as_ref()).to_string();
                current_tag = local_name;
            }
            Ok(Event::Text(e)) => {
                let text = e.unescape().unwrap_or_default().trim().to_string();
                if !text.is_empty() {
                    match current_tag.as_str() {
                        "title" => meta.title = Some(text),
                        "creator" => meta.author = Some(text),
                        "created" => meta.created = Some(text),
                        "modified" => meta.modified = Some(text),
                        _ => {}
                    }
                }
            }
            Ok(Event::End(_)) => {
                current_tag.clear();
            }
            Ok(Event::Eof) | Err(_) => break,
            _ => {}
        }
        buf.clear();
    }
    meta
}

fn convert_document_xml(xml: &str, opts: &AnydocOptions) -> Result<(String, Vec<String>), AnydocError> {
    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(false);

    let mut out = String::new();
    let warnings = Vec::new();

    let mut in_t = false;
    let mut in_tc = false;
    let mut in_b = false;
    let mut in_i = false;

    let mut current_heading_level: Option<usize> = None;
    let mut current_list_item = false;
    let mut current_run_text = String::new();
    let mut current_p_text = String::new();

    // Table state
    let mut table_rows: Vec<Vec<String>> = Vec::new();
    let mut current_row: Vec<String> = Vec::new();
    let mut current_cell_text = String::new();

    let mut buf = Vec::new();
    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) => {
                let local = e.local_name();
                match local.as_ref() {
                    b"tbl" => {
                        table_rows.clear();
                    }
                    b"tr" => {
                        current_row.clear();
                    }
                    b"tc" => {
                        in_tc = true;
                        current_cell_text.clear();
                    }
                    b"p" => {
                        current_p_text.clear();
                        current_heading_level = None;
                        current_list_item = false;
                    }
                    b"pStyle" => {
                        for attr in e.attributes().flatten() {
                            if attr.key.local_name().as_ref() == b"val" {
                                let val = String::from_utf8_lossy(&attr.value);
                                if let Some(lvl) = parse_heading_level(&val) {
                                    current_heading_level = Some(lvl);
                                }
                            }
                        }
                    }
                    b"numPr" => {
                        current_list_item = true;
                    }
                    b"r" => {
                        current_run_text.clear();
                        in_b = false;
                        in_i = false;
                    }
                    b"b" => in_b = true,
                    b"i" => in_i = true,
                    b"t" => in_t = true,
                    _ => {}
                }
            }
            Ok(Event::Empty(ref e)) => {
                let local = e.local_name();
                match local.as_ref() {
                    b"pStyle" => {
                        for attr in e.attributes().flatten() {
                            if attr.key.local_name().as_ref() == b"val" {
                                let val = String::from_utf8_lossy(&attr.value);
                                if let Some(lvl) = parse_heading_level(&val) {
                                    current_heading_level = Some(lvl);
                                }
                            }
                        }
                    }
                    b"numPr" => {
                        current_list_item = true;
                    }
                    b"b" => in_b = true,
                    b"i" => in_i = true,
                    b"br" => {
                        current_p_text.push('\n');
                    }
                    _ => {}
                }
            }
            Ok(Event::Text(ref e)) => {
                if in_t {
                    let text = e.unescape().unwrap_or_default().to_string();
                    current_run_text.push_str(&text);
                }
            }
            Ok(Event::End(ref e)) => {
                let local = e.local_name();
                match local.as_ref() {
                    b"t" => in_t = false,
                    b"r" => {
                        if !current_run_text.is_empty() {
                            let formatted = if opts.preserve_formatting {
                                format_run(&current_run_text, in_b, in_i)
                            } else {
                                current_run_text.clone()
                            };
                            current_p_text.push_str(&formatted);
                            current_run_text.clear();
                        }
                    }
                    b"p" => {
                        let trimmed = current_p_text.trim();
                        if in_tc {
                            if !current_cell_text.is_empty() && !trimmed.is_empty() {
                                current_cell_text.push(' ');
                            }
                            current_cell_text.push_str(trimmed);
                        } else if !trimmed.is_empty() {
                            if let Some(lvl) = current_heading_level {
                                let prefix = "#".repeat(lvl.min(6));
                                out.push_str(&format!("\n{prefix} {trimmed}\n\n"));
                            } else if current_list_item {
                                out.push_str(&format!("- {trimmed}\n"));
                            } else {
                                out.push_str(trimmed);
                                out.push_str("\n\n");
                            }
                        }
                        current_p_text.clear();
                    }
                    b"tc" => {
                        in_tc = false;
                        let cell_val = current_cell_text.trim().replace('\n', " ").replace('|', "\\|");
                        current_row.push(cell_val);
                        current_cell_text.clear();
                    }
                    b"tr" => {
                        if !current_row.is_empty() {
                            table_rows.push(current_row.clone());
                            current_row.clear();
                        }
                    }
                    b"tbl" => {
                        if opts.extract_tables && !table_rows.is_empty() {
                            out.push_str(&render_markdown_table(&table_rows));
                            out.push_str("\n\n");
                        }
                        table_rows.clear();
                    }
                    _ => {}
                }
            }
            Ok(Event::Eof) => break,
            Err(e) => return Err(AnydocError::Xml(e.to_string())),
            _ => {}
        }
        buf.clear();
    }

    Ok((out.trim().to_string(), warnings))
}

fn parse_heading_level(val: &str) -> Option<usize> {
    let lower = val.to_ascii_lowercase();
    if lower == "heading1" || lower == "heading 1" || lower == "title" {
        Some(1)
    } else if lower == "heading2" || lower == "heading 2" || lower == "subtitle" {
        Some(2)
    } else if lower == "heading3" || lower == "heading 3" {
        Some(3)
    } else if lower == "heading4" || lower == "heading 4" {
        Some(4)
    } else if lower == "heading5" || lower == "heading 5" {
        Some(5)
    } else if lower == "heading6" || lower == "heading 6" {
        Some(6)
    } else {
        None
    }
}

fn format_run(text: &str, bold: bool, italic: bool) -> String {
    if text.trim().is_empty() {
        return text.to_string();
    }
    match (bold, italic) {
        (true, true) => format!("***{text}***"),
        (true, false) => format!("**{text}**"),
        (false, true) => format!("*{text}*"),
        (false, false) => text.to_string(),
    }
}

pub(crate) fn render_markdown_table(rows: &[Vec<String>]) -> String {
    if rows.is_empty() {
        return String::new();
    }
    let col_count = rows.iter().map(|r| r.len()).max().unwrap_or(0);
    if col_count == 0 {
        return String::new();
    }

    let mut out = String::new();

    // Header row
    let header = &rows[0];
    out.push('|');
    for c in 0..col_count {
        let val = header.get(c).map(|s| s.as_str()).unwrap_or("");
        out.push(' ');
        out.push_str(val);
        out.push_str(" |");
    }
    out.push('\n');

    // Separator row
    out.push('|');
    for _ in 0..col_count {
        out.push_str(" --- |");
    }
    out.push('\n');

    // Data rows
    for row in rows.iter().skip(1) {
        out.push('|');
        for c in 0..col_count {
            let val = row.get(c).map(|s| s.as_str()).unwrap_or("");
            out.push(' ');
            out.push_str(val);
            out.push_str(" |");
        }
        out.push('\n');
    }

    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Cursor, Write};
    use zip::write::SimpleFileOptions;
    use zip::ZipWriter;

    #[test]
    fn test_docx_parsing() {
        let doc_xml = r#"<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>Project Title</w:t></w:r>
    </w:p>
    <w:p>
      <w:r><w:rPr><w:b/></w:rPr><w:t>Bold intro text</w:t></w:r>
      <w:r><w:t> with regular text.</w:t></w:r>
    </w:p>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Header A</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Header B</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Cell 1</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Cell 2</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>"#;

        let mut buf = Vec::new();
        {
            let mut zip = ZipWriter::new(Cursor::new(&mut buf));
            let opts = SimpleFileOptions::default();
            zip.start_file("word/document.xml", opts).unwrap();
            zip.write_all(doc_xml.as_bytes()).unwrap();
            zip.finish().unwrap();
        }

        let opts = AnydocOptions::default();
        let parsed = parse_docx(&buf, &opts).expect("should parse docx");
        assert_eq!(parsed.title, "Project Title");
        assert!(parsed.markdown.contains("# Project Title"));
        assert!(parsed.markdown.contains("**Bold intro text** with regular text."));
        assert!(parsed.markdown.contains("| Header A | Header B |"));
        assert!(parsed.markdown.contains("| Cell 1 | Cell 2 |"));
    }
}

