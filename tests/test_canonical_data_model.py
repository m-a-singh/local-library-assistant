import unittest

from canonical_data_model import (
    MINIMAL_SCHEMA,
    AmbiguityMarker,
    AmbiguitySeverity,
    AnchoredSpan,
    ArtifactTarget,
    DataModelValidationError,
    DerivedArtifact,
    EvidenceGraph,
    EvidenceUnit,
    ExtractedText,
    FidelityState,
    ProvenanceLink,
    RecordRef,
    ResolverStatus,
    SourceDocument,
    SourceLocator,
    SourceScope,
    SpanMapEntry,
    StructuralRegion,
    TextSpan,
    TrustState,
)


class CanonicalDataModelTest(unittest.TestCase):
    def test_mixed_markdown_regions_preserve_modalities_and_hierarchy(self) -> None:
        graph = EvidenceGraph()
        source = SourceDocument(
            source_document_id="doc:note-1",
            source_snapshot_id="snap:note-1:v1",
            source_uri="/knowledge/mixed_note.md",
            source_kind="markdown",
            mime_type="text/markdown",
            content_hash="sha256:note-1",
            size_bytes=412,
            content_facets=["prose", "sql", "shell"],
        )
        graph.add_source_document(source)

        extracted = ExtractedText(
            extracted_text_id="ext:note-1:v1",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            extractor_name="markdown-parser",
            extractor_version="1.0.0",
            extraction_mode="parser",
            text="# Notes\nRun this:\nSELECT * FROM users;\n$ psql -f setup.sql\n",
            text_hash="sha256:ext-note-1",
            fidelity_state=FidelityState.EXACT,
            span_map=[
                SpanMapEntry(
                    text_span=TextSpan(0, 12),
                    locator=SourceLocator(
                        source_uri=source.source_uri,
                        line_start=1,
                        line_end=2,
                        char_start=0,
                        char_end=12,
                    ),
                ),
                SpanMapEntry(
                    text_span=TextSpan(12, 33),
                    locator=SourceLocator(
                        source_uri=source.source_uri,
                        line_start=3,
                        line_end=3,
                        char_start=13,
                        char_end=33,
                    ),
                ),
                SpanMapEntry(
                    text_span=TextSpan(33, 53),
                    locator=SourceLocator(
                        source_uri=source.source_uri,
                        line_start=4,
                        line_end=4,
                        char_start=34,
                        char_end=53,
                    ),
                ),
            ],
            content_facets=["prose", "sql", "shell"],
        )
        graph.add_extracted_text(extracted)

        parent = StructuralRegion(
            structural_region_id="region:note-1:section",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            extracted_text_id=extracted.extracted_text_id,
            region_type="section",
            primary_span=AnchoredSpan(
                locator=SourceLocator(source_uri=source.source_uri, line_start=1, line_end=4),
                text_span=TextSpan(0, 53),
            ),
            ordinal=0,
            content_facets=["prose", "sql", "shell"],
            boundary_basis="markdown heading",
            label="Notes",
            children_count=2,
        )
        graph.add_structural_region(parent)

        sql_region = StructuralRegion(
            structural_region_id="region:note-1:sql",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            extracted_text_id=extracted.extracted_text_id,
            region_type="sql_statement",
            primary_span=AnchoredSpan(
                locator=SourceLocator(
                    source_uri=source.source_uri,
                    line_start=3,
                    line_end=3,
                    char_start=13,
                    char_end=33,
                ),
                text_span=TextSpan(12, 33),
            ),
            ordinal=1,
            content_facets=["sql"],
            boundary_basis="syntax statement",
            parent_region_id=parent.structural_region_id,
        )
        shell_region = StructuralRegion(
            structural_region_id="region:note-1:shell",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            extracted_text_id=extracted.extracted_text_id,
            region_type="command_line",
            primary_span=AnchoredSpan(
                locator=SourceLocator(
                    source_uri=source.source_uri,
                    line_start=4,
                    line_end=4,
                    char_start=34,
                    char_end=53,
                ),
                text_span=TextSpan(33, 53),
            ),
            ordinal=2,
            content_facets=["shell"],
            boundary_basis="line command",
            parent_region_id=parent.structural_region_id,
            prev_region_id=sql_region.structural_region_id,
        )
        graph.add_structural_region(sql_region)
        graph.add_structural_region(shell_region)

        sql_evidence = EvidenceUnit(
            evidence_unit_id="evidence:note-1:sql",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            unit_type="sql_statement",
            canonical_text="SELECT * FROM users;",
            support_links=[
                ProvenanceLink(
                    source_document_id=source.source_document_id,
                    source_snapshot_id=source.source_snapshot_id,
                    locator=SourceLocator(
                        source_uri=source.source_uri,
                        line_start=3,
                        line_end=3,
                        char_start=13,
                        char_end=33,
                    ),
                    text_span=TextSpan(12, 33),
                    role="primary_support",
                    recoverable=True,
                    upstream_entity_refs=[RecordRef("StructuralRegion", sql_region.structural_region_id)],
                )
            ],
            structural_region_ids=[sql_region.structural_region_id],
            ordinal=1,
            boundary_rationale="single SQL statement is coherent and traceable",
            content_facets=["sql"],
            trust_state=TrustState.SEGMENTED,
        )
        graph.add_evidence_unit(sql_evidence)

        self.assertEqual(parent.children_count, 2)
        self.assertEqual(sql_region.parent_region_id, parent.structural_region_id)
        self.assertEqual(shell_region.prev_region_id, sql_region.structural_region_id)
        self.assertEqual(sql_evidence.support_links[0].locator.line_start, 3)
        self.assertEqual(sql_evidence.content_facets, ["sql"])

    def test_pdf_fallback_locator_keeps_page_and_block_without_fake_char_offsets(self) -> None:
        graph = EvidenceGraph()
        source = SourceDocument(
            source_document_id="doc:pdf-1",
            source_snapshot_id="snap:pdf-1:v1",
            source_uri="/knowledge/manual.pdf",
            source_kind="pdf",
            mime_type="application/pdf",
            content_hash="sha256:pdf-1",
            size_bytes=8048,
        )
        graph.add_source_document(source)

        ambiguity = AmbiguityMarker(
            ambiguity_type="missing_char_offsets",
            reason="Extractor emitted page and block anchors but not stable text offsets.",
            severity=AmbiguitySeverity.MEDIUM,
            resolver_status=ResolverStatus.UNRESOLVED,
            affected_locator=SourceLocator(source_uri=source.source_uri, page=4, block="b12"),
        )
        extracted = ExtractedText(
            extracted_text_id="ext:pdf-1:v1",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            extractor_name="pdf-layout",
            extractor_version="2.1.0",
            extraction_mode="layout",
            text="Safety procedures ...",
            text_hash="sha256:ext-pdf-1",
            span_map=[
                SpanMapEntry(
                    text_span=TextSpan(0, 21),
                    locator=SourceLocator(source_uri=source.source_uri, page=4, block="b12"),
                )
            ],
            fidelity_state=FidelityState.PARTIAL,
            ambiguity=[ambiguity],
        )
        graph.add_extracted_text(extracted)

        region = StructuralRegion(
            structural_region_id="region:pdf-1:page4-block12",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            extracted_text_id=extracted.extracted_text_id,
            region_type="document_block",
            primary_span=AnchoredSpan(
                locator=SourceLocator(source_uri=source.source_uri, page=4, block="b12"),
                text_span=TextSpan(0, 21),
            ),
            ordinal=0,
            content_facets=["prose"],
            boundary_basis="pdf layout block",
            ambiguity=[ambiguity],
        )
        graph.add_structural_region(region)

        evidence = EvidenceUnit(
            evidence_unit_id="evidence:pdf-1:block12",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            unit_type="document_block",
            canonical_text="Safety procedures ...",
            support_links=[
                ProvenanceLink(
                    source_document_id=source.source_document_id,
                    source_snapshot_id=source.source_snapshot_id,
                    locator=SourceLocator(source_uri=source.source_uri, page=4, block="b12"),
                    role="primary_support",
                    recoverable=True,
                )
            ],
            structural_region_ids=[region.structural_region_id],
            ordinal=0,
            boundary_rationale="single layout block is the best recoverable support span",
            content_facets=["prose"],
            trust_state=TrustState.AMBIGUOUS,
            ambiguity=[ambiguity],
        )
        graph.add_evidence_unit(evidence)

        locator = evidence.support_links[0].locator
        self.assertEqual(locator.page, 4)
        self.assertEqual(locator.block, "b12")
        self.assertIsNone(locator.char_start)
        self.assertIsNone(locator.char_end)
        self.assertEqual(evidence.ambiguity[0].ambiguity_type, "missing_char_offsets")

    def test_shell_transcript_adjacency_and_command_index_trace_back_to_evidence(self) -> None:
        graph = EvidenceGraph()
        source = SourceDocument(
            source_document_id="doc:shell-1",
            source_snapshot_id="snap:shell-1:v1",
            source_uri="/knowledge/session.txt",
            source_kind="shell_transcript",
            mime_type="text/plain",
            content_hash="sha256:shell-1",
            size_bytes=123,
        )
        graph.add_source_document(source)
        extracted = ExtractedText(
            extracted_text_id="ext:shell-1:v1",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            extractor_name="line-reader",
            extractor_version="1.0.0",
            extraction_mode="native_text",
            text="$ ls\nfile.txt\n$ cat file.txt\nhello\n",
            text_hash="sha256:ext-shell-1",
            span_map=[
                SpanMapEntry(
                    text_span=TextSpan(0, 4),
                    locator=SourceLocator(source_uri=source.source_uri, line_start=1, line_end=1),
                ),
                SpanMapEntry(
                    text_span=TextSpan(14, 28),
                    locator=SourceLocator(source_uri=source.source_uri, line_start=3, line_end=3),
                ),
            ],
            fidelity_state=FidelityState.EXACT,
        )
        graph.add_extracted_text(extracted)

        session_region = StructuralRegion(
            structural_region_id="region:shell-1:session",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            extracted_text_id=extracted.extracted_text_id,
            region_type="shell_session",
            primary_span=AnchoredSpan(
                locator=SourceLocator(source_uri=source.source_uri, line_start=1, line_end=4),
                text_span=TextSpan(0, 34),
            ),
            ordinal=0,
            content_facets=["shell", "output"],
            boundary_basis="session transcript",
            children_count=2,
        )
        graph.add_structural_region(session_region)

        first_region = StructuralRegion(
            structural_region_id="region:shell-1:cmd1",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            extracted_text_id=extracted.extracted_text_id,
            region_type="command_with_output",
            primary_span=AnchoredSpan(
                locator=SourceLocator(source_uri=source.source_uri, line_start=1, line_end=2),
                text_span=TextSpan(0, 13),
            ),
            ordinal=1,
            content_facets=["shell", "output"],
            boundary_basis="command plus immediate output",
            parent_region_id=session_region.structural_region_id,
        )
        graph.add_structural_region(first_region)

        second_region = StructuralRegion(
            structural_region_id="region:shell-1:cmd2",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            extracted_text_id=extracted.extracted_text_id,
            region_type="command_with_output",
            primary_span=AnchoredSpan(
                locator=SourceLocator(source_uri=source.source_uri, line_start=3, line_end=4),
                text_span=TextSpan(14, 34),
            ),
            ordinal=2,
            content_facets=["shell", "output"],
            boundary_basis="command plus immediate output",
            parent_region_id=session_region.structural_region_id,
            prev_region_id=first_region.structural_region_id,
        )
        graph.add_structural_region(second_region)

        first_evidence = EvidenceUnit(
            evidence_unit_id="evidence:shell-1:cmd1",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            unit_type="command_with_output",
            canonical_text="$ ls\nfile.txt",
            support_links=[
                ProvenanceLink(
                    source_document_id=source.source_document_id,
                    source_snapshot_id=source.source_snapshot_id,
                    locator=SourceLocator(source_uri=source.source_uri, line_start=1, line_end=2),
                    role="primary_support",
                    recoverable=True,
                )
            ],
            structural_region_ids=[first_region.structural_region_id],
            ordinal=1,
            boundary_rationale="command plus adjacent output is the minimal coherent shell unit",
            content_facets=["shell", "output"],
            trust_state=TrustState.SEGMENTED,
            next_evidence_unit_id="evidence:shell-1:cmd2",
        )
        graph.add_evidence_unit(first_evidence)

        second_evidence = EvidenceUnit(
            evidence_unit_id="evidence:shell-1:cmd2",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            unit_type="command_with_output",
            canonical_text="$ cat file.txt\nhello",
            support_links=[
                ProvenanceLink(
                    source_document_id=source.source_document_id,
                    source_snapshot_id=source.source_snapshot_id,
                    locator=SourceLocator(source_uri=source.source_uri, line_start=3, line_end=4),
                    role="primary_support",
                    recoverable=True,
                )
            ],
            structural_region_ids=[second_region.structural_region_id],
            ordinal=2,
            boundary_rationale="command plus adjacent output is the minimal coherent shell unit",
            content_facets=["shell", "output"],
            trust_state=TrustState.SEGMENTED,
            prev_evidence_unit_id=first_evidence.evidence_unit_id,
        )
        graph.add_evidence_unit(second_evidence)

        artifact = DerivedArtifact(
            derived_artifact_id="artifact:shell-1:commands",
            artifact_type="command_index",
            artifact_schema_version="1.0",
            source_scope=SourceScope(
                scope_type="snapshot",
                source_document_ids=[source.source_document_id],
                source_snapshot_ids=[source.source_snapshot_id],
            ),
            derived_from=[
                RecordRef("EvidenceUnit", first_evidence.evidence_unit_id),
                RecordRef("EvidenceUnit", second_evidence.evidence_unit_id),
            ],
            payload={
                "commands": [
                    {"command": "ls", "evidence_unit_id": first_evidence.evidence_unit_id},
                    {
                        "command": "cat file.txt",
                        "evidence_unit_id": second_evidence.evidence_unit_id,
                    },
                ]
            },
            producer_name="command-indexer",
            producer_version="0.1.0",
            artifact_hash="sha256:artifact-shell-1",
            target_refs=[
                ArtifactTarget(
                    target_type="command",
                    value="ls",
                    evidence_unit_ids=[first_evidence.evidence_unit_id],
                    locator=SourceLocator(source_uri=source.source_uri, line_start=1, line_end=1),
                ),
                ArtifactTarget(
                    target_type="command",
                    value="cat file.txt",
                    evidence_unit_ids=[second_evidence.evidence_unit_id],
                    locator=SourceLocator(source_uri=source.source_uri, line_start=3, line_end=3),
                ),
            ],
        )
        graph.add_derived_artifact(artifact)

        self.assertEqual(second_region.prev_region_id, first_region.structural_region_id)
        self.assertEqual(second_evidence.prev_evidence_unit_id, first_evidence.evidence_unit_id)
        self.assertEqual(second_evidence.previous_unit_id, first_evidence.evidence_unit_id)
        self.assertEqual(first_evidence.next_unit_id, second_evidence.evidence_unit_id)
        self.assertEqual(artifact.target_refs[0].evidence_unit_ids, [first_evidence.evidence_unit_id])
        self.assertEqual(artifact.target_refs[1].locator.line_start, 3)

    def test_code_context_can_expand_from_function_body_to_enclosing_symbol(self) -> None:
        graph = EvidenceGraph()
        source = SourceDocument(
            source_document_id="doc:code-1",
            source_snapshot_id="snap:code-1:v1",
            source_uri="/knowledge/example.py",
            source_kind="code",
            mime_type="text/x-python",
            content_hash="sha256:code-1",
            size_bytes=211,
        )
        graph.add_source_document(source)

        extracted = ExtractedText(
            extracted_text_id="ext:code-1:v1",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            extractor_name="python-parser",
            extractor_version="1.0.0",
            extraction_mode="parser",
            text='"""Greets the caller."""\ndef greet(name):\n    return f"hello {name}"\n',
            text_hash="sha256:ext-code-1",
            span_map=[
                SpanMapEntry(
                    text_span=TextSpan(0, 25),
                    locator=SourceLocator(source_uri=source.source_uri, line_start=1, line_end=1),
                ),
                SpanMapEntry(
                    text_span=TextSpan(26, 70),
                    locator=SourceLocator(source_uri=source.source_uri, line_start=2, line_end=3),
                ),
            ],
            fidelity_state=FidelityState.EXACT,
            content_facets=["code", "prose"],
        )
        graph.add_extracted_text(extracted)

        function_region = StructuralRegion(
            structural_region_id="region:code-1:function",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            extracted_text_id=extracted.extracted_text_id,
            region_type="function_definition",
            primary_span=AnchoredSpan(
                locator=SourceLocator(source_uri=source.source_uri, line_start=1, line_end=3),
                text_span=TextSpan(0, 70),
            ),
            ordinal=0,
            content_facets=["code", "prose"],
            boundary_basis="python function",
            label="greet",
            heading_path=["greet"],
            children_count=2,
        )
        graph.add_structural_region(function_region)

        doc_region = StructuralRegion(
            structural_region_id="region:code-1:docstring",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            extracted_text_id=extracted.extracted_text_id,
            region_type="docstring",
            primary_span=AnchoredSpan(
                locator=SourceLocator(source_uri=source.source_uri, line_start=1, line_end=1),
                text_span=TextSpan(0, 25),
            ),
            ordinal=1,
            content_facets=["prose"],
            boundary_basis="python docstring",
            parent_region_id=function_region.structural_region_id,
        )
        body_region = StructuralRegion(
            structural_region_id="region:code-1:body",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            extracted_text_id=extracted.extracted_text_id,
            region_type="function_body",
            primary_span=AnchoredSpan(
                locator=SourceLocator(source_uri=source.source_uri, line_start=2, line_end=3),
                text_span=TextSpan(26, 70),
            ),
            ordinal=2,
            content_facets=["code"],
            boundary_basis="python function body",
            parent_region_id=function_region.structural_region_id,
            prev_region_id=doc_region.structural_region_id,
            label="greet",
        )
        graph.add_structural_region(doc_region)
        graph.add_structural_region(body_region)

        evidence = EvidenceUnit(
            evidence_unit_id="evidence:code-1:body",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            unit_type="function_body",
            canonical_text='def greet(name):\n    return f"hello {name}"',
            support_links=[
                ProvenanceLink(
                    source_document_id=source.source_document_id,
                    source_snapshot_id=source.source_snapshot_id,
                    locator=SourceLocator(source_uri=source.source_uri, line_start=2, line_end=3),
                    role="primary_support",
                    recoverable=True,
                    upstream_entity_refs=[RecordRef("StructuralRegion", body_region.structural_region_id)],
                ),
                ProvenanceLink(
                    source_document_id=source.source_document_id,
                    source_snapshot_id=source.source_snapshot_id,
                    locator=SourceLocator(source_uri=source.source_uri, line_start=1, line_end=1),
                    role="attached_context",
                    recoverable=True,
                    upstream_entity_refs=[RecordRef("StructuralRegion", doc_region.structural_region_id)],
                ),
            ],
            structural_region_ids=[
                function_region.structural_region_id,
                body_region.structural_region_id,
            ],
            ordinal=2,
            boundary_rationale="function body stands alone but docstring remains attached context",
            content_facets=["code"],
            trust_state=TrustState.VERIFIED,
            context_labels=["greet", "docstring"],
            expansion_policy_hint="expand_to_parent_region",
        )
        graph.add_evidence_unit(evidence)

        recovery = graph.recover_evidence(evidence.evidence_unit_id)
        self.assertEqual(recovery.parent_regions[0].label, "greet")
        self.assertEqual(recovery.parent_regions[1].structural_region_id, body_region.structural_region_id)
        self.assertEqual(evidence.expansion_policy_hint, "expand_to_parent_region")
        self.assertIn("docstring", evidence.context_labels)

    def test_recovery_view_exposes_source_snapshot_locator_parent_and_neighbors(self) -> None:
        graph = EvidenceGraph()
        source = SourceDocument(
            source_document_id="doc:recovery-1",
            source_snapshot_id="snap:recovery-1:v1",
            source_uri="/knowledge/recovery.txt",
            source_kind="text",
            mime_type="text/plain",
            content_hash="sha256:recovery-1",
            size_bytes=50,
        )
        graph.add_source_document(source)
        extracted = ExtractedText(
            extracted_text_id="ext:recovery-1:v1",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            extractor_name="line-reader",
            extractor_version="1.0.0",
            extraction_mode="native_text",
            text="alpha\nbeta\ngamma\n",
            text_hash="sha256:ext-recovery-1",
            span_map=[
                SpanMapEntry(
                    text_span=TextSpan(6, 10),
                    locator=SourceLocator(source_uri=source.source_uri, line_start=2, line_end=2),
                )
            ],
            fidelity_state=FidelityState.EXACT,
        )
        graph.add_extracted_text(extracted)

        region = StructuralRegion(
            structural_region_id="region:recovery-1:beta",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            extracted_text_id=extracted.extracted_text_id,
            region_type="line",
            primary_span=AnchoredSpan(
                locator=SourceLocator(source_uri=source.source_uri, line_start=2, line_end=2),
                text_span=TextSpan(6, 10),
            ),
            ordinal=1,
            content_facets=["prose"],
            boundary_basis="line boundary",
        )
        graph.add_structural_region(region)

        prev_evidence = EvidenceUnit(
            evidence_unit_id="evidence:recovery-1:alpha",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            unit_type="line",
            canonical_text="alpha",
            support_links=[
                ProvenanceLink(
                    source_document_id=source.source_document_id,
                    source_snapshot_id=source.source_snapshot_id,
                    locator=SourceLocator(source_uri=source.source_uri, line_start=1, line_end=1),
                    role="primary_support",
                    recoverable=True,
                )
            ],
            structural_region_ids=[region.structural_region_id],
            ordinal=0,
            boundary_rationale="single line",
            content_facets=["prose"],
            trust_state=TrustState.SEGMENTED,
            next_evidence_unit_id="evidence:recovery-1:beta",
        )
        graph.add_evidence_unit(prev_evidence)

        evidence = EvidenceUnit(
            evidence_unit_id="evidence:recovery-1:beta",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            unit_type="line",
            canonical_text="beta",
            support_links=[
                ProvenanceLink(
                    source_document_id=source.source_document_id,
                    source_snapshot_id=source.source_snapshot_id,
                    locator=SourceLocator(source_uri=source.source_uri, line_start=2, line_end=2),
                    role="primary_support",
                    recoverable=True,
                    text_span=TextSpan(6, 10),
                )
            ],
            structural_region_ids=[region.structural_region_id],
            ordinal=1,
            boundary_rationale="single line",
            content_facets=["prose"],
            trust_state=TrustState.SEGMENTED,
            prev_evidence_unit_id=prev_evidence.evidence_unit_id,
            next_evidence_unit_id="evidence:recovery-1:gamma",
        )
        graph.add_evidence_unit(evidence)

        recovery = graph.recover_evidence(evidence.evidence_unit_id)
        self.assertEqual(recovery.source_uri, source.source_uri)
        self.assertEqual(recovery.source_snapshot_id, source.source_snapshot_id)
        self.assertEqual(recovery.locators[0].line_start, 2)
        self.assertEqual(recovery.parent_regions[0].structural_region_id, region.structural_region_id)
        self.assertEqual(recovery.previous_evidence_unit_id, prev_evidence.evidence_unit_id)
        self.assertEqual(recovery.next_evidence_unit_id, "evidence:recovery-1:gamma")
        self.assertEqual(recovery.previous_unit_id, prev_evidence.evidence_unit_id)
        self.assertEqual(recovery.next_unit_id, "evidence:recovery-1:gamma")

    def test_evidence_graph_can_walk_neighbors_and_expand_context(self) -> None:
        graph = EvidenceGraph()
        source = SourceDocument(
            source_document_id="doc:neighbors-1",
            source_snapshot_id="snap:neighbors-1:v1",
            source_uri="/knowledge/neighbors.txt",
            source_kind="text",
            mime_type="text/plain",
            content_hash="sha256:neighbors-1",
            size_bytes=32,
        )
        graph.add_source_document(source)
        extracted = ExtractedText(
            extracted_text_id="ext:neighbors-1:v1",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            extractor_name="line-reader",
            extractor_version="1.0.0",
            extraction_mode="native_text",
            text="alpha\nbeta\ngamma\n",
            text_hash="sha256:ext-neighbors-1",
            span_map=[
                SpanMapEntry(
                    text_span=TextSpan(0, 5),
                    locator=SourceLocator(source_uri=source.source_uri, line_start=1, line_end=1),
                )
            ],
            fidelity_state=FidelityState.EXACT,
        )
        graph.add_extracted_text(extracted)

        region = StructuralRegion(
            structural_region_id="region:neighbors-1:all",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            extracted_text_id=extracted.extracted_text_id,
            region_type="line",
            primary_span=AnchoredSpan(
                locator=SourceLocator(source_uri=source.source_uri, line_start=1, line_end=3),
                text_span=TextSpan(0, 16),
            ),
            ordinal=0,
            content_facets=["prose"],
            boundary_basis="line grouping",
        )
        graph.add_structural_region(region)

        alpha = EvidenceUnit(
            evidence_unit_id="evidence:neighbors-1:alpha",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            unit_type="line",
            canonical_text="alpha",
            support_links=[
                ProvenanceLink(
                    source_document_id=source.source_document_id,
                    source_snapshot_id=source.source_snapshot_id,
                    locator=SourceLocator(source_uri=source.source_uri, line_start=1, line_end=1),
                    role="primary_support",
                    recoverable=True,
                    text_span=TextSpan(0, 5),
                )
            ],
            structural_region_ids=[region.structural_region_id],
            ordinal=0,
            boundary_rationale="single line",
            content_facets=["prose"],
            trust_state=TrustState.SEGMENTED,
            next_evidence_unit_id="evidence:neighbors-1:beta",
        )
        graph.add_evidence_unit(alpha)

        beta = EvidenceUnit(
            evidence_unit_id="evidence:neighbors-1:beta",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            unit_type="line",
            canonical_text="beta",
            support_links=[
                ProvenanceLink(
                    source_document_id=source.source_document_id,
                    source_snapshot_id=source.source_snapshot_id,
                    locator=SourceLocator(source_uri=source.source_uri, line_start=2, line_end=2),
                    role="primary_support",
                    recoverable=True,
                    text_span=TextSpan(6, 10),
                )
            ],
            structural_region_ids=[region.structural_region_id],
            ordinal=1,
            boundary_rationale="single line",
            content_facets=["prose"],
            trust_state=TrustState.SEGMENTED,
            prev_evidence_unit_id=alpha.evidence_unit_id,
            next_evidence_unit_id="evidence:neighbors-1:gamma",
        )
        graph.add_evidence_unit(beta)

        gamma = EvidenceUnit(
            evidence_unit_id="evidence:neighbors-1:gamma",
            source_document_id=source.source_document_id,
            source_snapshot_id=source.source_snapshot_id,
            unit_type="line",
            canonical_text="gamma",
            support_links=[
                ProvenanceLink(
                    source_document_id=source.source_document_id,
                    source_snapshot_id=source.source_snapshot_id,
                    locator=SourceLocator(source_uri=source.source_uri, line_start=3, line_end=3),
                    role="primary_support",
                    recoverable=True,
                    text_span=TextSpan(11, 16),
                )
            ],
            structural_region_ids=[region.structural_region_id],
            ordinal=2,
            boundary_rationale="single line",
            content_facets=["prose"],
            trust_state=TrustState.SEGMENTED,
            prev_evidence_unit_id=beta.evidence_unit_id,
        )
        graph.add_evidence_unit(gamma)

        neighbors = graph.get_neighbors(beta, 1)
        expanded = graph.expand_context(beta, 1)

        self.assertEqual(
            [unit.evidence_unit_id for unit in neighbors],
            [alpha.evidence_unit_id, gamma.evidence_unit_id],
        )
        self.assertEqual(
            [unit.evidence_unit_id for unit in expanded],
            [alpha.evidence_unit_id, beta.evidence_unit_id, gamma.evidence_unit_id],
        )
        self.assertEqual(beta.text_span, TextSpan(6, 10))

    def test_evidence_unit_requires_recoverable_support_locator(self) -> None:
        with self.assertRaises(DataModelValidationError):
            EvidenceUnit(
                evidence_unit_id="evidence:bad",
                source_document_id="doc:bad",
                source_snapshot_id="snap:bad:v1",
                unit_type="paragraph",
                canonical_text="unsupported",
                support_links=[
                    ProvenanceLink(
                        source_document_id="doc:bad",
                        source_snapshot_id="snap:bad:v1",
                        locator=SourceLocator(source_uri="/bad/path"),
                        role="primary_support",
                        recoverable=False,
                    )
                ],
                structural_region_ids=["region:bad"],
                ordinal=0,
                boundary_rationale="invalid because it cannot recover to source",
                content_facets=["prose"],
                trust_state=TrustState.AMBIGUOUS,
            )

    def test_schema_profiles_export_minimal_and_extended_contracts(self) -> None:
        minimal = MINIMAL_SCHEMA.entity_map()
        self.assertIn("SourceDocument", minimal)
        self.assertIn("EvidenceUnit", minimal)
        evidence_fields = [field.name for field in minimal["EvidenceUnit"].required_fields]
        self.assertIn("support_links", evidence_fields)
        self.assertIn("trust_state", evidence_fields)


if __name__ == "__main__":
    unittest.main()
