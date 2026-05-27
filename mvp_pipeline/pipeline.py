"""End-to-end pipeline orchestration for the MVP slice."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from canonical_data_model import (
    DerivedArtifact,
    EvidenceUnit,
    ExtractedText,
    SourceDocument,
    StructuralRegion,
)
from query_retriever import EvidenceUnit as IndexedEvidenceUnit
from query_retriever import ingest_evidence_units

from .derived_artifacts import build_derived_artifacts
from .evidence import (
    build_evidence_unit_index,
    build_evidence_units,
    collect_neighbor_units,
    expand_unit_context,
)
from .ingest import ingest_local_file, is_supported_source_file
from .normalize import SegmentationInput, build_segmentation_input, normalize_extracted_text
from .segment import build_structural_regions

DEFAULT_EVIDENCE_DB_PATH = "evidence_units.db"


@dataclass(frozen=True)
class PipelineResult:
    source_document: SourceDocument
    raw_extracted_text: ExtractedText
    normalized_extracted_text: ExtractedText
    segmentation_input: SegmentationInput
    structural_regions: list[StructuralRegion]
    evidence_units: list[EvidenceUnit]

    def get_neighbors(self, unit: EvidenceUnit, k: int) -> list[EvidenceUnit]:
        units_by_id = build_evidence_unit_index(self.evidence_units)
        return collect_neighbor_units(unit, units_by_id, k)

    def expand_context(self, unit: EvidenceUnit, n_units: int) -> list[EvidenceUnit]:
        units_by_id = build_evidence_unit_index(self.evidence_units)
        return expand_unit_context(unit, units_by_id, n_units)

    def build_derived_artifacts(self) -> list[DerivedArtifact]:
        return build_derived_artifacts(self.source_document, self.evidence_units)


def _to_indexed_evidence_unit(source_document: SourceDocument, evidence_unit: EvidenceUnit) -> IndexedEvidenceUnit:
    text_span = evidence_unit.text_span
    source_file = evidence_unit.source_uri or source_document.source_uri
    signals = {signal: True for signal in evidence_unit.signals}
    if evidence_unit.flags:
        signals["flags"] = list(evidence_unit.flags)
    signals["confidence"] = evidence_unit.confidence.value
    signals["trust_state"] = evidence_unit.trust_state.value

    region: dict[str, object | None] = {
        "parent_region_id": evidence_unit.parent_region_id,
        "structural_region_ids": list(evidence_unit.structural_region_ids),
        "line_start": evidence_unit.line_start,
        "line_end": evidence_unit.line_end,
        "char_start": evidence_unit.char_start,
        "char_end": evidence_unit.char_end,
        "text_span_start": text_span.start if text_span is not None else None,
        "text_span_end": text_span.end if text_span is not None else None,
        "ordinal": evidence_unit.ordinal,
    }
    structural_context = {
        "source_document_id": evidence_unit.source_document_id,
        "source_snapshot_id": evidence_unit.source_snapshot_id,
        "content_facets": list(evidence_unit.content_facets),
        "context_labels": list(evidence_unit.context_labels),
        "boundary_rationale": evidence_unit.boundary_rationale,
    }
    return IndexedEvidenceUnit(
        unit_id=evidence_unit.evidence_unit_id,
        type=evidence_unit.unit_type,
        source_file=source_file,
        region=region,
        signals=signals,
        text=evidence_unit.canonical_text,
        structural_context=structural_context,
        prev_unit_id=evidence_unit.previous_unit_id,
        next_unit_id=evidence_unit.next_unit_id,
    )


def _persist_evidence_units(
    source_document: SourceDocument,
    evidence_units: list[EvidenceUnit],
    *,
    db_path: str = DEFAULT_EVIDENCE_DB_PATH,
    mode: str = "upsert",
) -> None:
    indexed_units = [_to_indexed_evidence_unit(source_document, evidence_unit) for evidence_unit in evidence_units]
    ingest_evidence_units(indexed_units, db_path=db_path, mode=mode)
    print(f"Indexed {len(indexed_units)} EvidenceUnits into {db_path}")


def run_structural_segmentation(path: str | Path) -> list[StructuralRegion]:
    source_document, raw_extracted_text = ingest_local_file(path)
    normalized_extracted_text = normalize_extracted_text(raw_extracted_text)
    segmentation_input = build_segmentation_input(normalized_extracted_text)
    return build_structural_regions(source_document, segmentation_input)


def run_pipeline(
    path: str | Path,
    *,
    db_path: str = DEFAULT_EVIDENCE_DB_PATH,
    write_index: bool = True,
) -> PipelineResult:
    source_document, raw_extracted_text = ingest_local_file(path)
    normalized_extracted_text = normalize_extracted_text(raw_extracted_text)
    segmentation_input = build_segmentation_input(normalized_extracted_text)
    structural_regions = build_structural_regions(source_document, segmentation_input)
    evidence_units = build_evidence_units(source_document, normalized_extracted_text, structural_regions)
    if write_index:
        _persist_evidence_units(source_document, evidence_units, db_path=db_path, mode="upsert")
    return PipelineResult(
        source_document=source_document,
        raw_extracted_text=raw_extracted_text,
        normalized_extracted_text=normalized_extracted_text,
        segmentation_input=segmentation_input,
        structural_regions=structural_regions,
        evidence_units=evidence_units,
    )


def run_folder_pipeline(
    folder_path: str | Path,
    *,
    db_path: str = DEFAULT_EVIDENCE_DB_PATH,
) -> list[PipelineResult]:
    folder = Path(folder_path).expanduser().resolve()
    results: list[PipelineResult] = []
    indexed_units: list[IndexedEvidenceUnit] = []
    total_files = 0
    skipped_unsupported = 0
    skipped_failed = 0

    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        total_files += 1
        if not is_supported_source_file(path):
            skipped_unsupported += 1
            continue
        try:
            result = run_pipeline(path, db_path=db_path, write_index=False)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            skipped_failed += 1
            print(f"Skipped file: {path} ({exc})", file=sys.stderr)
            continue
        results.append(result)
        indexed_units.extend(
            _to_indexed_evidence_unit(result.source_document, evidence_unit) for evidence_unit in result.evidence_units
        )

    ingest_evidence_units(indexed_units, db_path=db_path, mode="replace")
    print(
        "Folder pipeline summary: "
        f"total={total_files} "
        f"processed={len(results)} "
        f"skipped_unsupported={skipped_unsupported} "
        f"skipped_failed={skipped_failed}"
    )
    print(f"Indexed {len(indexed_units)} EvidenceUnits into {db_path}")
    return results
