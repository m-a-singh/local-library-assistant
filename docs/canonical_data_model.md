# Canonical Data Model

## How to use this document

This is the contract document for the system.

Use this file to answer only these questions:
- What are the core entities?
- What fields does each entity carry?
- What invariants must always hold?
- What does each layer read and write?

This document should describe the canonical objects shared across the pipeline.
It should not become a design brainstorming doc or an implementation task list.

This directory now contains a concrete Phase 1 implementation in [canonical_data_model.py](../canonical_data_model.py).

## Purpose

The model is built around a source-faithful pipeline:

`SourceDocument -> ExtractedText -> StructuralRegion -> EvidenceUnit -> DerivedArtifact`

It is intentionally:

- local-first
- storage-agnostic
- provenance-first
- recoverable to source
- explicit about ambiguity instead of hiding it

## Shared Types

### `SourceLocator`

Purpose: best recoverable source position without inventing unavailable precision.

Required fields:

- `source_uri`

Optional fields:

- `page`
- `block`
- `line_start`
- `line_end`
- `char_start`
- `char_end`
- `byte_start`
- `byte_end`
- `bbox`

Why they matter: this system has to support plain text, code, logs, PDFs, docx extractions, and OCR-like flows. Some sources give exact line and char positions; others only give page or layout anchors. `SourceLocator` carries the best truthful locator available.

### `ProvenanceLink`

Purpose: explicit trace from downstream records back to exact source support.

Required fields:

- `source_document_id`
- `source_snapshot_id`
- `locator`
- `role`
- `recoverable`

Optional fields:

- `upstream_entity_refs`
- `text_span`

Why they matter: every `EvidenceUnit` must be able to prove where it came from. The model uses `ProvenanceLink` so explanations and audits can reconstruct the path from evidence back through extracted or structural layers to source.

### `AmbiguityMarker`

Purpose: structured uncertainty for extraction loss, mixed content, uncertain boundaries, or overlapping interpretations.

Required fields:

- `ambiguity_type`
- `reason`
- `severity`
- `resolver_status`

Optional fields:

- `affected_locator`
- `alternatives`

Why they matter: the model cannot pretend every source is clean. OCR, mixed prose plus code, missing offsets, or multiple plausible segmentations need to stay visible so retrieval and explanation can expose uncertainty instead of overclaiming precision.

## Core Entities

### `SourceDocument`

Purpose: canonical identity and recovery anchor for an original local source snapshot.

Required fields:

- `source_document_id`
- `source_snapshot_id`
- `source_uri`
- `source_kind`
- `mime_type`
- `content_hash`
- `size_bytes`

Optional fields:

- `display_name`
- `relative_path`
- `encoding`
- `language_hint`
- `created_at`
- `modified_at`
- `collector`
- `content_facets`
- `source_metadata`

Why they matter: `SourceDocument` is the source-of-truth anchor. Stable IDs support durable references across reprocessing, while snapshot IDs and hashes pin every downstream object to an exact source version. The optional metadata preserves file-specific context without flattening it into generic text.

### `ExtractedText`

Purpose: text-bearing representation of one source snapshot with mappings back to source positions.

Required fields:

- `extracted_text_id`
- `source_document_id`
- `source_snapshot_id`
- `extractor_name`
- `extractor_version`
- `extraction_mode`
- `text`
- `text_hash`
- `span_map`
- `fidelity_state`

Optional fields:

- `line_index`
- `page_map`
- `block_map`
- `normalization_notes`
- `language_spans`
- `errors`
- `ambiguity`
- `content_facets`

Why they matter: extracted text is where raw source becomes usable for segmentation. The schema preserves which extractor produced it, how faithful it is, and how each extracted segment maps back to source. The optional indexes and audit metadata preserve recoverability and trace conservative normalization decisions.

### `StructuralRegion`

Purpose: source-preserving structural span such as a section, code block, log event, or command-output pair.

Required fields:

- `structural_region_id`
- `source_document_id`
- `source_snapshot_id`
- `extracted_text_id`
- `region_type`
- `primary_span`
- `ordinal`
- `content_facets`
- `boundary_basis`

Optional fields:

- `parent_region_id`
- `prev_region_id`
- `next_region_id`
- `label`
- `heading_path`
- `supporting_spans`
- `ambiguity`
- `quality_flags`
- `children_count`

Why they matter: this layer preserves structure before final evidence shaping. Parent-child and adjacency links let the system recover local context even when segmentation is imperfect. `primary_span` plus optional `supporting_spans` allow faithful representation of regions that are ordered, hierarchical, or explicitly multi-span.

### `EvidenceUnit`

Purpose: smallest retrieval-ready, explanation-ready, provenance-bearing support object.

Required fields:

- `evidence_unit_id`
- `source_document_id`
- `source_snapshot_id`
- `unit_type`
- `canonical_text`
- `support_links`
- `structural_region_ids`
- `ordinal`
- `boundary_rationale`
- `content_facets`
- `trust_state`

Optional fields:

- `parent_evidence_unit_id`
- `prev_evidence_unit_id`
- `next_evidence_unit_id`
- `context_labels`
- `ambiguity`
- `integrity_flags`
- `keywords`
- `entity_refs`
- `conflict_refs`
- `expansion_policy_hint`

Why they matter: this is the primary retrieval object. It has enough structure to rank, explain, expand, and audit without drifting away from the source. The implementation enforces the main invariant: every evidence unit must have at least one recoverable source locator, and each support link must match the evidence unit's source identity and snapshot.

### `DerivedArtifact`

Purpose: typed secondary view such as a command index, query index, link index, or citation graph.

Required fields:

- `derived_artifact_id`
- `artifact_type`
- `artifact_schema_version`
- `source_scope`
- `derived_from`
- `payload`
- `producer_name`
- `producer_version`
- `artifact_hash`

Optional fields:

- `target_refs`
- `ambiguity`
- `confidence`
- `refresh_state`
- `notes`
- `backfill_needed`
- `retrieval_tags`

Why they matter: downstream indexes are useful, but they must never become source truth. `DerivedArtifact` keeps artifact payloads explicitly tied back to `EvidenceUnit` or `StructuralRegion` references so the system can always recover grounded support.

## Minimal And Extended Schema

Two schema profiles are exported:

- `MINIMAL_SCHEMA`: only the fields required to support ingestion, provenance, segmentation, and recoverable derived artifacts.
- `EXTENDED_SCHEMA`: adds audit, ambiguity, retrieval, and multi-pass extraction support.

Both profiles are exported as structured `SchemaProfile` objects in [canonical_data_model.py](../canonical_data_model.py), so later tooling can render docs, JSON, or implementation-specific storage layers from the same canonical contract.

## Recovery And Validation

The module also includes `EvidenceGraph`, an in-memory registry that:

- validates cross-record references
- enforces snapshot consistency across layers
- resolves `DerivedArtifact.derived_from` references
- reconstructs grounded recovery views from an `EvidenceUnit`

This keeps the current implementation aligned with the central requirement: every explanation-ready unit must be traceable back to source.
