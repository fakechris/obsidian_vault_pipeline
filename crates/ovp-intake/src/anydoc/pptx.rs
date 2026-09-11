//! In-process Microsoft PowerPoint (.pptx) to GitHub Flavored Markdown (GFM) extractor.

use std::io::{Cursor, Read};
use quick_xml::events::Event;
use quick_xml::Reader;
use zip::ZipArchive;

use super::docx::render_markdown_table;
use super::{AnydocError, AnydocOptions, DocumentMetadata, OfficeFormat, ParsedDocument};

pub fn parse_pptx(bytes: &[u8], opts: &AnydocOptions) -> Result<ParsedDocument, AnydocError> {
    let reader = Cursor::new(bytes);
    let mut zip = ZipArchive::new(reader).map_err(|e| AnydocError::Zip(e.to_string()))?;

    // Find and order slide files: ppt/slides/slide{N}.xml
    let mut slide_names: Vec<(usize, String)> = Vec::new();
    for i in 0..zip.len() {
        if let Ok(file) = zip.by_index(i) {
            let name = file.name();
            if name.starts_with("ppt/slides/slide")
                && name.ends_with(".xml")
                && let Some(num_str) = name
                    .strip_prefix("ppt/slides/slide")
                    .and_then(|s| s.strip_suffix(".xml"))
                && let Ok(n) = num_str.parse::<usize>()
            {
                slide_names.push((n, name.to_string()));
            }
        }
    }
    slide_names.sort_by_key(|(n, _)| *n);

    let mut markdown = String::new();
    let mut warnings = Vec::new();
    let mut slide_count = 0;
    let mut first_slide_title = None;

    for (num, slide_name) in &slide_names {
        slide_count += 1;
        let mut xml = String::new();
        {
            let mut file = zip
                .by_name(slide_name)
                .map_err(|e| AnydocError::Zip(format!("reading {slide_name}: {e}")))?;
            file.read_to_string(&mut xml)
                .map_err(|e| AnydocError::Io(e.to_string()))?;
        }

        let (slide_title, slide_md) = convert_slide_xml(&xml, *num, opts)?;
        if first_slide_title.is_none() && !slide_title.trim().is_empty() {
            first_slide_title = Some(slide_title.clone());
        }

        if !markdown.is_empty() {
            markdown.push_str("\n\n---\n\n");
        }
        if !slide_title.is_empty() {
            markdown.push_str(&format!("## Slide {num}: {slide_title}\n\n"));
        } else {
            markdown.push_str(&format!("## Slide {num}\n\n"));
        }
        markdown.push_str(&slide_md);
    }

    if slide_names.is_empty() {
        warnings.push("No slides found in pptx archive".into());
    }

    let title = first_slide_title.unwrap_or_else(|| "Untitled Presentation".to_string());
    let word_count = markdown.split_whitespace().count();

    let meta = DocumentMetadata {
        title: Some(title.clone()),
        author: None,
        created: None,
        modified: None,
        section_count: slide_count,
        word_count,
    };

    Ok(ParsedDocument {
        title,
        markdown: markdown.trim().to_string(),
        format: OfficeFormat::Pptx,
        metadata: meta,
        warnings,
    })
}

fn convert_slide_xml(
    xml: &str,
    _slide_num: usize,
    opts: &AnydocOptions,
) -> Result<(String, String), AnydocError> {
    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(false);

    let mut slide_title = String::new();
    let mut slide_body = String::new();

    let mut in_t = false;
    let mut in_tc = false;
    let mut in_b = false;
    let mut in_i = false;

    let mut list_level: usize = 0;
    let mut current_run_text = String::new();
    let mut current_p_text = String::new();

    // Tables
    let mut table_rows: Vec<Vec<String>> = Vec::new();
    let mut current_row: Vec<String> = Vec::new();
    let mut current_cell_text = String::new();

    let mut is_title_shape = false;
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) => {
                let local = e.local_name();
                match local.as_ref() {
                    b"ph" => {
                        for attr in e.attributes().flatten() {
                            if attr.key.local_name().as_ref() == b"type" {
                                let val = String::from_utf8_lossy(&attr.value);
                                if val == "title" || val == "ctrTitle" {
                                    is_title_shape = true;
                                }
                            }
                        }
                    }
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
                        list_level = 0;
                    }
                    b"pPr" => {
                        for attr in e.attributes().flatten() {
                            if attr.key.local_name().as_ref() == b"lvl"
                                && let Ok(lvl) = String::from_utf8_lossy(&attr.value).parse::<usize>()
                            {
                                list_level = lvl;
                            }
                        }
                    }
                    b"r" => {
                        current_run_text.clear();
                        in_b = false;
                        in_i = false;
                    }
                    b"rPr" => {
                        for attr in e.attributes().flatten() {
                            let k = attr.key.local_name();
                            if k.as_ref() == b"b" && attr.value.as_ref() == b"1" {
                                in_b = true;
                            } else if k.as_ref() == b"i" && attr.value.as_ref() == b"1" {
                                in_i = true;
                            }
                        }
                    }
                    b"t" => in_t = true,
                    _ => {}
                }
            }
            Ok(Event::Empty(ref e)) => {
                let local = e.local_name();
                if local.as_ref() == b"ph" {
                    for attr in e.attributes().flatten() {
                        if attr.key.local_name().as_ref() == b"type" {
                            let val = String::from_utf8_lossy(&attr.value);
                            if val == "title" || val == "ctrTitle" {
                                is_title_shape = true;
                            }
                        }
                    }
                } else if local.as_ref() == b"pPr" {
                    for attr in e.attributes().flatten() {
                        if attr.key.local_name().as_ref() == b"lvl"
                            && let Ok(lvl) = String::from_utf8_lossy(&attr.value).parse::<usize>()
                        {
                            list_level = lvl;
                        }
                    }
                } else if local.as_ref() == b"rPr" {
                    for attr in e.attributes().flatten() {
                        let k = attr.key.local_name();
                        if k.as_ref() == b"b" && attr.value.as_ref() == b"1" {
                            in_b = true;
                        } else if k.as_ref() == b"i" && attr.value.as_ref() == b"1" {
                            in_i = true;
                        }
                    }
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
                            let formatted = if opts.preserve_formatting && (in_b || in_i) {
                                match (in_b, in_i) {
                                    (true, true) => format!("***{current_run_text}***"),
                                    (true, false) => format!("**{current_run_text}**"),
                                    (false, true) => format!("*{current_run_text}*"),
                                    (false, false) => current_run_text.clone(),
                                }
                            } else {
                                current_run_text.clone()
                            };
                            current_p_text.push_str(&formatted);
                            current_run_text.clear();
                        }
                    }
                    b"sp" => {
                        is_title_shape = false;
                    }
                    b"p" => {
                        let trimmed = current_p_text.trim();
                        if in_tc {
                            if !current_cell_text.is_empty() && !trimmed.is_empty() {
                                current_cell_text.push(' ');
                            }
                            current_cell_text.push_str(trimmed);
                        } else if !trimmed.is_empty() {
                            if is_title_shape && slide_title.is_empty() {
                                slide_title = trimmed.to_string();
                            } else {
                                let indent = "  ".repeat(list_level);
                                slide_body.push_str(&format!("{indent}- {trimmed}\n"));
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
                            slide_body.push_str(&render_markdown_table(&table_rows));
                            slide_body.push_str("\n\n");
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

    Ok((slide_title, slide_body.trim().to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Cursor, Write};
    use zip::write::SimpleFileOptions;
    use zip::ZipWriter;

    #[test]
    fn test_pptx_parsing() {
        let slide_xml = r#"<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:nvSpPr>
          <p:nvPr>
            <p:ph type="title"/>
          </p:nvPr>
        </p:nvSpPr>
        <p:txBody>
          <a:p>
            <a:r><a:t>Quarterly Strategy</a:t></a:r>
          </a:p>
        </p:txBody>
      </p:sp>
      <p:sp>
        <p:txBody>
          <a:p>
            <a:r><a:rPr b="1"/><a:t>Core Objective</a:t></a:r>
          </a:p>
          <a:p>
            <a:pPr lvl="1"/>
            <a:r><a:t>Deliver M6 roadmap</a:t></a:r>
          </a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>"#;

        let mut buf = Vec::new();
        {
            let mut zip = ZipWriter::new(Cursor::new(&mut buf));
            let opts = SimpleFileOptions::default();
            zip.start_file("ppt/slides/slide1.xml", opts).unwrap();
            zip.write_all(slide_xml.as_bytes()).unwrap();
            zip.finish().unwrap();
        }

        let opts = AnydocOptions::default();
        let parsed = parse_pptx(&buf, &opts).expect("should parse pptx");
        assert_eq!(parsed.title, "Quarterly Strategy");
        assert!(parsed.markdown.contains("## Slide 1: Quarterly Strategy"));
        assert!(parsed.markdown.contains("- **Core Objective**"));
        assert!(parsed.markdown.contains("  - Deliver M6 roadmap"));
    }
}

