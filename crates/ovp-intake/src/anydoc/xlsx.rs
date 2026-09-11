//! In-process Microsoft Excel (.xlsx) to GitHub Flavored Markdown (GFM) extractor.

use std::collections::BTreeMap;
use std::io::{Cursor, Read};
use quick_xml::events::Event;
use quick_xml::Reader;
use zip::ZipArchive;

use super::docx::render_markdown_table;
use super::{AnydocError, AnydocOptions, DocumentMetadata, OfficeFormat, ParsedDocument};

pub fn parse_xlsx(bytes: &[u8], opts: &AnydocOptions) -> Result<ParsedDocument, AnydocError> {
    let reader = Cursor::new(bytes);
    let mut zip = ZipArchive::new(reader).map_err(|e| AnydocError::Zip(e.to_string()))?;

    let shared_strings = extract_shared_strings(&mut zip)?;
    let mut sheet_names = extract_sheet_names(&mut zip)?;
    if sheet_names.is_empty() {
        for i in 0..zip.len() {
            if let Ok(file) = zip.by_index(i) {
                let name = file.name().to_string();
                if name.starts_with("xl/worksheets/sheet") && name.ends_with(".xml") {
                    let num_part = name
                        .trim_start_matches("xl/worksheets/sheet")
                        .trim_end_matches(".xml");
                    if let Ok(idx) = num_part.parse::<usize>() {
                        sheet_names.push((idx, format!("Sheet {idx}")));
                    }
                }
            }
        }
        sheet_names.sort_by_key(|(idx, _)| *idx);
    }

    let mut markdown = String::new();
    let mut warnings = Vec::new();
    let mut sheet_count = 0;

    for (sheet_idx, sheet_name) in &sheet_names {
        sheet_count += 1;
        let file_path = format!("xl/worksheets/sheet{sheet_idx}.xml");
        let mut xml = String::new();
        {
            let Ok(mut file) = zip.by_name(&file_path) else {
                continue;
            };
            file.read_to_string(&mut xml)
                .map_err(|e| AnydocError::Io(e.to_string()))?;
        }

        let sheet_table = parse_worksheet(&xml, &shared_strings)?;
        if !sheet_table.is_empty() {
            if !markdown.is_empty() {
                markdown.push_str("\n\n---\n\n");
            }
            markdown.push_str(&format!("## Sheet: {sheet_name}\n\n"));
            if opts.extract_tables {
                markdown.push_str(&render_markdown_table(&sheet_table));
            } else {
                for row in sheet_table {
                    markdown.push_str(&format!("- {}\n", row.join(" | ")));
                }
            }
        }
    }

    if sheet_count == 0 {
        warnings.push("No worksheets found in xlsx archive".into());
    }

    let title = sheet_names
        .first()
        .map(|(_, name)| format!("Spreadsheet ({name})"))
        .unwrap_or_else(|| "Untitled Spreadsheet".to_string());

    let word_count = markdown.split_whitespace().count();
    let meta = DocumentMetadata {
        title: Some(title.clone()),
        author: None,
        created: None,
        modified: None,
        section_count: sheet_count,
        word_count,
    };

    Ok(ParsedDocument {
        title,
        markdown: markdown.trim().to_string(),
        format: OfficeFormat::Xlsx,
        metadata: meta,
        warnings,
    })
}

fn extract_shared_strings<R: std::io::Read + std::io::Seek>(
    zip: &mut ZipArchive<R>,
) -> Result<Vec<String>, AnydocError> {
    let mut strings = Vec::new();
    let Ok(mut file) = zip.by_name("xl/sharedStrings.xml") else {
        return Ok(strings);
    };

    let mut xml = String::new();
    file.read_to_string(&mut xml)
        .map_err(|e| AnydocError::Io(e.to_string()))?;

    let mut reader = Reader::from_str(&xml);
    reader.config_mut().trim_text(false);

    let mut in_t = false;
    let mut current_str = String::new();
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) => {
                if e.local_name().as_ref() == b"t" {
                    in_t = true;
                }
            }
            Ok(Event::Text(ref e)) => {
                if in_t {
                    let text = e.unescape().unwrap_or_default().to_string();
                    current_str.push_str(&text);
                }
            }
            Ok(Event::End(ref e)) => {
                let local = e.local_name();
                if local.as_ref() == b"t" {
                    in_t = false;
                } else if local.as_ref() == b"si" {
                    strings.push(current_str.trim().to_string());
                    current_str.clear();
                }
            }
            Ok(Event::Eof) => break,
            Err(e) => return Err(AnydocError::Xml(e.to_string())),
            _ => {}
        }
        buf.clear();
    }

    Ok(strings)
}

fn extract_sheet_names<R: std::io::Read + std::io::Seek>(
    zip: &mut ZipArchive<R>,
) -> Result<Vec<(usize, String)>, AnydocError> {
    let mut sheets = Vec::new();
    let Ok(mut file) = zip.by_name("xl/workbook.xml") else {
        return Ok(sheets);
    };

    let mut xml = String::new();
    file.read_to_string(&mut xml)
        .map_err(|e| AnydocError::Io(e.to_string()))?;

    let mut reader = Reader::from_str(&xml);
    reader.config_mut().trim_text(true);

    let mut sheet_idx = 1;
    let mut buf = Vec::new();
    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) | Ok(Event::Empty(ref e)) => {
                if e.local_name().as_ref() == b"sheet" {
                    let mut name = format!("Sheet{sheet_idx}");
                    for attr in e.attributes().flatten() {
                        if attr.key.local_name().as_ref() == b"name" {
                            name = String::from_utf8_lossy(&attr.value).to_string();
                        }
                    }
                    sheets.push((sheet_idx, name));
                    sheet_idx += 1;
                }
            }
            Ok(Event::Eof) => break,
            Err(e) => return Err(AnydocError::Xml(e.to_string())),
            _ => {}
        }
        buf.clear();
    }

    Ok(sheets)
}

fn parse_worksheet(xml: &str, shared_strings: &[String]) -> Result<Vec<Vec<String>>, AnydocError> {
    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(false);

    let mut rows: BTreeMap<usize, BTreeMap<usize, String>> = BTreeMap::new();
    let mut current_row_idx: usize = 0;
    let mut current_col_idx: usize = 0;
    let mut current_cell_type = String::new();
    let mut in_v = false;
    let mut in_is_t = false;
    let mut current_v = String::new();

    let mut buf = Vec::new();
    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) => {
                let local = e.local_name();
                match local.as_ref() {
                    b"row" => {
                        for attr in e.attributes().flatten() {
                            if attr.key.local_name().as_ref() == b"r"
                                && let Ok(r) = String::from_utf8_lossy(&attr.value).parse::<usize>()
                            {
                                current_row_idx = r;
                            }
                        }
                    }
                    b"c" => {
                        current_cell_type.clear();
                        current_v.clear();
                        for attr in e.attributes().flatten() {
                            let k = attr.key.local_name();
                            if k.as_ref() == b"r" {
                                let cell_ref = String::from_utf8_lossy(&attr.value);
                                current_col_idx = parse_column_index(&cell_ref);
                            } else if k.as_ref() == b"t" {
                                current_cell_type = String::from_utf8_lossy(&attr.value).to_string();
                            }
                        }
                    }
                    b"v" => in_v = true,
                    b"t" => in_is_t = true,
                    _ => {}
                }
            }
            Ok(Event::Text(ref e)) => {
                if in_v || in_is_t {
                    let text = e.unescape().unwrap_or_default().to_string();
                    current_v.push_str(&text);
                }
            }
            Ok(Event::End(ref e)) => {
                let local = e.local_name();
                match local.as_ref() {
                    b"v" => in_v = false,
                    b"t" => in_is_t = false,
                    b"c" => {
                        let final_val = resolve_cell_value(&current_cell_type, &current_v, shared_strings);
                        if !final_val.trim().is_empty() {
                            rows.entry(current_row_idx)
                                .or_default()
                                .insert(current_col_idx, final_val);
                        }
                        current_v.clear();
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

    // Convert BTreeMap rows into a rectangular 2D vector
    if rows.is_empty() {
        return Ok(Vec::new());
    }

    let max_col = rows
        .values()
        .flat_map(|r| r.keys().copied())
        .max()
        .unwrap_or(0);

    let mut result = Vec::new();
    for (_row_idx, col_map) in rows {
        let mut row_vec = vec![String::new(); max_col + 1];
        for (col_idx, val) in col_map {
            if col_idx <= max_col {
                row_vec[col_idx] = val.replace('\n', " ").replace('|', "\\|");
            }
        }
        // Only include rows that have at least one non-empty value
        if row_vec.iter().any(|c| !c.is_empty()) {
            result.push(row_vec);
        }
    }

    Ok(result)
}

fn resolve_cell_value(cell_type: &str, raw_v: &str, shared_strings: &[String]) -> String {
    let trimmed = raw_v.trim();
    if cell_type == "s" {
        if let Ok(idx) = trimmed.parse::<usize>() {
            return shared_strings.get(idx).cloned().unwrap_or_else(|| trimmed.to_string());
        }
    } else if cell_type == "b" {
        return if trimmed == "1" { "TRUE".into() } else { "FALSE".into() };
    }
    trimmed.to_string()
}

fn parse_column_index(cell_ref: &str) -> usize {
    let mut col = 0;
    for b in cell_ref.bytes() {
        if b.is_ascii_uppercase() {
            col = col * 26 + ((b - b'A' + 1) as usize);
        } else {
            break;
        }
    }
    col.saturating_sub(1)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Cursor, Write};
    use zip::write::SimpleFileOptions;
    use zip::ZipWriter;

    #[test]
    fn test_xlsx_parsing() {
        let sst_xml = r#"<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="3" uniqueCount="3">
  <si><t>Item Name</t></si>
  <si><t>Quantity</t></si>
  <si><t>Apples</t></si>
</sst>"#;

        let sheet_xml = r#"<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1">
      <c r="A1" t="s"><v>0</v></c>
      <c r="B1" t="s"><v>1</v></c>
    </row>
    <row r="2">
      <c r="A2" t="s"><v>2</v></c>
      <c r="B2"><v>42</v></c>
    </row>
  </sheetData>
</worksheet>"#;

        let mut buf = Vec::new();
        {
            let mut zip = ZipWriter::new(Cursor::new(&mut buf));
            let opts = SimpleFileOptions::default();
            zip.start_file("xl/sharedStrings.xml", opts).unwrap();
            zip.write_all(sst_xml.as_bytes()).unwrap();
            zip.start_file("xl/worksheets/sheet1.xml", opts).unwrap();
            zip.write_all(sheet_xml.as_bytes()).unwrap();
            zip.finish().unwrap();
        }

        let opts = AnydocOptions::default();
        let parsed = parse_xlsx(&buf, &opts).expect("should parse xlsx");
        assert!(parsed.markdown.contains("## Sheet: Sheet 1"));
        assert!(parsed.markdown.contains("| Item Name | Quantity |"));
        assert!(parsed.markdown.contains("| Apples | 42 |"));
    }
}

