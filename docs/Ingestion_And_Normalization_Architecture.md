# Ingestion And Normalization Architecture

## How to use this document

This is the input-contract document.

Use this file to answer only these questions:
- What must ingestion preserve?
- What must extraction produce?
- What is normalization allowed to change?
- What must remain recoverable back to source?

This document defines the rules for getting from raw source to segmentation-ready text without losing source truth.
It should not define segmentation policy or implementation backlog.

**Design**
Use three distinct contracts, not one blended pipeline:

- Ingestion creates immutable source snapshots and routing metadata.
- Extraction produces source-mapped text views.
- Normalization produces segmentation-ready text views without replacing raw extraction.

The governing rule is simple: raw source stays authoritative, and every normalized span must either map back to source or be explicitly marked as uncertain.

**1. Ingestion Layer Responsibilities**
- Discover and admit local files into the system as `SourceDocument` snapshots.
- Assign stable `source_document_id` and exact `source_snapshot_id`; snapshots are immutable once recorded.
- Capture source identity and recovery metadata: `source_uri`, `relative_path`, `mime_type`, `source_kind`, `content_hash`, `size_bytes`, timestamps, encoding hints.
- Preserve original source access. Ingestion must never rewrite the file and must keep a recoverable path or content-addressed snapshot reference.
- Perform file-type routing only. It decides which extractor profile to invoke; it does not parse meaning.
- Detect change and deduplicate by snapshot hash so reprocessing is incremental and stable.
- Record source-level ambiguity early when obvious: unknown encoding, mixed binary/text, truncated file, partial read, unsupported container.
- Emit a complete source record even if extraction later fails; failure to extract must not erase source visibility.

**2. Extraction Layer Responsibilities**
- Convert one `SourceDocument` snapshot into one or more `ExtractedText` records.
- Preserve source order exactly as extracted; no hidden cleanup or semantic rewriting.
- Produce text plus position mapping: `text`, `text_hash`, `span_map[]`, and where possible `line_index[]`, `page_map[]`, `block_map[]`.
- Emit extractor provenance: `extractor_name`, `extractor_version`, `extraction_mode`, `fidelity_state`, `errors[]`, `ambiguity[]`, `content_facets[]`.
- Preserve mixed content rather than flattening it; extraction should label prose/code/sql/table/output spans when detectable.
- Represent degraded fidelity honestly. If page/block anchors exist but char offsets do not, emit only page/block anchors and mark ambiguity.

Segmentation should require from extraction:
- A source-snapshot-aligned `ExtractedText` record.
- Stable text order.
- `span_map[]` coverage for all extracted content, or explicit gaps.
- Best available locators: line for line-oriented sources, page/block/bbox for layout sources.
- `fidelity_state`, `errors[]`, and `ambiguity[]`.
- No implicit normalization beyond what is explicitly recorded.

**3. Normalization Layer Responsibilities**
- Accept extracted text as input and emit a segmentation-ready text view that is still tied to the same `SourceDocument` snapshot.
- Never overwrite the raw extraction. Normalized output should be a sibling derived view, not a replacement.
- Apply only conservative, deterministic transformations that improve structural readability or boundary detection.
- Preserve or rebuild span mapping so normalized text can still resolve to `SourceLocator` values.
- Emit `normalization_notes[]` and ambiguity markers for every non-trivial change.
- Surface uncertainty instead of guessing. If normalization cannot preserve truthful mapping, keep the raw form and mark the issue rather than fabricating a cleaner view.
- Preserve modality and structure cues needed downstream: headings, code fences, indentation, list boundaries, log-event boundaries, SQL delimiters, page/block breaks.

**4. Transformation Policy**
Allowed transformations:
- Line-ending normalization.
- BOM removal and other non-content wrapper cleanup.
- Unicode normalization only when deterministic and logged.
- Whitespace cleanup that does not change semantic tokens and remains span-recoverable.
- Explicit preservation markers for structure: heading levels, fenced block boundaries, page breaks, table row boundaries.
- Soft-wrap repair or PDF dehyphenation only when the operation is deterministic, mapped, and flagged.
- Splitting combined extracted blobs into ordered structural text segments without rewriting text.

Not allowed transformations:
- Paraphrasing, summarization, or “cleanup” that changes wording.
- Spell-correction or OCR guessing presented as source truth.
- Reordering text, merging distant spans, or dropping duplicated-looking material.
- Reformatting code or SQL as the canonical normalized text.
- Converting tables, lists, or logs into prose summaries.
- Inventing headings, punctuation, delimiters, timestamps, or line numbers.
- Hiding extractor uncertainty by replacing it with a confident normalized form.

Decision rule:
- A transformation is allowed only if it is deterministic, local, traceable, and either reversible or span-recoverable.

**5. Normalization Audit Trail Model**
Use a two-level audit model.

`NormalizationRun`
- `normalization_run_id`
- `source_document_id`
- `source_snapshot_id`
- `input_extracted_text_id`
- `output_extracted_text_id`
- `normalizer_name`
- `normalizer_version`
- `policy_version`
- `created_at`
- `overall_fidelity_state`
- `summary`

`NormalizationEvent`
- `event_id`
- `normalization_run_id`
- `sequence_no`
- `operation_type`
- `reason`
- `input_text_span`
- `output_text_span`
- `input_locators[]`
- `reversible`
- `semantic_risk`
- `before_hash`
- `after_hash`
- `ambiguity_emitted[]`
- `warnings[]`

Architecture rules:
- Every normalized view must point to its raw extracted input.
- Every event must identify the exact input and output spans it changed.
- Audit records are append-only.
- If a transform cannot identify input/output span correspondence, it must be marked lossy and segmentation should treat it cautiously.

**6. Source-Specific Considerations**
`markdown`
- Preserve heading hierarchy, lists, blockquotes, fenced code blocks, tables-as-text, link text, and link targets.
- Do not flatten code fences into prose or resolve reference links by replacing visible source text.
- Front matter should stay distinct, not silently merged into body text.

`txt`
- Treat as low-structure input; preserve line order, blank lines, indentation, delimiter runs, and paragraph breaks.
- Avoid paragraph reflow unless the source explicitly indicates soft wraps.

`logs`
- Preserve timestamps, severity, host/process/request IDs, and exact event order.
- Multiline events should stay grouped only when grouping evidence is explicit; otherwise mark ambiguity.
- Never deduplicate repeated lines during normalization.

`code`
- Preserve exact tokens, comments, docstrings, indentation, and line numbers.
- Structure can be added as annotations, but the normalized text must not be formatter output.
- Do not strip comments, reorder imports, or expand macros/templating into inferred source.

`sql`
- Preserve exact statements, delimiters, comments, dialect cues, and statement order.
- Statement boundaries may be annotated, but pretty-printing, uppercasing keywords, or rewriting aliases should not become canonical normalized text.
- Do not interpolate variables or resolve external references during normalization.

`pdf-extracted text`
- Expect degraded structure; require page, block, and bbox data when available.
- Keep reading-order uncertainty explicit.
- Dehyphenation, column stitching, and header/footer suppression must be logged and marked when uncertain.
- Never invent char offsets if only page/block anchors exist.

`docx-extracted text`
- Preserve paragraph boundaries, heading levels, lists, tables, section breaks, footnotes, and comments when available from extraction.
- Merging runs into paragraph text is acceptable if the run-to-paragraph mapping remains recoverable.
- Tables should remain table-like in the normalized view, not flattened into plain paragraphs unless that loss is explicit.

The contract to hold across all source types is: extraction may be imperfect, normalization may be helpful, but neither layer is allowed to replace source truth or hide uncertainty.