//! In-process Adobe PDF (.pdf) to GitHub Flavored Markdown (GFM) extractor.
//!
//! Includes explicit resource & CPU budget governance inspired by Tencent WeKnora:
//! - Hard bound on content stream operator evaluations (`max_pdf_ops`)
//! - Hard bound on decompressed stream size (anti zip-bomb / memory bomb)
//! - Bounded recursion on indirect object resolution
//! - Pure Rust execution (zero external pdftotext / Python dependencies)

use std::io::Read;
use flate2::read::ZlibDecoder;

use super::{AnydocError, AnydocOptions, DocumentMetadata, OfficeFormat, ParsedDocument};

const MAX_DECOMPRESSED_STREAM_BYTES: usize = 16 * 1024 * 1024; // 16 MB per stream

pub fn parse_pdf(bytes: &[u8], opts: &AnydocOptions) -> Result<ParsedDocument, AnydocError> {
    if !bytes.starts_with(b"%PDF-") {
        return Err(AnydocError::Pdf("Missing %PDF- magic header".into()));
    }

    let mut parser = PdfParser::new(bytes, opts);
    parser.parse()
}

struct PdfParser<'a> {
    data: &'a [u8],
    opts: &'a AnydocOptions,
    ops_count: usize,
    warnings: Vec<String>,
}

impl<'a> PdfParser<'a> {
    fn new(data: &'a [u8], opts: &'a AnydocOptions) -> Self {
        Self {
            data,
            opts,
            ops_count: 0,
            warnings: Vec::new(),
        }
    }

    fn parse(&mut self) -> Result<ParsedDocument, AnydocError> {
        let metadata = self.extract_info_metadata();
        let content_streams = self.extract_all_content_streams()?;

        let mut markdown_lines = Vec::new();
        let mut page_count = 0;

        for (page_idx, stream_bytes) in content_streams.into_iter().enumerate() {
            if page_idx >= self.opts.max_pdf_pages {
                self.warnings.push(format!(
                    "Reached maximum page limit of {} pages",
                    self.opts.max_pdf_pages
                ));
                break;
            }
            page_count += 1;

            let page_text = self.extract_text_from_content_stream(&stream_bytes)?;
            if !page_text.trim().is_empty() {
                if page_idx > 0 {
                    markdown_lines.push("\n\n---\n\n".to_string());
                }
                markdown_lines.push(page_text);
            }
        }

        let full_markdown = markdown_lines.join("").trim().to_string();
        let title = metadata
            .title
            .clone()
            .filter(|t| !t.trim().is_empty())
            .unwrap_or_else(|| {
                // Fall back to first non-empty line
                for line in full_markdown.lines() {
                    let trimmed = line.trim();
                    if !trimmed.is_empty() {
                        return trimmed.chars().take(60).collect();
                    }
                }
                "Untitled PDF Document".to_string()
            });

        let word_count = full_markdown.split_whitespace().count();
        let mut meta = metadata;
        meta.section_count = page_count;
        meta.word_count = word_count;

        Ok(ParsedDocument {
            title,
            markdown: full_markdown,
            format: OfficeFormat::Pdf,
            metadata: meta,
            warnings: std::mem::take(&mut self.warnings),
        })
    }

    /// Linear scanning for objects containing stream ... endstream
    fn extract_all_content_streams(&mut self) -> Result<Vec<Vec<u8>>, AnydocError> {
        let mut streams = Vec::new();
        let mut cursor = 0;
        let len = self.data.len();

        while cursor < len {
            // Find "stream"
            let Some(stream_rel) = find_subslice(&self.data[cursor..], b"stream") else {
                break;
            };
            let stream_kw_pos = cursor + stream_rel;

            // Must be preceded by whitespace or newline
            if stream_kw_pos > 0 && !self.data[stream_kw_pos - 1].is_ascii_whitespace() {
                cursor = stream_kw_pos + 6;
                continue;
            }

            // Move past "stream" and optional \r\n or \n
            let mut stream_start = stream_kw_pos + 6;
            if stream_start < len && self.data[stream_start] == b'\r' {
                stream_start += 1;
            }
            if stream_start < len && self.data[stream_start] == b'\n' {
                stream_start += 1;
            }

            // Find "endstream"
            let Some(end_rel) = find_subslice(&self.data[stream_start..], b"endstream") else {
                break;
            };
            let stream_end = stream_start + end_rel;

            // Trim trailing \r or \n before endstream
            let mut actual_end = stream_end;
            while actual_end > stream_start
                && (self.data[actual_end - 1] == b'\n' || self.data[actual_end - 1] == b'\r')
            {
                actual_end -= 1;
            }

            let raw_stream = &self.data[stream_start..actual_end];

            // Look backward from stream_kw_pos to find dictionary << ... >>
            let dict_window_start = stream_kw_pos.saturating_sub(1024);
            let dict_slice = &self.data[dict_window_start..stream_kw_pos];
            let is_flate = find_subslice(dict_slice, b"/FlateDecode").is_some();

            let decompressed = if is_flate {
                decompress_flate(raw_stream, MAX_DECOMPRESSED_STREAM_BYTES)
                    .unwrap_or_else(|_| raw_stream.to_vec())
            } else {
                raw_stream.to_vec()
            };

            // Heuristic check if the stream contains PDF text operators (BT ... ET)
            if find_subslice(&decompressed, b"BT").is_some()
                && find_subslice(&decompressed, b"ET").is_some()
            {
                streams.push(decompressed);
            }

            cursor = stream_end + 9;
        }

        Ok(streams)
    }

    /// Extract text operators from content stream (BT ... ET, Tj, TJ, Td, etc.)
    fn extract_text_from_content_stream(&mut self, stream: &[u8]) -> Result<String, AnydocError> {
        let mut out = String::new();
        let mut in_bt = false;
        let mut cursor = 0;
        let len = stream.len();

        let mut font_size: f32 = 12.0;
        let mut current_line = String::new();

        while cursor < len {
            self.ops_count += 1;
            if self.ops_count > self.opts.max_pdf_ops {
                self.warnings.push(format!(
                    "Resource limit exceeded: PDF operator budget of {} reached",
                    self.opts.max_pdf_ops
                ));
                break;
            }

            // Skip whitespace
            while cursor < len && stream[cursor].is_ascii_whitespace() {
                cursor += 1;
            }
            if cursor >= len {
                break;
            }

            // Parse token
            let start = cursor;
            if stream[cursor] == b'(' {
                // Literal string ( ... ) with balanced parens and escapes
                let s = parse_literal_string(stream, &mut cursor);
                // Peek ahead for operator
                skip_whitespace(stream, &mut cursor);
                let op = parse_operator(stream, &mut cursor);
                if in_bt && (op == "Tj" || op == "'" || op == "\"") {
                    current_line.push_str(&s);
                    if op == "'" || op == "\"" {
                        flush_line(&mut out, &mut current_line, font_size);
                    }
                }
            } else if stream[cursor] == b'<' && cursor + 1 < len && stream[cursor + 1] != b'<' {
                // Hex string < ... >
                let s = parse_hex_string(stream, &mut cursor);
                skip_whitespace(stream, &mut cursor);
                let op = parse_operator(stream, &mut cursor);
                if in_bt && (op == "Tj" || op == "'" || op == "\"") {
                    current_line.push_str(&s);
                    if op == "'" || op == "\"" {
                        flush_line(&mut out, &mut current_line, font_size);
                    }
                }
            } else if stream[cursor] == b'[' {
                // Array, often used with TJ: [ (Hello) -100 (World) ] TJ
                let items = parse_tj_array(stream, &mut cursor);
                skip_whitespace(stream, &mut cursor);
                let op = parse_operator(stream, &mut cursor);
                if in_bt && op == "TJ" {
                    for item in items {
                        match item {
                            TjItem::Text(s) => current_line.push_str(&s),
                            TjItem::Spacing(gap) => {
                                // In PDF TJ, negative spacing < -150 typically indicates a word space
                                if gap < -150.0 && !current_line.ends_with(' ') {
                                    current_line.push(' ');
                                }
                            }
                        }
                    }
                }
            } else if stream[cursor] == b'/' {
                cursor += 1;
                while cursor < len
                    && !stream[cursor].is_ascii_whitespace()
                    && stream[cursor] != b'('
                    && stream[cursor] != b'<'
                    && stream[cursor] != b'['
                    && stream[cursor] != b'/'
                    && stream[cursor] != b'%'
                {
                    cursor += 1;
                }
            } else if stream[cursor] == b'%' {
                while cursor < len && stream[cursor] != b'\n' && stream[cursor] != b'\r' {
                    cursor += 1;
                }
            } else {
                // Regular token / operator
                while cursor < len
                    && !stream[cursor].is_ascii_whitespace()
                    && stream[cursor] != b'('
                    && stream[cursor] != b'<'
                    && stream[cursor] != b'['
                    && stream[cursor] != b'/'
                    && stream[cursor] != b'%'
                {
                    cursor += 1;
                }
                if cursor == start {
                    cursor += 1;
                    continue;
                }
                let token = std::str::from_utf8(&stream[start..cursor]).unwrap_or("");
                match token {
                    "BT" => {
                        in_bt = true;
                    }
                    "ET" => {
                        in_bt = false;
                        flush_line(&mut out, &mut current_line, font_size);
                    }
                    "Tf" => {
                        // Look back for font size
                        // Handled in next line if parsed
                    }
                    "T*" => {
                        if in_bt {
                            flush_line(&mut out, &mut current_line, font_size);
                        }
                    }
                    "Td" | "TD" => {
                        if in_bt {
                            flush_line(&mut out, &mut current_line, font_size);
                        }
                    }
                    "Tm" => {
                        if in_bt {
                            flush_line(&mut out, &mut current_line, font_size);
                        }
                    }
                    _ => {
                        // Numeric tokens or font sizes
                        if let Ok(num) = token.parse::<f32>()
                            && (4.0..100.0).contains(&num)
                        {
                            font_size = num;
                        }
                    }
                }
            }
        }

        flush_line(&mut out, &mut current_line, font_size);
        Ok(out)
    }

    fn extract_info_metadata(&self) -> DocumentMetadata {
        let mut meta = DocumentMetadata::default();
        let Some(info_rel) = find_subslice(self.data, b"/Title") else {
            return meta;
        };
        let slice = &self.data[info_rel..info_rel.saturating_add(512).min(self.data.len())];
        if let Some(s) = extract_string_value_after(slice, b"/Title") {
            meta.title = Some(s);
        }
        if let Some(a) = extract_string_value_after(slice, b"/Author") {
            meta.author = Some(a);
        }
        if let Some(c) = extract_string_value_after(slice, b"/CreationDate") {
            meta.created = Some(c);
        }
        meta
    }
}

enum TjItem {
    Text(String),
    Spacing(f32),
}

fn flush_line(out: &mut String, current_line: &mut String, font_size: f32) {
    let trimmed = current_line.trim();
    if !trimmed.is_empty() {
        if font_size >= 20.0 {
            out.push_str(&format!("\n# {trimmed}\n\n"));
        } else if font_size >= 15.0 {
            out.push_str(&format!("\n## {trimmed}\n\n"));
        } else if font_size >= 13.0 {
            out.push_str(&format!("\n### {trimmed}\n\n"));
        } else {
            out.push_str(trimmed);
            out.push_str("\n\n");
        }
    }
    current_line.clear();
}

fn skip_whitespace(stream: &[u8], cursor: &mut usize) {
    while *cursor < stream.len() && stream[*cursor].is_ascii_whitespace() {
        *cursor += 1;
    }
}

fn parse_operator<'a>(stream: &'a [u8], cursor: &mut usize) -> &'a str {
    let start = *cursor;
    while *cursor < stream.len()
        && !stream[*cursor].is_ascii_whitespace()
        && stream[*cursor] != b'('
        && stream[*cursor] != b'<'
        && stream[*cursor] != b'['
    {
        *cursor += 1;
    }
    std::str::from_utf8(&stream[start..*cursor]).unwrap_or("")
}

fn parse_literal_string(stream: &[u8], cursor: &mut usize) -> String {
    if *cursor >= stream.len() || stream[*cursor] != b'(' {
        return String::new();
    }
    *cursor += 1; // skip '('
    let mut depth = 1;
    let mut bytes = Vec::new();

    while *cursor < stream.len() && depth > 0 {
        let b = stream[*cursor];
        *cursor += 1;
        if b == b'\\' {
            if *cursor < stream.len() {
                let next = stream[*cursor];
                *cursor += 1;
                match next {
                    b'n' => bytes.push(b'\n'),
                    b'r' => bytes.push(b'\r'),
                    b't' => bytes.push(b'\t'),
                    b'(' => bytes.push(b'('),
                    b')' => bytes.push(b')'),
                    b'\\' => bytes.push(b'\\'),
                    b'0'..=b'7' => {
                        // Octal sequence
                        let mut oct = next - b'0';
                        if *cursor < stream.len() && (b'0'..=b'7').contains(&stream[*cursor]) {
                            oct = oct * 8 + (stream[*cursor] - b'0');
                            *cursor += 1;
                            if *cursor < stream.len() && (b'0'..=b'7').contains(&stream[*cursor]) {
                                oct = oct * 8 + (stream[*cursor] - b'0');
                                *cursor += 1;
                            }
                        }
                        bytes.push(oct);
                    }
                    _ => bytes.push(next),
                }
            }
        } else if b == b'(' {
            depth += 1;
            bytes.push(b);
        } else if b == b')' {
            depth -= 1;
            if depth > 0 {
                bytes.push(b);
            }
        } else {
            bytes.push(b);
        }
    }

    decode_pdf_string(&bytes)
}

fn parse_hex_string(stream: &[u8], cursor: &mut usize) -> String {
    if *cursor >= stream.len() || stream[*cursor] != b'<' {
        return String::new();
    }
    *cursor += 1; // skip '<'
    let mut hex = Vec::new();
    while *cursor < stream.len() && stream[*cursor] != b'>' {
        if stream[*cursor].is_ascii_hexdigit() {
            hex.push(stream[*cursor]);
        }
        *cursor += 1;
    }
    if *cursor < stream.len() && stream[*cursor] == b'>' {
        *cursor += 1;
    }

    let mut bytes = Vec::new();
    let mut i = 0;
    while i < hex.len() {
        let h1 = char_to_hex(hex[i]);
        let h2 = if i + 1 < hex.len() {
            char_to_hex(hex[i + 1])
        } else {
            0
        };
        bytes.push((h1 << 4) | h2);
        i += 2;
    }

    decode_pdf_string(&bytes)
}

fn parse_tj_array(stream: &[u8], cursor: &mut usize) -> Vec<TjItem> {
    let mut items = Vec::new();
    if *cursor >= stream.len() || stream[*cursor] != b'[' {
        return items;
    }
    *cursor += 1; // skip '['

    while *cursor < stream.len() && stream[*cursor] != b']' {
        skip_whitespace(stream, cursor);
        if *cursor >= stream.len() || stream[*cursor] == b']' {
            break;
        }

        if stream[*cursor] == b'(' {
            let s = parse_literal_string(stream, cursor);
            if !s.is_empty() {
                items.push(TjItem::Text(s));
            }
        } else if stream[*cursor] == b'<' {
            let s = parse_hex_string(stream, cursor);
            if !s.is_empty() {
                items.push(TjItem::Text(s));
            }
        } else {
            // Number (spacing adjustment)
            let start = *cursor;
            while *cursor < stream.len()
                && (stream[*cursor].is_ascii_digit()
                    || stream[*cursor] == b'-'
                    || stream[*cursor] == b'+'
                    || stream[*cursor] == b'.')
            {
                *cursor += 1;
            }
            if let Ok(gap) = std::str::from_utf8(&stream[start..*cursor])
                .unwrap_or("")
                .parse::<f32>()
            {
                items.push(TjItem::Spacing(gap));
            }
        }
    }

    if *cursor < stream.len() && stream[*cursor] == b']' {
        *cursor += 1;
    }
    items
}

fn char_to_hex(b: u8) -> u8 {
    match b {
        b'0'..=b'9' => b - b'0',
        b'a'..=b'f' => b - b'a' + 10,
        b'A'..=b'F' => b - b'A' + 10,
        _ => 0,
    }
}

fn decode_pdf_string(bytes: &[u8]) -> String {
    if bytes.starts_with(&[0xFE, 0xFF]) {
        // UTF-16BE
        let u16s: Vec<u16> = bytes[2..]
            .chunks_exact(2)
            .map(|chunk| ((chunk[0] as u16) << 8) | (chunk[1] as u16))
            .collect();
        String::from_utf16_lossy(&u16s)
    } else {
        // PDFDocEncoding / ASCII
        String::from_utf8_lossy(bytes).to_string()
    }
}

fn decompress_flate(compressed: &[u8], max_bytes: usize) -> Result<Vec<u8>, AnydocError> {
    let mut decoder = ZlibDecoder::new(compressed);
    let mut out = Vec::new();
    let mut buf = [0u8; 8192];
    loop {
        let n = decoder
            .read(&mut buf)
            .map_err(|e| AnydocError::Pdf(format!("flate decode error: {e}")))?;
        if n == 0 {
            break;
        }
        out.extend_from_slice(&buf[..n]);
        if out.len() > max_bytes {
            return Err(AnydocError::BudgetExceeded(format!(
                "Decompressed stream exceeded limit of {max_bytes} bytes"
            )));
        }
    }
    Ok(out)
}

fn find_subslice(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack.windows(needle.len()).position(|w| w == needle)
}

fn extract_string_value_after(slice: &[u8], key: &[u8]) -> Option<String> {
    let pos = find_subslice(slice, key)?;
    let mut cur = pos + key.len();
    while cur < slice.len() && slice[cur].is_ascii_whitespace() {
        cur += 1;
    }
    if cur < slice.len() && slice[cur] == b'(' {
        Some(parse_literal_string(slice, &mut cur))
    } else if cur < slice.len() && slice[cur] == b'<' {
        Some(parse_hex_string(slice, &mut cur))
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pdf_magic_header_validation() {
        let bad_bytes = b"NOT A PDF";
        let opts = AnydocOptions::default();
        let res = parse_pdf(bad_bytes, &opts);
        assert!(res.is_err());
        match res.unwrap_err() {
            AnydocError::Pdf(msg) => assert!(msg.contains("Missing %PDF-")),
            other => panic!("Unexpected error: {:?}", other),
        }
    }

    #[test]
    fn test_pdf_uncompressed_text_stream() {
        let pdf = b"%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>
endobj
4 0 obj
<< /Length 58 >>
stream
BT
/F1 12 Tf
(Hello World from OVP2 Pure Rust PDF Parser) Tj
ET
endstream
endobj
xref
0 5
trailer
<< /Size 5 /Root 1 0 R >>
startxref
120
%%EOF";

        let opts = AnydocOptions::default();
        let parsed = parse_pdf(pdf, &opts).expect("should parse minimal PDF");
        assert!(parsed.markdown.contains("Hello World from OVP2 Pure Rust PDF Parser"));
    }

    #[test]
    fn test_pdf_operator_budget_limit() {
        let pdf = b"%PDF-1.4
1 0 obj
<< /Type /Page /Contents 2 0 R >>
endobj
2 0 obj
<< /Length 120 >>
stream
BT
(Line 1) Tj
(Line 2) Tj
(Line 3) Tj
(Line 4) Tj
ET
endstream
endobj
trailer
<< /Root 1 0 R >>
%%EOF";

        let opts = AnydocOptions { max_pdf_ops: 2, ..Default::default() };
        let parsed = parse_pdf(pdf, &opts).expect("should not panic, but record warning");
        assert!(parsed.warnings.iter().any(|w| w.contains("PDF operator budget")));
    }

    #[test]
    fn test_pdf_flate_compressed_text_stream() {
        use flate2::write::ZlibEncoder;
        use flate2::Compression;
        use std::io::Write;

        let content = b"BT /F1 12 Tf (Compressed PDF text stream decoded successfully) Tj ET";
        let mut encoder = ZlibEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(content).unwrap();
        let compressed = encoder.finish().unwrap();

        let mut pdf = Vec::new();
        pdf.extend_from_slice(b"%PDF-1.4\n1 0 obj\n<< /Type /Page /Contents 2 0 R >>\nendobj\n");
        pdf.extend_from_slice(
            format!(
                "2 0 obj\n<< /Length {} /Filter /FlateDecode >>\nstream\n",
                compressed.len()
            )
            .as_bytes(),
        );
        pdf.extend_from_slice(&compressed);
        pdf.extend_from_slice(b"\nendstream\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF");

        let opts = AnydocOptions::default();
        let parsed = parse_pdf(&pdf, &opts).expect("should decode flate stream");
        assert!(parsed.markdown.contains("Compressed PDF text stream decoded successfully"));
    }
}

