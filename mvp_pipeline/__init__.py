"""Thin MVP evidence pipeline for local `.txt` and `.md` files."""

from .derived_artifacts import (
    build_command_index,
    build_derived_artifacts,
    build_link_index,
    build_query_index,
)
from .evidence import build_evidence_units
from .export_markdown import (
    BatchMarkdownExportResult,
    MarkdownExportFile,
    MarkdownExportResult,
    SkippedSourceFile,
    discover_source_files,
    export_evidence_units_to_markdown,
    export_folder_to_markdown,
    export_pipeline_results_to_markdown,
)
from .ingest import ingest_local_file
from .normalize import (
    SegmentationInput,
    SegmentationLine,
    build_segmentation_input,
    normalize_extracted_text,
)
from .pipeline import (
    DEFAULT_EVIDENCE_DB_PATH,
    PipelineResult,
    run_folder_pipeline,
    run_pipeline,
    run_structural_segmentation,
)
from .segment import SegmentationProfile, build_structural_regions, select_segmentation_profile

__all__ = [
    "MarkdownExportFile",
    "MarkdownExportResult",
    "BatchMarkdownExportResult",
    "DEFAULT_EVIDENCE_DB_PATH",
    "PipelineResult",
    "SegmentationInput",
    "SegmentationLine",
    "SegmentationProfile",
    "SkippedSourceFile",
    "build_command_index",
    "build_derived_artifacts",
    "build_evidence_units",
    "discover_source_files",
    "build_segmentation_input",
    "build_structural_regions",
    "export_evidence_units_to_markdown",
    "export_folder_to_markdown",
    "export_pipeline_results_to_markdown",
    "build_link_index",
    "build_query_index",
    "ingest_local_file",
    "normalize_extracted_text",
    "run_folder_pipeline",
    "run_pipeline",
    "run_structural_segmentation",
    "select_segmentation_profile",
]
