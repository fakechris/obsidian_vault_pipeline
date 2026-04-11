from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .results import ExtractionRecord, ExtractionSpan
from .specs import ExtractionProfileSpec
from .validator import filter_valid_records

_DATE_RE = re.compile(r"\b(20\d{2}[-/]\d{2}[-/]\d{2}|\d{4}-\d{2}|\b[A-Z][a-z]{2,8}\s+\d{1,2},\s+20\d{2})\b")
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_BULLET_RE = re.compile(r"^\s*(?:[-*]|\d+\.)\s+(?P<step>.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Section:
    title: str
    body: str
    char_start: int
    char_end: int


class DefaultProfileExtractor:
    def extract(self, chunk_text, *, chunk_index, source_path, profile):  # noqa: ANN001
        if profile.name == "tech/doc_structure":
            records = self._extract_doc_structure(chunk_text, source_path)
        elif profile.name == "tech/workflow_graph":
            records = self._extract_workflow_graph(chunk_text, source_path)
        elif profile.name == "media/news_timeline":
            records = self._extract_news_timeline(chunk_text, source_path)
        elif profile.name == "media/commentary_sentiment":
            records = self._extract_commentary_sentiment(chunk_text, source_path)
        else:
            records = []
        return filter_valid_records(profile, records)

    def _extract_doc_structure(self, text: str, source_path: Path) -> list[ExtractionRecord]:
        records: list[ExtractionRecord] = []
        for section in _split_sections(text):
            summary = _first_sentence(section.body) or section.body.strip()[:160]
            references = _WIKILINK_RE.findall(section.body)
            records.append(
                ExtractionRecord(
                    values={
                        "section_title": section.title,
                        "section_kind": _infer_section_kind(section),
                        "summary": summary,
                        "references": references,
                    },
                    spans=[_span_for_section(source_path, section, summary or section.title)],
                )
            )
        return records

    def _extract_workflow_graph(self, text: str, source_path: Path) -> list[ExtractionRecord]:
        steps = [match.group("step").strip() for match in _BULLET_RE.finditer(text)]
        if not steps:
            steps = [section.title for section in _split_sections(text) if section.title]
        records: list[ExtractionRecord] = []
        previous_step = ""
        for step in steps:
            depends_on = [previous_step] if previous_step else []
            records.append(
                ExtractionRecord(
                    values={
                        "step_name": step,
                        "step_kind": _infer_step_kind(step),
                        "depends_on": depends_on,
                        "produces": _infer_produces(step),
                    },
                    spans=[
                        ExtractionSpan(
                            source_path=str(source_path),
                            section_title=step,
                            char_start=max(text.find(step), 0),
                            char_end=max(text.find(step), 0) + len(step),
                            quote=step[:180],
                        )
                    ],
                )
            )
            previous_step = step
        return records

    def _extract_news_timeline(self, text: str, source_path: Path) -> list[ExtractionRecord]:
        records: list[ExtractionRecord] = []
        title = _first_heading(text) or source_path.stem
        for sentence, start, end in _iter_sentences(text):
            if len(sentence) < 20:
                continue
            when_match = _DATE_RE.search(sentence)
            if not when_match and not any(keyword in sentence.lower() for keyword in ("announced", "released", "launched", "raised", "acquired")):
                continue
            records.append(
                ExtractionRecord(
                    values={
                        "event_type": _infer_event_type(sentence),
                        "actors": _extract_actors(sentence, fallback=title),
                        "when": when_match.group(0) if when_match else "",
                        "claim": sentence.strip(),
                        "impact": _impact_from_sentence(sentence),
                    },
                    spans=[
                        ExtractionSpan(
                            source_path=str(source_path),
                            section_title=title,
                            char_start=start,
                            char_end=end,
                            quote=sentence.strip()[:220],
                        )
                    ],
                )
            )
        return records[:12]

    def _extract_commentary_sentiment(self, text: str, source_path: Path) -> list[ExtractionRecord]:
        title = _first_heading(text) or source_path.stem
        positive = sum(word in text.lower() for word in ("good", "great", "strong", "useful", "promising", "important"))
        negative = sum(word in text.lower() for word in ("bad", "weak", "risk", "problem", "fragile", "concern"))
        score = positive - negative
        stance = "positive" if score > 0 else "negative" if score < 0 else "neutral"
        thesis = _first_sentence(text) or title
        return [
            ExtractionRecord(
                values={
                    "subject": title,
                    "stance": stance,
                    "sentiment_score": score,
                    "thesis": thesis,
                },
                spans=[
                    ExtractionSpan(
                        source_path=str(source_path),
                        section_title=title,
                        char_start=0,
                        char_end=min(len(thesis), len(text)),
                        quote=thesis[:220],
                    )
                ],
            )
        ]


def _split_sections(text: str) -> list[Section]:
    matches = list(re.finditer(r"^(#{1,6})\s+(.+)$", text, re.MULTILINE))
    if not matches:
        body = text.strip()
        if not body:
            return []
        return [Section(title="Document", body=body, char_start=0, char_end=len(text))]

    sections: list[Section] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        title = match.group(2).strip()
        body = text[match.end():end].strip()
        sections.append(Section(title=title, body=body, char_start=start, char_end=end))
    return sections


def _span_for_section(source_path: Path, section: Section, quote: str) -> ExtractionSpan:
    quote_text = quote.strip()[:220] or section.title[:220]
    quote_start = section.body.find(quote_text)
    char_start = section.char_start if quote_start < 0 else section.char_start + quote_start
    return ExtractionSpan(
        source_path=str(source_path),
        section_title=section.title,
        char_start=char_start,
        char_end=char_start + len(quote_text),
        quote=quote_text,
    )


def _first_sentence(text: str) -> str:
    normalized = " ".join(text.split())
    if not normalized:
        return ""
    parts = re.split(r"(?<=[.!?。！？])\s+", normalized)
    return parts[0].strip()


def _infer_section_kind(section: Section) -> str:
    body = section.body.lower()
    if "```" in section.body:
        return "code"
    if any(marker in body for marker in ("table", "|", "csv")):
        return "table"
    if any(marker in body for marker in ("appendix", "附录")):
        return "appendix"
    return "body"


def _infer_step_kind(step: str) -> str:
    lower = step.lower()
    if any(token in lower for token in ("if ", "when ", "whether", "decide")):
        return "decision"
    if any(token in lower for token in ("input", "fetch", "load", "read")):
        return "input"
    if any(token in lower for token in ("output", "write", "persist", "emit", "save")):
        return "output"
    return "action"


def _infer_produces(step: str) -> list[str]:
    lower = step.lower()
    outputs: list[str] = []
    if "artifact" in lower:
        outputs.append("artifact")
    if "summary" in lower:
        outputs.append("summary")
    if "index" in lower:
        outputs.append("index")
    if "record" in lower:
        outputs.append("record")
    return outputs


def _first_heading(text: str) -> str:
    match = re.search(r"^#{1,6}\s+(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _iter_sentences(text: str):
    start = 0
    for match in re.finditer(r"[^.!?。！？\n]+[.!?。！？]?", text):
        sentence = match.group(0).strip()
        if not sentence:
            continue
        yield sentence, match.start(), match.end()
        start = match.end()
    if start < len(text):
        tail = text[start:].strip()
        if tail:
            yield tail, start, len(text)


def _infer_event_type(sentence: str) -> str:
    lower = sentence.lower()
    if "raise" in lower or "fund" in lower:
        return "funding"
    if "release" in lower or "launch" in lower:
        return "launch"
    if "acquire" in lower:
        return "acquisition"
    if "publish" in lower or "paper" in lower:
        return "publication"
    return "update"


def _extract_actors(sentence: str, *, fallback: str) -> list[str]:
    actors = re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}\b", sentence)
    deduped = list(dict.fromkeys(actors))
    return deduped[:4] if deduped else [fallback]


def _impact_from_sentence(sentence: str) -> str:
    lowered = sentence.lower()
    if any(word in lowered for word in ("benchmark", "performance", "quality")):
        return "performance impact"
    if any(word in lowered for word in ("workflow", "pipeline", "agent")):
        return "workflow impact"
    return "notable development"
