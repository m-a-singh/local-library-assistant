"""Canonical data model for a local-first evidence system.

This module is the implementation companion to `canonical_data_model.md`.

Its role is narrow:
- define the canonical entities shared across the pipeline
- enforce core invariants on those entities
- provide a storage-agnostic object model for later adapters

Its role is not to:
- define segmentation policy
- define ingestion or normalization policy
- act as a task tracker or architecture brief

Pipeline model:
    SourceDocument -> ExtractedText -> StructuralRegion -> EvidenceUnit -> DerivedArtifact

The implementation is intentionally storage-agnostic and uses only the Python
standard library so it can serve as a neutral contract for later adapters.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


class DataModelValidationError(ValueError):
    """Raised when a model instance violates a canonical contract."""


class ValueEnum(str, Enum):
    """String-backed enum with predictable serialization."""

    def __str__(self) -> str:
        return self.value


class FidelityState(ValueEnum):
    EXACT = "exact"
    NORMALIZED = "normalized"
    LOSSY = "lossy"
    OCR_DERIVED = "ocr_derived"
    PARTIAL = "partial"


class TrustState(ValueEnum):
    RAW_EXTRACTED = "raw_extracted"
    NORMALIZED = "normalized"
    SEGMENTED = "segmented"
    VERIFIED = "verified"
    AMBIGUOUS = "ambiguous"


class AmbiguitySeverity(ValueEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ResolverStatus(ValueEnum):
    UNRESOLVED = "unresolved"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class ConfidenceLevel(ValueEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


def _require_non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DataModelValidationError(f"{field_name} must be a non-empty string")


def _require_non_negative(value: int, field_name: str) -> None:
    if value < 0:
        raise DataModelValidationError(f"{field_name} must be >= 0")


def _validate_span_order(start: Optional[int], end: Optional[int], label: str) -> None:
    if start is not None and start < 0:
        raise DataModelValidationError(f"{label} start must be >= 0")
    if end is not None and end < 0:
        raise DataModelValidationError(f"{label} end must be >= 0")
    if start is not None and end is not None and end < start:
        raise DataModelValidationError(f"{label} end must be >= start")


def _validate_line_order(start: Optional[int], end: Optional[int], label: str) -> None:
    if start is not None and start < 1:
        raise DataModelValidationError(f"{label} start must be >= 1")
    if end is not None and end < 1:
        raise DataModelValidationError(f"{label} end must be >= 1")
    if start is not None and end is not None and end < start:
        raise DataModelValidationError(f"{label} end must be >= start")


def _validate_non_empty_items(values: Sequence[str], field_name: str) -> None:
    if not values:
        raise DataModelValidationError(f"{field_name} must not be empty")
    for value in values:
        _require_non_empty(value, field_name)


def _serialize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


@dataclass(frozen=True)
class BoundingBox:
    x0: float
    y0: float
    x1: float
    y1: float
    page: Optional[int] = None

    def __post_init__(self) -> None:
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise DataModelValidationError("bbox max coordinates must be >= min coordinates")
        if self.page is not None and self.page < 1:
            raise DataModelValidationError("bbox page must be >= 1")


@dataclass(frozen=True)
class TextSpan:
    start: int
    end: int

    def __post_init__(self) -> None:
        _validate_span_order(self.start, self.end, "text_span")

    def overlaps(self, other: "TextSpan") -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class SourceLocator:
    source_uri: str
    page: Optional[int] = None
    block: Optional[str] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    byte_start: Optional[int] = None
    byte_end: Optional[int] = None
    bbox: Optional[BoundingBox] = None

    def __post_init__(self) -> None:
        _require_non_empty(self.source_uri, "source_uri")
        _validate_line_order(self.line_start, self.line_end, "line range")
        _validate_span_order(self.char_start, self.char_end, "char range")
        _validate_span_order(self.byte_start, self.byte_end, "byte range")
        if self.page is not None and self.page < 1:
            raise DataModelValidationError("page must be >= 1")
        if self.block is not None:
            _require_non_empty(self.block, "block")

    def has_position(self) -> bool:
        return any(
            value is not None
            for value in (
                self.page,
                self.block,
                self.line_start,
                self.line_end,
                self.char_start,
                self.char_end,
                self.byte_start,
                self.byte_end,
                self.bbox,
            )
        )

    def is_precise(self) -> bool:
        return self.line_start is not None or self.char_start is not None or self.byte_start is not None


@dataclass(frozen=True)
class AnchoredSpan:
    locator: SourceLocator
    text_span: Optional[TextSpan] = None
    label: Optional[str] = None
    role: Optional[str] = None

    def __post_init__(self) -> None:
        if self.label is not None:
            _require_non_empty(self.label, "label")
        if self.role is not None:
            _require_non_empty(self.role, "role")


@dataclass(frozen=True)
class RecordRef:
    entity_type: str
    record_id: str

    def __post_init__(self) -> None:
        _require_non_empty(self.entity_type, "entity_type")
        _require_non_empty(self.record_id, "record_id")


@dataclass(frozen=True)
class AmbiguityAlternative:
    label: str
    locator: Optional[SourceLocator] = None
    note: Optional[str] = None

    def __post_init__(self) -> None:
        _require_non_empty(self.label, "label")
        if self.note is not None:
            _require_non_empty(self.note, "note")


@dataclass(frozen=True)
class AmbiguityMarker:
    ambiguity_type: str
    reason: str
    severity: AmbiguitySeverity
    resolver_status: ResolverStatus
    affected_locator: Optional[SourceLocator] = None
    alternatives: List[AmbiguityAlternative] = field(default_factory=list)

    def __post_init__(self) -> None:
        _require_non_empty(self.ambiguity_type, "ambiguity_type")
        _require_non_empty(self.reason, "reason")


@dataclass(frozen=True)
class ProvenanceLink:
    source_document_id: str
    source_snapshot_id: str
    locator: SourceLocator
    role: str
    recoverable: bool
    upstream_entity_refs: List[RecordRef] = field(default_factory=list)
    text_span: Optional[TextSpan] = None

    def __post_init__(self) -> None:
        _require_non_empty(self.source_document_id, "source_document_id")
        _require_non_empty(self.source_snapshot_id, "source_snapshot_id")
        _require_non_empty(self.role, "role")


@dataclass(frozen=True)
class SpanMapEntry:
    text_span: TextSpan
    locator: SourceLocator
    confidence: Optional[float] = None

    def __post_init__(self) -> None:
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise DataModelValidationError("confidence must be between 0.0 and 1.0")


@dataclass(frozen=True)
class LineIndexEntry:
    line_number: int
    text_span: TextSpan

    def __post_init__(self) -> None:
        if self.line_number < 1:
            raise DataModelValidationError("line_number must be >= 1")


@dataclass(frozen=True)
class PageMapEntry:
    page_number: int
    text_span: TextSpan

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise DataModelValidationError("page_number must be >= 1")


@dataclass(frozen=True)
class BlockMapEntry:
    block_id: str
    text_span: TextSpan

    def __post_init__(self) -> None:
        _require_non_empty(self.block_id, "block_id")


@dataclass(frozen=True)
class NormalizationNote:
    note_type: str
    description: str
    affected_span: Optional[TextSpan] = None

    def __post_init__(self) -> None:
        _require_non_empty(self.note_type, "note_type")
        _require_non_empty(self.description, "description")


@dataclass(frozen=True)
class LanguageSpan:
    language: str
    text_span: TextSpan

    def __post_init__(self) -> None:
        _require_non_empty(self.language, "language")


@dataclass(frozen=True)
class ExtractionError:
    code: str
    message: str
    recoverable: bool = True
    locator: Optional[SourceLocator] = None

    def __post_init__(self) -> None:
        _require_non_empty(self.code, "code")
        _require_non_empty(self.message, "message")


@dataclass(frozen=True)
class EntityRef:
    entity_kind: str
    value: str
    evidence_unit_id: Optional[str] = None

    def __post_init__(self) -> None:
        _require_non_empty(self.entity_kind, "entity_kind")
        _require_non_empty(self.value, "value")
        if self.evidence_unit_id is not None:
            _require_non_empty(self.evidence_unit_id, "evidence_unit_id")


@dataclass(frozen=True)
class SourceScope:
    scope_type: str
    source_document_ids: List[str]
    source_snapshot_ids: List[str]

    def __post_init__(self) -> None:
        _require_non_empty(self.scope_type, "scope_type")
        _validate_non_empty_items(self.source_document_ids, "source_document_ids")
        _validate_non_empty_items(self.source_snapshot_ids, "source_snapshot_ids")


@dataclass(frozen=True)
class ArtifactTarget:
    target_type: str
    value: str
    evidence_unit_ids: List[str] = field(default_factory=list)
    locator: Optional[SourceLocator] = None

    def __post_init__(self) -> None:
        _require_non_empty(self.target_type, "target_type")
        _require_non_empty(self.value, "value")
        for evidence_unit_id in self.evidence_unit_ids:
            _require_non_empty(evidence_unit_id, "evidence_unit_ids")


@dataclass(frozen=True)
class FieldDefinition:
    name: str
    field_type: str
    why: str

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "name")
        _require_non_empty(self.field_type, "field_type")
        _require_non_empty(self.why, "why")


@dataclass(frozen=True)
class EntityDefinition:
    name: str
    purpose: str
    required_fields: List[FieldDefinition]
    optional_fields: List[FieldDefinition]
    invariants: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "name")
        _require_non_empty(self.purpose, "purpose")


@dataclass(frozen=True)
class SchemaProfile:
    name: str
    summary: str
    shared_types: List[EntityDefinition]
    entities: List[EntityDefinition]

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "name")
        _require_non_empty(self.summary, "summary")

    def entity_map(self) -> Dict[str, EntityDefinition]:
        return {entity.name: entity for entity in self.entities}

    def to_dict(self) -> Dict[str, Any]:
        return _serialize(self)


@dataclass(frozen=True)
class SourceDocument:
    source_document_id: str
    source_snapshot_id: str
    source_uri: str
    source_kind: str
    mime_type: str
    content_hash: str
    size_bytes: int
    display_name: Optional[str] = None
    relative_path: Optional[str] = None
    encoding: Optional[str] = None
    language_hint: Optional[str] = None
    created_at: Optional[str] = None
    modified_at: Optional[str] = None
    collector: Optional[str] = None
    content_facets: List[str] = field(default_factory=list)
    source_metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty(self.source_document_id, "source_document_id")
        _require_non_empty(self.source_snapshot_id, "source_snapshot_id")
        _require_non_empty(self.source_uri, "source_uri")
        _require_non_empty(self.source_kind, "source_kind")
        _require_non_empty(self.mime_type, "mime_type")
        _require_non_empty(self.content_hash, "content_hash")
        _require_non_negative(self.size_bytes, "size_bytes")

    def to_dict(self) -> Dict[str, Any]:
        return _serialize(self)


@dataclass(frozen=True)
class ExtractedText:
    extracted_text_id: str
    source_document_id: str
    source_snapshot_id: str
    extractor_name: str
    extractor_version: str
    extraction_mode: str
    text: str
    text_hash: str
    span_map: List[SpanMapEntry]
    fidelity_state: FidelityState
    line_index: List[LineIndexEntry] = field(default_factory=list)
    page_map: List[PageMapEntry] = field(default_factory=list)
    block_map: List[BlockMapEntry] = field(default_factory=list)
    normalization_notes: List[NormalizationNote] = field(default_factory=list)
    language_spans: List[LanguageSpan] = field(default_factory=list)
    errors: List[ExtractionError] = field(default_factory=list)
    ambiguity: List[AmbiguityMarker] = field(default_factory=list)
    content_facets: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _require_non_empty(self.extracted_text_id, "extracted_text_id")
        _require_non_empty(self.source_document_id, "source_document_id")
        _require_non_empty(self.source_snapshot_id, "source_snapshot_id")
        _require_non_empty(self.extractor_name, "extractor_name")
        _require_non_empty(self.extractor_version, "extractor_version")
        _require_non_empty(self.extraction_mode, "extraction_mode")
        _require_non_empty(self.text_hash, "text_hash")
        if self.text and not self.span_map:
            raise DataModelValidationError("span_map is required when extracted text is present")

    def to_dict(self) -> Dict[str, Any]:
        return _serialize(self)


@dataclass(frozen=True)
class StructuralRegion:
    structural_region_id: str
    source_document_id: str
    source_snapshot_id: str
    extracted_text_id: str
    region_type: str
    primary_span: AnchoredSpan
    ordinal: int
    content_facets: List[str]
    boundary_basis: str
    parent_region_id: Optional[str] = None
    prev_region_id: Optional[str] = None
    next_region_id: Optional[str] = None
    label: Optional[str] = None
    heading_path: List[str] = field(default_factory=list)
    supporting_spans: List[AnchoredSpan] = field(default_factory=list)
    ambiguity: List[AmbiguityMarker] = field(default_factory=list)
    quality_flags: List[str] = field(default_factory=list)
    children_count: Optional[int] = None

    def __post_init__(self) -> None:
        _require_non_empty(self.structural_region_id, "structural_region_id")
        _require_non_empty(self.source_document_id, "source_document_id")
        _require_non_empty(self.source_snapshot_id, "source_snapshot_id")
        _require_non_empty(self.extracted_text_id, "extracted_text_id")
        _require_non_empty(self.region_type, "region_type")
        _require_non_empty(self.boundary_basis, "boundary_basis")
        _validate_non_empty_items(self.content_facets, "content_facets")
        _require_non_negative(self.ordinal, "ordinal")
        if self.parent_region_id is not None:
            _require_non_empty(self.parent_region_id, "parent_region_id")
        if self.prev_region_id is not None:
            _require_non_empty(self.prev_region_id, "prev_region_id")
        if self.next_region_id is not None:
            _require_non_empty(self.next_region_id, "next_region_id")
        if self.label is not None:
            _require_non_empty(self.label, "label")
        if self.children_count is not None:
            _require_non_negative(self.children_count, "children_count")

    @property
    def line_start(self) -> Optional[int]:
        return self.primary_span.locator.line_start

    @property
    def line_end(self) -> Optional[int]:
        return self.primary_span.locator.line_end

    @property
    def char_start(self) -> Optional[int]:
        return self.primary_span.locator.char_start

    @property
    def char_end(self) -> Optional[int]:
        return self.primary_span.locator.char_end

    def to_dict(self) -> Dict[str, Any]:
        payload = _serialize(self)
        payload["line_start"] = self.line_start
        payload["line_end"] = self.line_end
        payload["char_start"] = self.char_start
        payload["char_end"] = self.char_end
        return payload


@dataclass(frozen=True)
class EvidenceUnit:
    evidence_unit_id: str
    source_document_id: str
    source_snapshot_id: str
    unit_type: str
    canonical_text: str
    support_links: List[ProvenanceLink]
    structural_region_ids: List[str]
    ordinal: int
    boundary_rationale: str
    content_facets: List[str]
    trust_state: TrustState
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH
    parent_region_id: Optional[str] = None
    parent_evidence_unit_id: Optional[str] = None
    prev_evidence_unit_id: Optional[str] = None
    next_evidence_unit_id: Optional[str] = None
    context_labels: List[str] = field(default_factory=list)
    ambiguity: List[AmbiguityMarker] = field(default_factory=list)
    signals: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    integrity_flags: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    entity_refs: List[EntityRef] = field(default_factory=list)
    conflict_refs: List[RecordRef] = field(default_factory=list)
    expansion_policy_hint: Optional[str] = None

    def __post_init__(self) -> None:
        _require_non_empty(self.evidence_unit_id, "evidence_unit_id")
        _require_non_empty(self.source_document_id, "source_document_id")
        _require_non_empty(self.source_snapshot_id, "source_snapshot_id")
        _require_non_empty(self.unit_type, "unit_type")
        _require_non_empty(self.canonical_text, "canonical_text")
        _require_non_empty(self.boundary_rationale, "boundary_rationale")
        _validate_non_empty_items(self.structural_region_ids, "structural_region_ids")
        _validate_non_empty_items(self.content_facets, "content_facets")
        _require_non_negative(self.ordinal, "ordinal")
        if not self.support_links:
            raise DataModelValidationError("support_links must not be empty")
        if self.parent_region_id is not None:
            _require_non_empty(self.parent_region_id, "parent_region_id")
        if self.parent_evidence_unit_id is not None:
            _require_non_empty(self.parent_evidence_unit_id, "parent_evidence_unit_id")
        if self.prev_evidence_unit_id is not None:
            _require_non_empty(self.prev_evidence_unit_id, "prev_evidence_unit_id")
        if self.next_evidence_unit_id is not None:
            _require_non_empty(self.next_evidence_unit_id, "next_evidence_unit_id")
        if self.expansion_policy_hint is not None:
            _require_non_empty(self.expansion_policy_hint, "expansion_policy_hint")
        for signal in self.signals:
            _require_non_empty(signal, "signals")
        for flag in self.flags:
            _require_non_empty(flag, "flags")
        for integrity_flag in self.integrity_flags:
            _require_non_empty(integrity_flag, "integrity_flags")
        if not any(link.recoverable and link.locator.has_position() for link in self.support_links):
            raise DataModelValidationError(
                "evidence units require at least one recoverable support locator"
            )
        for support_link in self.support_links:
            if support_link.source_document_id != self.source_document_id:
                raise DataModelValidationError(
                    "support link source_document_id must match the evidence unit"
                )
            if support_link.source_snapshot_id != self.source_snapshot_id:
                raise DataModelValidationError(
                    "support link source_snapshot_id must match the evidence unit"
                )

    @property
    def primary_locator(self) -> Optional[SourceLocator]:
        return self.support_links[0].locator if self.support_links else None

    @property
    def primary_support_link(self) -> Optional[ProvenanceLink]:
        return self.support_links[0] if self.support_links else None

    @property
    def text_span(self) -> Optional[TextSpan]:
        support_link = self.primary_support_link
        return support_link.text_span if support_link is not None else None

    @property
    def previous_unit_id(self) -> Optional[str]:
        return self.prev_evidence_unit_id

    @property
    def next_unit_id(self) -> Optional[str]:
        return self.next_evidence_unit_id

    @property
    def source_uri(self) -> Optional[str]:
        locator = self.primary_locator
        return locator.source_uri if locator is not None else None

    @property
    def line_start(self) -> Optional[int]:
        locator = self.primary_locator
        return locator.line_start if locator is not None else None

    @property
    def line_end(self) -> Optional[int]:
        locator = self.primary_locator
        return locator.line_end if locator is not None else None

    @property
    def char_start(self) -> Optional[int]:
        locator = self.primary_locator
        return locator.char_start if locator is not None else None

    @property
    def char_end(self) -> Optional[int]:
        locator = self.primary_locator
        return locator.char_end if locator is not None else None

    def to_dict(self) -> Dict[str, Any]:
        payload = _serialize(self)
        payload["source_uri"] = self.source_uri
        payload["text_span"] = _serialize(self.text_span)
        payload["line_start"] = self.line_start
        payload["line_end"] = self.line_end
        payload["char_start"] = self.char_start
        payload["char_end"] = self.char_end
        payload["previous_unit_id"] = self.previous_unit_id
        payload["next_unit_id"] = self.next_unit_id
        return payload


@dataclass(frozen=True)
class DerivedArtifact:
    derived_artifact_id: str
    artifact_type: str
    artifact_schema_version: str
    source_scope: SourceScope
    derived_from: List[RecordRef]
    payload: Dict[str, Any]
    producer_name: str
    producer_version: str
    artifact_hash: str
    target_refs: List[ArtifactTarget] = field(default_factory=list)
    ambiguity: List[AmbiguityMarker] = field(default_factory=list)
    confidence: Optional[float] = None
    refresh_state: Optional[str] = None
    notes: Optional[str] = None
    backfill_needed: bool = False
    retrieval_tags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _require_non_empty(self.derived_artifact_id, "derived_artifact_id")
        _require_non_empty(self.artifact_type, "artifact_type")
        _require_non_empty(self.artifact_schema_version, "artifact_schema_version")
        _require_non_empty(self.producer_name, "producer_name")
        _require_non_empty(self.producer_version, "producer_version")
        _require_non_empty(self.artifact_hash, "artifact_hash")
        if not self.derived_from:
            raise DataModelValidationError("derived_from must not be empty")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise DataModelValidationError("confidence must be between 0.0 and 1.0")
        if self.refresh_state is not None:
            _require_non_empty(self.refresh_state, "refresh_state")
        if self.notes is not None:
            _require_non_empty(self.notes, "notes")

    def to_dict(self) -> Dict[str, Any]:
        return _serialize(self)


@dataclass(frozen=True)
class EvidenceRecovery:
    evidence_unit_id: str
    source_uri: str
    source_snapshot_id: str
    locators: List[SourceLocator]
    parent_regions: List[StructuralRegion]
    previous_evidence_unit_id: Optional[str]
    next_evidence_unit_id: Optional[str]

    @property
    def previous_unit_id(self) -> Optional[str]:
        return self.previous_evidence_unit_id

    @property
    def next_unit_id(self) -> Optional[str]:
        return self.next_evidence_unit_id

    def to_dict(self) -> Dict[str, Any]:
        payload = _serialize(self)
        payload["previous_unit_id"] = self.previous_unit_id
        payload["next_unit_id"] = self.next_unit_id
        return payload


class EvidenceGraph:
    """In-memory registry for cross-record validation and recovery."""

    def __init__(self) -> None:
        self._sources_by_snapshot: Dict[str, SourceDocument] = {}
        self._source_ids_by_document: Dict[str, set[str]] = {}
        self._extracted_texts: Dict[str, ExtractedText] = {}
        self._structural_regions: Dict[str, StructuralRegion] = {}
        self._evidence_units: Dict[str, EvidenceUnit] = {}
        self._derived_artifacts: Dict[str, DerivedArtifact] = {}

    def add_source_document(self, document: SourceDocument) -> None:
        existing = self._sources_by_snapshot.get(document.source_snapshot_id)
        if existing and existing != document:
            raise DataModelValidationError(
                f"duplicate source_snapshot_id: {document.source_snapshot_id}"
            )
        self._sources_by_snapshot[document.source_snapshot_id] = document
        self._source_ids_by_document.setdefault(document.source_document_id, set()).add(
            document.source_snapshot_id
        )

    def add_extracted_text(self, extracted_text: ExtractedText) -> None:
        self._require_source(extracted_text.source_document_id, extracted_text.source_snapshot_id)
        self._extracted_texts[extracted_text.extracted_text_id] = extracted_text

    def add_structural_region(self, region: StructuralRegion) -> None:
        self._require_source(region.source_document_id, region.source_snapshot_id)
        self._require_known(self._extracted_texts, region.extracted_text_id, "extracted_text_id")
        if region.parent_region_id is not None:
            self._require_known(self._structural_regions, region.parent_region_id, "parent_region_id")
        if region.prev_region_id is not None:
            self._require_known(self._structural_regions, region.prev_region_id, "prev_region_id")
        self._structural_regions[region.structural_region_id] = region
        if region.next_region_id is not None:
            pass

    def add_evidence_unit(self, evidence_unit: EvidenceUnit) -> None:
        self._require_source(evidence_unit.source_document_id, evidence_unit.source_snapshot_id)
        for region_id in evidence_unit.structural_region_ids:
            region = self._require_known(self._structural_regions, region_id, "structural_region_ids")
            if region.source_snapshot_id != evidence_unit.source_snapshot_id:
                raise DataModelValidationError(
                    "evidence units and structural regions must share source_snapshot_id"
                )
        if evidence_unit.parent_evidence_unit_id is not None:
            self._require_known(
                self._evidence_units, evidence_unit.parent_evidence_unit_id, "parent_evidence_unit_id"
            )
        if evidence_unit.prev_evidence_unit_id is not None:
            self._require_known(
                self._evidence_units, evidence_unit.prev_evidence_unit_id, "prev_evidence_unit_id"
            )
        self._evidence_units[evidence_unit.evidence_unit_id] = evidence_unit

    def add_derived_artifact(self, artifact: DerivedArtifact) -> None:
        for source_document_id in artifact.source_scope.source_document_ids:
            if source_document_id not in self._source_ids_by_document:
                raise DataModelValidationError(
                    f"unknown source_document_id in source_scope: {source_document_id}"
                )
        for source_snapshot_id in artifact.source_scope.source_snapshot_ids:
            self._require_known(self._sources_by_snapshot, source_snapshot_id, "source_snapshot_ids")
        for ref in artifact.derived_from:
            self._resolve_record_ref(ref)
        for target_ref in artifact.target_refs:
            for evidence_unit_id in target_ref.evidence_unit_ids:
                self._require_known(self._evidence_units, evidence_unit_id, "target_refs.evidence_unit_ids")
        self._derived_artifacts[artifact.derived_artifact_id] = artifact

    def recover_evidence(self, evidence_unit_id: str) -> EvidenceRecovery:
        evidence_unit = self._require_known(self._evidence_units, evidence_unit_id, "evidence_unit_id")
        source_document = self._require_source(
            evidence_unit.source_document_id, evidence_unit.source_snapshot_id
        )
        parent_regions = [
            self._require_known(self._structural_regions, region_id, "structural_region_ids")
            for region_id in evidence_unit.structural_region_ids
        ]
        return EvidenceRecovery(
            evidence_unit_id=evidence_unit.evidence_unit_id,
            source_uri=source_document.source_uri,
            source_snapshot_id=evidence_unit.source_snapshot_id,
            locators=[support_link.locator for support_link in evidence_unit.support_links],
            parent_regions=parent_regions,
            previous_evidence_unit_id=evidence_unit.prev_evidence_unit_id,
            next_evidence_unit_id=evidence_unit.next_evidence_unit_id,
        )

    def get_neighbors(self, unit: EvidenceUnit, k: int) -> List[EvidenceUnit]:
        if k < 0:
            raise ValueError("k must be >= 0")
        previous_units = self._walk_neighbors(unit.previous_unit_id, "previous", k)
        next_units = self._walk_neighbors(unit.next_unit_id, "next", k)
        return list(reversed(previous_units)) + next_units

    def expand_context(self, unit: EvidenceUnit, n_units: int) -> List[EvidenceUnit]:
        if n_units < 0:
            raise ValueError("n_units must be >= 0")
        previous_units = self._walk_neighbors(unit.previous_unit_id, "previous", n_units)
        next_units = self._walk_neighbors(unit.next_unit_id, "next", n_units)
        return list(reversed(previous_units)) + [unit] + next_units

    def _require_source(self, source_document_id: str, source_snapshot_id: str) -> SourceDocument:
        document = self._require_known(self._sources_by_snapshot, source_snapshot_id, "source_snapshot_id")
        if document.source_document_id != source_document_id:
            raise DataModelValidationError(
                "source_document_id/source_snapshot_id pair does not resolve to the same source document"
            )
        return document

    def _resolve_record_ref(self, record_ref: RecordRef) -> None:
        if record_ref.entity_type == "EvidenceUnit":
            self._require_known(self._evidence_units, record_ref.record_id, "record_id")
            return
        if record_ref.entity_type == "StructuralRegion":
            self._require_known(self._structural_regions, record_ref.record_id, "record_id")
            return
        raise DataModelValidationError(f"unsupported RecordRef entity_type: {record_ref.entity_type}")

    def _walk_neighbors(self, start_id: Optional[str], direction: str, limit: int) -> List[EvidenceUnit]:
        neighbors: List[EvidenceUnit] = []
        next_id = start_id
        while next_id is not None and len(neighbors) < limit:
            neighbor = self._evidence_units.get(next_id)
            if neighbor is None:
                break
            neighbors.append(neighbor)
            if direction == "previous":
                next_id = neighbor.previous_unit_id
            else:
                next_id = neighbor.next_unit_id
        return neighbors

    @staticmethod
    def _require_known(registry: Dict[str, Any], key: str, field_name: str) -> Any:
        if key not in registry:
            raise DataModelValidationError(f"unknown {field_name}: {key}")
        return registry[key]


SHARED_TYPE_DEFINITIONS = [
    EntityDefinition(
        name="SourceLocator",
        purpose="Best recoverable source position without inventing unavailable precision.",
        required_fields=[
            FieldDefinition("source_uri", "string", "Provides the recovery path back to the local source."),
        ],
        optional_fields=[
            FieldDefinition("page", "integer", "Captures page-level location for paginated sources."),
            FieldDefinition("block", "string", "Carries layout or extractor block identity when available."),
            FieldDefinition("line_start", "integer", "Supports grounded citations into line-oriented sources."),
            FieldDefinition("line_end", "integer", "Closes line ranges for recovery and display."),
            FieldDefinition("char_start", "integer", "Tracks character-level precision when extraction preserves it."),
            FieldDefinition("char_end", "integer", "Closes character ranges for exact source spans."),
            FieldDefinition("byte_start", "integer", "Supports binary or encoding-aware recoverability."),
            FieldDefinition("byte_end", "integer", "Closes byte ranges when byte offsets are available."),
            FieldDefinition("bbox", "BoundingBox", "Preserves spatial recovery for PDF or OCR flows."),
        ],
    ),
    EntityDefinition(
        name="ProvenanceLink",
        purpose="Explicit trace from downstream objects back to exact source support.",
        required_fields=[
            FieldDefinition("source_document_id", "string", "Links evidence to the stable source identity."),
            FieldDefinition("source_snapshot_id", "string", "Pins the evidence to an exact source version."),
            FieldDefinition("locator", "SourceLocator", "Identifies the recoverable source span."),
            FieldDefinition("role", "string", "Distinguishes primary support from attached context."),
            FieldDefinition("recoverable", "boolean", "Signals whether the system can navigate back to source."),
        ],
        optional_fields=[
            FieldDefinition(
                "upstream_entity_refs",
                "RecordRef[]",
                "Shows which extracted or structural records produced this support path.",
            ),
            FieldDefinition(
                "text_span",
                "TextSpan",
                "Maps the source support into extracted-text coordinates when available.",
            ),
        ],
        invariants=[
            "Every EvidenceUnit must carry at least one recoverable ProvenanceLink with a non-empty locator.",
        ],
    ),
    EntityDefinition(
        name="AmbiguityMarker",
        purpose="Structured uncertainty model for extraction loss, mixed content, and disputed boundaries.",
        required_fields=[
            FieldDefinition("ambiguity_type", "string", "Names the uncertainty class so downstream systems can reason about it."),
            FieldDefinition("reason", "string", "Preserves the human-readable explanation for the ambiguity."),
            FieldDefinition("severity", "AmbiguitySeverity", "Supports retrieval and explanation tradeoffs."),
            FieldDefinition("resolver_status", "ResolverStatus", "Tracks whether the ambiguity has been addressed."),
        ],
        optional_fields=[
            FieldDefinition("affected_locator", "SourceLocator", "Narrows the ambiguity to a specific source area."),
            FieldDefinition("alternatives", "AmbiguityAlternative[]", "Captures competing interpretations without collapsing them."),
        ],
    ),
]


MINIMAL_SCHEMA = SchemaProfile(
    name="minimal",
    summary="Minimal object schema required for provenance-bearing ingestion, segmentation, and derived artifacts.",
    shared_types=SHARED_TYPE_DEFINITIONS,
    entities=[
        EntityDefinition(
            name="SourceDocument",
            purpose="Canonical identity and recovery anchor for an original local artifact snapshot.",
            required_fields=[
                FieldDefinition("source_document_id", "string", "Keeps references stable across reprocessing."),
                FieldDefinition("source_snapshot_id", "string", "Pins all downstream records to an exact version."),
                FieldDefinition("source_uri", "string", "Allows recovery of the original local source."),
                FieldDefinition("source_kind", "string", "Routes source-specific parsing and segmentation behavior."),
                FieldDefinition("mime_type", "string", "Preserves extractor routing metadata."),
                FieldDefinition("content_hash", "string", "Supports integrity checks and deduplication."),
                FieldDefinition("size_bytes", "integer", "Sanity-checks recovery and change detection."),
            ],
            optional_fields=[],
        ),
        EntityDefinition(
            name="ExtractedText",
            purpose="Text-bearing representation of one source snapshot with mapping back to source positions.",
            required_fields=[
                FieldDefinition("extracted_text_id", "string", "Provides a stable handle for extraction output."),
                FieldDefinition("source_document_id", "string", "Links the extraction back to the source identity."),
                FieldDefinition("source_snapshot_id", "string", "Pins extraction output to a specific source version."),
                FieldDefinition("extractor_name", "string", "Supports reproducibility across extraction pipelines."),
                FieldDefinition("extractor_version", "string", "Supports change-aware reprocessing."),
                FieldDefinition("extraction_mode", "string", "Distinguishes parser, OCR, or hybrid text generation."),
                FieldDefinition("text", "string", "Carries the extracted text payload."),
                FieldDefinition("text_hash", "string", "Supports integrity checks on extracted payload."),
                FieldDefinition("span_map", "SpanMapEntry[]", "Preserves traceable mappings from extracted text into source spans."),
                FieldDefinition("fidelity_state", "FidelityState", "Communicates whether extraction is exact, lossy, or partial."),
            ],
            optional_fields=[],
        ),
        EntityDefinition(
            name="StructuralRegion",
            purpose="Source-preserving structural span such as a section, log event, or code block.",
            required_fields=[
                FieldDefinition("structural_region_id", "string", "Provides a stable structural anchor."),
                FieldDefinition("source_document_id", "string", "Links the region back to the source identity."),
                FieldDefinition("source_snapshot_id", "string", "Pins the region to a specific source version."),
                FieldDefinition("extracted_text_id", "string", "Connects structure to its extraction layer."),
                FieldDefinition("region_type", "string", "Names the structural boundary semantics."),
                FieldDefinition("primary_span", "AnchoredSpan", "Preserves exact or fallback source coverage."),
                FieldDefinition("ordinal", "integer", "Maintains ordered traversal within a source."),
                FieldDefinition("content_facets", "string[]", "Preserves modality mix such as prose, SQL, or shell."),
                FieldDefinition("boundary_basis", "string", "Explains why this structural boundary exists."),
            ],
            optional_fields=[
                FieldDefinition("parent_region_id", "string", "Supports hierarchy for nested sections or sessions."),
                FieldDefinition("prev_region_id", "string", "Supports left-context recovery."),
                FieldDefinition("next_region_id", "string", "Supports right-context recovery."),
            ],
        ),
        EntityDefinition(
            name="EvidenceUnit",
            purpose="Smallest retrieval-ready and explanation-ready source-faithful support object.",
            required_fields=[
                FieldDefinition("evidence_unit_id", "string", "Provides the durable retrieval identity."),
                FieldDefinition("source_document_id", "string", "Pins evidence to the stable source identity."),
                FieldDefinition("source_snapshot_id", "string", "Pins evidence to the exact source version."),
                FieldDefinition("unit_type", "string", "Expresses retrieval semantics such as section, event, or snippet."),
                FieldDefinition("canonical_text", "string", "Carries the retrieval and explanation payload."),
                FieldDefinition("support_links", "ProvenanceLink[]", "Preserves exact source traceability."),
                FieldDefinition("structural_region_ids", "string[]", "Anchors the unit inside structural context."),
                FieldDefinition("ordinal", "integer", "Supports ordered expansion and adjacency."),
                FieldDefinition("boundary_rationale", "string", "Explains why the unit boundary is trustworthy."),
                FieldDefinition("content_facets", "string[]", "Preserves modality for retrieval and explanation."),
                FieldDefinition("trust_state", "TrustState", "Communicates whether the unit is segmented, verified, or ambiguous."),
            ],
            optional_fields=[
                FieldDefinition("parent_region_id", "string", "Preserves the enclosing structural anchor for recovery."),
                FieldDefinition("parent_evidence_unit_id", "string", "Supports hierarchical evidence groupings."),
                FieldDefinition("previous_unit_id", "string", "Supports grounded context expansion to the left."),
                FieldDefinition("next_unit_id", "string", "Supports grounded context expansion to the right."),
                FieldDefinition("confidence", "ConfidenceLevel", "Communicates coarse confidence without opaque scoring."),
                FieldDefinition("flags", "string[]", "Carries simple boundary or mixed-content warnings."),
            ],
            invariants=[
                "support_links must not be empty",
                "at least one support link must be recoverable and carry a non-empty locator",
            ],
        ),
        EntityDefinition(
            name="DerivedArtifact",
            purpose="Typed secondary view such as a command, query, or link index derived from evidence.",
            required_fields=[
                FieldDefinition("derived_artifact_id", "string", "Provides a stable artifact identity."),
                FieldDefinition("artifact_type", "string", "Names the artifact family."),
                FieldDefinition("artifact_schema_version", "string", "Supports artifact evolution without schema drift."),
                FieldDefinition("source_scope", "SourceScope", "Declares which sources or snapshots the artifact covers."),
                FieldDefinition("derived_from", "RecordRef[]", "Preserves traceability to evidence or structure."),
                FieldDefinition("payload", "object", "Carries the artifact's typed data."),
                FieldDefinition("producer_name", "string", "Captures which component built the artifact."),
                FieldDefinition("producer_version", "string", "Supports reproducibility and refresh logic."),
                FieldDefinition("artifact_hash", "string", "Supports integrity and cache validity."),
            ],
            optional_fields=[],
            invariants=[
                "DerivedArtifact never replaces source truth and must resolve back through derived_from",
            ],
        ),
    ],
)


EXTENDED_SCHEMA = SchemaProfile(
    name="extended",
    summary="Extended object schema with ambiguity, retrieval, audit, and multi-pass extraction support.",
    shared_types=SHARED_TYPE_DEFINITIONS,
    entities=[
        EntityDefinition(
            name="SourceDocument",
            purpose="Canonical identity and recovery anchor for an original local artifact snapshot.",
            required_fields=MINIMAL_SCHEMA.entity_map()["SourceDocument"].required_fields,
            optional_fields=[
                FieldDefinition("display_name", "string", "Gives humans a stable label for inspection and debugging."),
                FieldDefinition("relative_path", "string", "Supports workspace-relative portability."),
                FieldDefinition("encoding", "string", "Preserves source decoding assumptions."),
                FieldDefinition("language_hint", "string", "Steers parsing for code or mixed-language inputs."),
                FieldDefinition("created_at", "string", "Supports change-aware lifecycle tracking."),
                FieldDefinition("modified_at", "string", "Supports change-aware lifecycle tracking."),
                FieldDefinition("collector", "string", "Captures how the source entered the ingestion pipeline."),
                FieldDefinition("content_facets", "string[]", "Carries early mixed-content hints into downstream stages."),
                FieldDefinition("source_metadata", "object", "Retains format-specific metadata without flattening it."),
            ],
        ),
        EntityDefinition(
            name="ExtractedText",
            purpose="Text-bearing representation of one source snapshot with mapping back to source positions.",
            required_fields=MINIMAL_SCHEMA.entity_map()["ExtractedText"].required_fields,
            optional_fields=[
                FieldDefinition("line_index", "LineIndexEntry[]", "Supports fast line-oriented recovery."),
                FieldDefinition("page_map", "PageMapEntry[]", "Preserves page-level navigation in extracted text."),
                FieldDefinition("block_map", "BlockMapEntry[]", "Preserves layout or block boundaries."),
                FieldDefinition("normalization_notes", "NormalizationNote[]", "Audits conservative normalization steps."),
                FieldDefinition("language_spans", "LanguageSpan[]", "Preserves mixed-language or mixed-format spans."),
                FieldDefinition("errors", "ExtractionError[]", "Retains extraction failures without hiding them."),
                FieldDefinition("ambiguity", "AmbiguityMarker[]", "Preserves unresolved extraction uncertainty."),
                FieldDefinition("content_facets", "string[]", "Carries modality hints forward into segmentation."),
            ],
        ),
        EntityDefinition(
            name="StructuralRegion",
            purpose="Source-preserving structural span such as a section, log event, or code block.",
            required_fields=MINIMAL_SCHEMA.entity_map()["StructuralRegion"].required_fields,
            optional_fields=[
                FieldDefinition("parent_region_id", "string", "Supports hierarchy for nested sections or sessions."),
                FieldDefinition("prev_region_id", "string", "Supports left-context recovery."),
                FieldDefinition("next_region_id", "string", "Supports right-context recovery."),
                FieldDefinition("label", "string", "Carries a concise structural label such as a heading or symbol name."),
                FieldDefinition("heading_path", "string[]", "Preserves enclosing section context."),
                FieldDefinition("supporting_spans", "AnchoredSpan[]", "Allows faithful multi-span structure when explicitly related."),
                FieldDefinition("ambiguity", "AmbiguityMarker[]", "Captures structural uncertainty without flattening it."),
                FieldDefinition("quality_flags", "string[]", "Supports downstream ranking and inspection."),
                FieldDefinition("children_count", "integer", "Supports hierarchy-aware traversal."),
            ],
        ),
        EntityDefinition(
            name="EvidenceUnit",
            purpose="Smallest retrieval-ready and explanation-ready source-faithful support object.",
            required_fields=MINIMAL_SCHEMA.entity_map()["EvidenceUnit"].required_fields,
            optional_fields=[
                FieldDefinition("parent_region_id", "string", "Preserves the enclosing structural anchor for recovery."),
                FieldDefinition("parent_evidence_unit_id", "string", "Supports hierarchical evidence groupings."),
                FieldDefinition("previous_unit_id", "string", "Supports grounded context expansion to the left."),
                FieldDefinition("next_unit_id", "string", "Supports grounded context expansion to the right."),
                FieldDefinition("confidence", "ConfidenceLevel", "Communicates coarse confidence without opaque scoring."),
                FieldDefinition("flags", "string[]", "Carries simple boundary or mixed-content warnings."),
                FieldDefinition("context_labels", "string[]", "Carries heading, symbol, or session context into retrieval."),
                FieldDefinition("ambiguity", "AmbiguityMarker[]", "Preserves support uncertainty for explanation."),
                FieldDefinition("integrity_flags", "string[]", "Captures content warnings such as truncation or overlap."),
                FieldDefinition("keywords", "string[]", "Supports lexical retrieval and diagnostics."),
                FieldDefinition("entity_refs", "EntityRef[]", "Supports grounded entity-aware retrieval."),
                FieldDefinition("conflict_refs", "RecordRef[]", "Links contradictory or competing evidence."),
                FieldDefinition("expansion_policy_hint", "string", "Guides how grounded explanation should expand context."),
            ],
            invariants=MINIMAL_SCHEMA.entity_map()["EvidenceUnit"].invariants,
        ),
        EntityDefinition(
            name="DerivedArtifact",
            purpose="Typed secondary view such as a command, query, or link index derived from evidence.",
            required_fields=MINIMAL_SCHEMA.entity_map()["DerivedArtifact"].required_fields,
            optional_fields=[
                FieldDefinition("target_refs", "ArtifactTarget[]", "Preserves extracted commands, links, or queries with upstream support."),
                FieldDefinition("ambiguity", "AmbiguityMarker[]", "Carries unresolved ambiguity into derived views."),
                FieldDefinition("confidence", "float", "Supports ranking without replacing typed ambiguity."),
                FieldDefinition("refresh_state", "string", "Supports incremental rebuild workflows."),
                FieldDefinition("notes", "string", "Preserves operator-facing artifact notes."),
                FieldDefinition("backfill_needed", "boolean", "Flags artifacts that need later enrichment."),
                FieldDefinition("retrieval_tags", "string[]", "Supports routing and index partitioning."),
            ],
            invariants=MINIMAL_SCHEMA.entity_map()["DerivedArtifact"].invariants,
        ),
    ],
)


SCHEMA_PROFILES = {
    "minimal": MINIMAL_SCHEMA,
    "extended": EXTENDED_SCHEMA,
}


__all__ = [
    "AmbiguityAlternative",
    "AmbiguityMarker",
    "AmbiguitySeverity",
    "AnchoredSpan",
    "ArtifactTarget",
    "BoundingBox",
    "ConfidenceLevel",
    "DataModelValidationError",
    "DerivedArtifact",
    "EntityDefinition",
    "EntityRef",
    "EvidenceGraph",
    "EvidenceRecovery",
    "EvidenceUnit",
    "ExtractionError",
    "ExtractedText",
    "EXTENDED_SCHEMA",
    "FieldDefinition",
    "FidelityState",
    "LanguageSpan",
    "LineIndexEntry",
    "MINIMAL_SCHEMA",
    "NormalizationNote",
    "PageMapEntry",
    "ProvenanceLink",
    "RecordRef",
    "ResolverStatus",
    "SCHEMA_PROFILES",
    "SchemaProfile",
    "SourceDocument",
    "SourceLocator",
    "SourceScope",
    "SpanMapEntry",
    "StructuralRegion",
    "TextSpan",
    "TrustState",
]
