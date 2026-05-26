"""Normalization helpers and the normalization-to-segmentation contract."""

from __future__ import annotations

from dataclasses import dataclass

from canonical_data_model import (
    ExtractedText,
    FidelityState,
    LineIndexEntry,
    NormalizationNote,
    SourceLocator,
    SpanMapEntry,
    TextSpan,
)

from .ids import build_content_hash, build_extracted_text_id

NORMALIZER_NAME = "basic_text_normalizer"
NORMALIZER_VERSION = "0.1.0"


@dataclass(frozen=True, slots=True)
class TextArtifacts:
    text: str
    span_map: list[SpanMapEntry]
    line_index: list[LineIndexEntry]


@dataclass(frozen=True, slots=True)
class SegmentationLine:
    """One normalized line plus its source mapping.

    Contract:
    - `text` is the exact substring from normalized text for `text_span`
    - line endings are already normalized to `\n`
    - `source_locator.line_start` and `line_end` preserve the original source line number
    - char mapping is best-effort and may be wider than normalized text when CRLF/BOM existed
    """

    line_number: int
    text: str
    text_span: TextSpan
    source_locator: SourceLocator

    def __post_init__(self) -> None:
        if self.line_number < 1:
            raise ValueError("SegmentationLine.line_number must be >= 1")
        if "\r" in self.text:
            raise ValueError("SegmentationLine.text must use LF-only newlines")
        if self.source_locator.line_start != self.line_number:
            raise ValueError("SegmentationLine.source_locator.line_start must match line_number")
        if self.source_locator.line_end != self.line_number:
            raise ValueError("SegmentationLine.source_locator.line_end must match line_number")

    @property
    def stripped_line(self) -> str:
        return self.text[:-1] if self.text.endswith("\n") else self.text

    @property
    def is_blank(self) -> bool:
        return not self.stripped_line.strip()


@dataclass(frozen=True, slots=True)
class SegmentationInput:
    """Explicit handoff contract from normalization to structural segmentation.

    Normalized text in the MVP means:
    - UTF-8 decoded text
    - no BOM at the start of the normalized view
    - LF-only line endings
    - source order preserved exactly
    - one contract line per normalized line
    - each contract line maps back to the original source line at minimum
    """

    source_document_id: str
    source_snapshot_id: str
    extracted_text_id: str
    text: str
    lines: list[SegmentationLine]
    normalization_notes: list[NormalizationNote]
    fidelity_state: FidelityState
    content_facets: list[str]

    def __post_init__(self) -> None:
        if "\r" in self.text:
            raise ValueError("SegmentationInput.text must use LF-only newlines")
        if self.text.startswith("\ufeff"):
            raise ValueError("SegmentationInput.text must not include a leading BOM")
        if not self.text and self.lines:
            raise ValueError("SegmentationInput.lines must be empty when text is empty")
        if self.text and not self.lines:
            raise ValueError("SegmentationInput.lines must not be empty when text is present")
        for expected_line_number, line in enumerate(self.lines, start=1):
            if line.line_number != expected_line_number:
                raise ValueError("SegmentationInput line numbers must be contiguous and start at 1")
            if self.text[line.text_span.start : line.text_span.end] != line.text:
                raise ValueError("SegmentationInput lines must match substrings of normalized text")


def _iter_source_lines(text: str) -> list[tuple[int, str, int, int]]:
    if not text:
        return []

    cursor = 0
    lines: list[tuple[int, str, int, int]] = []
    for line_number, raw_line in enumerate(text.splitlines(keepends=True), start=1):
        start = cursor
        end = start + len(raw_line)
        lines.append((line_number, raw_line, start, end))
        cursor = end
    return lines


def build_exact_text_artifacts(text: str, source_uri: str) -> TextArtifacts:
    if not text:
        zero_span = TextSpan(0, 0)
        return TextArtifacts(
            text=text,
            span_map=[SpanMapEntry(text_span=zero_span, locator=SourceLocator(source_uri=source_uri))],
            line_index=[],
        )

    span_map: list[SpanMapEntry] = []
    line_index: list[LineIndexEntry] = []
    for line_number, raw_line, start, end in _iter_source_lines(text):
        text_span = TextSpan(start, end)
        line_index.append(LineIndexEntry(line_number=line_number, text_span=text_span))
        span_map.append(
            SpanMapEntry(
                text_span=text_span,
                locator=SourceLocator(
                    source_uri=source_uri,
                    line_start=line_number,
                    line_end=line_number,
                    char_start=start,
                    char_end=end,
                ),
            )
        )
    return TextArtifacts(text=text, span_map=span_map, line_index=line_index)


def _extract_source_uri(extracted: ExtractedText) -> str:
    if not extracted.span_map:
        raise ValueError("ExtractedText must include at least one span_map entry in the MVP pipeline")
    return extracted.span_map[0].locator.source_uri


def _normalize_text(raw_text: str, source_uri: str) -> tuple[TextArtifacts, list[NormalizationNote]]:
    if not raw_text:
        zero_span = TextSpan(0, 0)
        return (
            TextArtifacts(
                text="",
                span_map=[SpanMapEntry(text_span=zero_span, locator=SourceLocator(source_uri=source_uri))],
                line_index=[],
            ),
            [],
        )

    notes: list[NormalizationNote] = []
    bom_removed = raw_text.startswith("\ufeff")
    newline_normalized = "\r" in raw_text

    normalized_parts: list[str] = []
    span_map: list[SpanMapEntry] = []
    line_index: list[LineIndexEntry] = []
    normalized_cursor = 0

    for line_number, raw_line, raw_start, raw_end in _iter_source_lines(raw_text):
        source_char_start = raw_start
        normalized_line = raw_line

        if line_number == 1 and normalized_line.startswith("\ufeff"):
            normalized_line = normalized_line[1:]
            source_char_start += 1

        normalized_line = normalized_line.replace("\r\n", "\n").replace("\r", "\n")
        normalized_parts.append(normalized_line)

        text_span = TextSpan(normalized_cursor, normalized_cursor + len(normalized_line))
        line_index.append(LineIndexEntry(line_number=line_number, text_span=text_span))
        span_map.append(
            SpanMapEntry(
                text_span=text_span,
                locator=SourceLocator(
                    source_uri=source_uri,
                    line_start=line_number,
                    line_end=line_number,
                    char_start=source_char_start,
                    char_end=raw_end,
                ),
            )
        )
        normalized_cursor = text_span.end

    normalized_text = "".join(normalized_parts)

    if bom_removed:
        notes.append(
            NormalizationNote(
                note_type="bom_removed",
                description="Removed leading UTF-8 BOM from normalized text view.",
                affected_span=TextSpan(0, 0),
            )
        )
    if newline_normalized:
        notes.append(
            NormalizationNote(
                note_type="newline_normalized",
                description="Normalized CRLF/CR newlines to LF in normalized text view.",
                affected_span=TextSpan(0, len(normalized_text)),
            )
        )

    return TextArtifacts(normalized_text, span_map, line_index), notes


def normalize_extracted_text(extracted: ExtractedText) -> ExtractedText:
    source_uri = _extract_source_uri(extracted)
    artifacts, notes = _normalize_text(extracted.text, source_uri)
    fidelity_state = (
        FidelityState.EXACT if artifacts.text == extracted.text else FidelityState.NORMALIZED
    )

    return ExtractedText(
        extracted_text_id=build_extracted_text_id(extracted.source_snapshot_id, "norm"),
        source_document_id=extracted.source_document_id,
        source_snapshot_id=extracted.source_snapshot_id,
        extractor_name=NORMALIZER_NAME,
        extractor_version=NORMALIZER_VERSION,
        extraction_mode="normalized_text",
        text=artifacts.text,
        text_hash=build_content_hash(artifacts.text),
        span_map=artifacts.span_map,
        fidelity_state=fidelity_state,
        line_index=artifacts.line_index,
        normalization_notes=notes,
        ambiguity=[],
        content_facets=list(extracted.content_facets),
    )


def build_segmentation_input(normalized: ExtractedText) -> SegmentationInput:
    if normalized.extraction_mode != "normalized_text":
        raise ValueError(
            "SegmentationInput must be built from the normalized text stage, not directly from raw extraction"
        )

    if not normalized.text:
        return SegmentationInput(
            source_document_id=normalized.source_document_id,
            source_snapshot_id=normalized.source_snapshot_id,
            extracted_text_id=normalized.extracted_text_id,
            text="",
            lines=[],
            normalization_notes=list(normalized.normalization_notes),
            fidelity_state=normalized.fidelity_state,
            content_facets=list(normalized.content_facets),
        )

    if len(normalized.line_index) != len(normalized.span_map):
        raise ValueError("Normalized text must provide one line_index entry per span_map entry")

    lines: list[SegmentationLine] = []
    for line_entry, span_entry in zip(normalized.line_index, normalized.span_map):
        if line_entry.text_span != span_entry.text_span:
            raise ValueError("Normalized line_index and span_map entries must align exactly")
        line_text = normalized.text[line_entry.text_span.start : line_entry.text_span.end]
        lines.append(
            SegmentationLine(
                line_number=line_entry.line_number,
                text=line_text,
                text_span=line_entry.text_span,
                source_locator=span_entry.locator,
            )
        )

    return SegmentationInput(
        source_document_id=normalized.source_document_id,
        source_snapshot_id=normalized.source_snapshot_id,
        extracted_text_id=normalized.extracted_text_id,
        text=normalized.text,
        lines=lines,
        normalization_notes=list(normalized.normalization_notes),
        fidelity_state=normalized.fidelity_state,
        content_facets=list(normalized.content_facets),
    )
