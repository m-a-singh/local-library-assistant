from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from canonical_data_model import FidelityState  # noqa: E402
from mvp_pipeline import (  # noqa: E402
    build_evidence_units,
    build_segmentation_input,
    build_structural_regions,
    ingest_local_file,
    normalize_extracted_text,
    run_pipeline,
    run_structural_segmentation,
    select_segmentation_profile,
)
from mvp_pipeline.ingest import ingest_folder, is_supported_source_file  # noqa: E402


class MvpPipelineTest(unittest.TestCase):
    def test_code_source_uses_plain_text_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "main.py"
            path.write_text("print('hello')\n", encoding="utf-8")

            source_document, _ = ingest_local_file(path)
            profile = select_segmentation_profile(source_document)

            self.assertEqual(profile.profile_name, "plain_text")
            self.assertEqual(source_document.source_kind, "code")

    def test_docx_ingestion_extracts_paragraph_text_without_python_docx(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "note.docx"
            with ZipFile(path, "w") as archive:
                archive.writestr(
                    "word/document.xml",
                    (
                        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                        "<w:body>"
                        "<w:p><w:r><w:t>First paragraph.</w:t></w:r></w:p>"
                        "<w:p><w:r><w:t>Second paragraph.</w:t></w:r></w:p>"
                        "</w:body>"
                        "</w:document>"
                    ),
                )

            source_document, extracted_text = ingest_local_file(path)
            result = run_pipeline(path)

            self.assertEqual(source_document.source_kind, "docx")
            self.assertEqual(extracted_text.extraction_mode, "docx_text")
            self.assertEqual(extracted_text.text, "First paragraph.\n\nSecond paragraph.")
            self.assertEqual(len(result.evidence_units), 2)
            self.assertEqual(result.evidence_units[0].canonical_text, "First paragraph.\n")
            self.assertEqual(result.evidence_units[1].canonical_text, "Second paragraph.")

    def test_ingest_folder_supports_mixed_file_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "note.md").write_text("# Title\nBody\n", encoding="utf-8")
            (root / "query.sql").write_text("SELECT 1;\n", encoding="utf-8")
            (root / "app.py").write_text("print('ok')\n", encoding="utf-8")
            (root / ".env").write_text("DEBUG=true\n", encoding="utf-8")
            (root / "ignore.bin").write_bytes(b"\x00\x01")

            documents, extracted_texts = ingest_folder(root)

            self.assertEqual(len(documents), 4)
            self.assertEqual(len(extracted_texts), 4)
            self.assertTrue(is_supported_source_file(root / "query.sql"))
            self.assertTrue(is_supported_source_file(root / "app.py"))
            self.assertTrue(is_supported_source_file(root / ".env"))
            self.assertFalse(is_supported_source_file(root / "ignore.bin"))

    def test_plain_text_source_uses_plain_text_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "note.txt"
            path.write_text("alpha\nbeta\n", encoding="utf-8")

            source_document, _ = ingest_local_file(path)
            profile = select_segmentation_profile(source_document)

            self.assertEqual(profile.profile_name, "plain_text")

    def test_markdown_source_uses_markdown_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "note.md"
            path.write_text("# Title\nBody\n", encoding="utf-8")

            source_document, _ = ingest_local_file(path)
            profile = select_segmentation_profile(source_document)

            self.assertEqual(profile.profile_name, "markdown")

    def test_plain_text_paragraphs_preserve_adjacency_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "note.txt"
            path.write_text("alpha\nbeta\n\ngamma\n", encoding="utf-8")

            result = run_pipeline(path)
            paragraph_regions = [
                region for region in result.structural_regions if region.region_type == "paragraph_block"
            ]

            self.assertEqual(
                [region.region_type for region in result.structural_regions],
                ["document_root", "paragraph_block", "paragraph_block"],
            )
            self.assertEqual(len(paragraph_regions), 2)
            self.assertIsNone(paragraph_regions[0].prev_region_id)
            self.assertEqual(paragraph_regions[0].next_region_id, paragraph_regions[1].structural_region_id)
            self.assertEqual(paragraph_regions[1].prev_region_id, paragraph_regions[0].structural_region_id)
            self.assertEqual(len(result.evidence_units), 2)
            self.assertEqual(result.evidence_units[0].unit_type, "prose")
            self.assertEqual(result.evidence_units[0].canonical_text, "alpha\nbeta\n")
            self.assertEqual((result.evidence_units[0].line_start, result.evidence_units[0].line_end), (1, 2))
            self.assertEqual((result.evidence_units[0].char_start, result.evidence_units[0].char_end), (0, 11))
            self.assertEqual((result.evidence_units[0].text_span.start, result.evidence_units[0].text_span.end), (0, 11))
            self.assertIsNone(result.evidence_units[0].previous_unit_id)
            self.assertEqual(result.evidence_units[0].next_unit_id, result.evidence_units[1].evidence_unit_id)
            self.assertEqual(result.evidence_units[1].previous_unit_id, result.evidence_units[0].evidence_unit_id)
            self.assertIsNone(result.evidence_units[1].next_unit_id)
            self.assertEqual(
                result.evidence_units[0].structural_region_ids[-1],
                result.structural_regions[0].structural_region_id,
            )
            self.assertEqual(result.evidence_units[0].confidence.value, "medium")
            self.assertEqual(result.evidence_units[0].flags, [])
            self.assertEqual(result.evidence_units[0].signals, ["has_paragraph_body", "single_region_unit", "unit_type:prose"])

    def test_pipeline_result_can_expand_evidence_context_via_adjacency(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "context.txt"
            path.write_text("alpha\n\nbeta\n\ngamma\n", encoding="utf-8")

            result = run_pipeline(path)
            middle_unit = result.evidence_units[1]

            neighbors = result.get_neighbors(middle_unit, 1)
            expanded = result.expand_context(middle_unit, 1)

            self.assertEqual(
                [unit.canonical_text for unit in neighbors],
                ["alpha\n", "gamma\n"],
            )
            self.assertEqual(
                [unit.canonical_text for unit in expanded],
                ["alpha\n", "beta\n", "gamma\n"],
            )

    def test_markdown_heading_hierarchy_populates_context_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "note.md"
            path.write_text("# Top\nIntro\n\n## Child\nMore\n", encoding="utf-8")

            result = run_pipeline(path)
            section_regions = [
                region for region in result.structural_regions if region.region_type == "section_anchor"
            ]
            self.assertEqual(len(section_regions), 2)
            self.assertEqual(section_regions[0].label, "Top")
            self.assertEqual(section_regions[1].label, "Child")
            self.assertEqual(section_regions[1].parent_region_id, section_regions[0].structural_region_id)
            self.assertEqual(len(result.evidence_units), 2)
            self.assertEqual([unit.unit_type for unit in result.evidence_units], ["heading_section", "heading_section"])
            self.assertEqual(result.evidence_units[0].canonical_text, "# Top\nIntro\n")
            self.assertEqual(result.evidence_units[1].canonical_text, "## Child\nMore\n")
            self.assertEqual(result.evidence_units[0].context_labels, ["Top"])
            self.assertEqual(result.evidence_units[1].context_labels, ["Top", "Child"])
            self.assertEqual(result.evidence_units[0].parent_region_id, result.structural_regions[0].structural_region_id)
            self.assertEqual(result.evidence_units[1].parent_region_id, section_regions[0].structural_region_id)
            self.assertIn("has_heading", result.evidence_units[0].signals)
            self.assertIn("multi_region_unit", result.evidence_units[0].signals)

    def test_markdown_fenced_code_block_becomes_one_leaf_region_and_evidence_unit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "code.md"
            path.write_text("# Title\n```python\nprint('x')\n```\nAfter\n", encoding="utf-8")

            result = run_pipeline(path)
            code_regions = [
                region for region in result.structural_regions if region.region_type == "fenced_code_block"
            ]
            code_evidence = [
                unit for unit in result.evidence_units if unit.unit_type == "code"
            ]

            self.assertEqual(len(code_regions), 1)
            self.assertEqual(len(code_evidence), 1)
            self.assertEqual(len(result.evidence_units), 2)
            self.assertTrue(code_evidence[0].canonical_text.startswith("```python"))
            self.assertNotIn("After", code_evidence[0].canonical_text)
            self.assertEqual(code_evidence[0].content_facets, ["code"])
            self.assertIn("has_fenced_code_block", code_evidence[0].signals)
            prose_evidence = [unit for unit in result.evidence_units if unit.unit_type == "prose"]
            self.assertEqual(len(prose_evidence), 1)
            self.assertEqual(prose_evidence[0].canonical_text, "After\n")

    def test_markdown_table_becomes_table_not_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "table.md"
            path.write_text(
                "| Name | Value |\n"
                "| --- | --- |\n"
                "| alpha | beta |\n",
                encoding="utf-8",
            )

            result = run_pipeline(path)

            self.assertEqual(len(result.evidence_units), 1)
            self.assertEqual(result.evidence_units[0].unit_type, "table")
            self.assertEqual(result.evidence_units[0].confidence.value, "high")
            self.assertEqual(result.evidence_units[0].flags, [])
            self.assertIn("table_candidate", result.evidence_units[0].signals)
            self.assertNotIn("command_candidate", result.evidence_units[0].signals)

    def test_fenced_mermaid_block_becomes_diagram_not_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "diagram.md"
            path.write_text(
                "```mermaid\n"
                "graph TD\n"
                "A-->B\n"
                "```\n",
                encoding="utf-8",
            )

            result = run_pipeline(path)

            self.assertEqual(len(result.evidence_units), 1)
            self.assertEqual(result.evidence_units[0].unit_type, "diagram")
            self.assertEqual(result.evidence_units[0].confidence.value, "high")
            self.assertEqual(result.evidence_units[0].flags, [])
            self.assertIn("mermaid_block", result.evidence_units[0].signals)
            self.assertNotIn("command_candidate", result.evidence_units[0].signals)

    def test_json_search_block_becomes_json_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "search.md"
            path.write_text(
                "GET my-index/_search\n"
                "{\n"
                "  \"query\": {\"match_all\": {}}\n"
                "}\n",
                encoding="utf-8",
            )

            result = run_pipeline(path)

            self.assertEqual(len(result.evidence_units), 1)
            self.assertEqual(result.evidence_units[0].unit_type, "json_query")
            self.assertEqual(result.evidence_units[0].confidence.value, "high")
            self.assertEqual(result.evidence_units[0].flags, [])
            self.assertIn("json_query_candidate", result.evidence_units[0].signals)
            self.assertNotIn("command_candidate", result.evidence_units[0].signals)

    def test_json_aliases_block_becomes_json_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "aliases.md"
            path.write_text(
                "POST /_aliases\n"
                "{\n"
                "  \"actions\": []\n"
                "}\n",
                encoding="utf-8",
            )

            result = run_pipeline(path)

            self.assertEqual(len(result.evidence_units), 1)
            self.assertEqual(result.evidence_units[0].unit_type, "json_query")
            self.assertEqual(result.evidence_units[0].confidence.value, "high")
            self.assertEqual(result.evidence_units[0].flags, [])
            self.assertIn("json_query_candidate", result.evidence_units[0].signals)
            self.assertNotIn("command_candidate", result.evidence_units[0].signals)

    def test_shell_like_paragraph_becomes_command_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "commands.txt"
            path.write_text("$ ls -la\npwd\n\nNarrative text\n", encoding="utf-8")

            result = run_pipeline(path)

            self.assertEqual([unit.unit_type for unit in result.evidence_units], ["command", "prose"])
            self.assertIn("command_candidate", result.evidence_units[0].signals)
            self.assertEqual(result.evidence_units[0].confidence.value, "high")
            self.assertEqual(result.evidence_units[0].flags, [])
            self.assertEqual(result.evidence_units[0].canonical_text, "$ ls -la\npwd\n")
            self.assertNotIn("command_candidate", result.evidence_units[1].signals)

    def test_standalone_command_without_prompt_becomes_command_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "commands.txt"
            path.write_text("git status\n", encoding="utf-8")

            result = run_pipeline(path)

            self.assertEqual(len(result.evidence_units), 1)
            self.assertEqual(result.evidence_units[0].unit_type, "command")
            self.assertEqual(result.evidence_units[0].confidence.value, "high")
            self.assertEqual(result.evidence_units[0].flags, [])
            self.assertIn("command_candidate", result.evidence_units[0].signals)

    def test_standalone_aws_command_becomes_command_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "aws.txt"
            path.write_text(
                "aws dynamodb list-tables --endpoint-url http://localhost:8001\n",
                encoding="utf-8",
            )

            result = run_pipeline(path)

            self.assertEqual(len(result.evidence_units), 1)
            self.assertEqual(result.evidence_units[0].unit_type, "command")
            self.assertEqual(result.evidence_units[0].confidence.value, "high")
            self.assertEqual(result.evidence_units[0].flags, [])
            self.assertIn("command_candidate", result.evidence_units[0].signals)

    def test_standalone_keytool_command_becomes_command_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "keytool.txt"
            path.write_text(
                "keytool -list -keystore my.jks -storepass changeit\n",
                encoding="utf-8",
            )

            result = run_pipeline(path)

            self.assertEqual(len(result.evidence_units), 1)
            self.assertEqual(result.evidence_units[0].unit_type, "command")
            self.assertEqual(result.evidence_units[0].confidence.value, "high")
            self.assertEqual(result.evidence_units[0].flags, [])
            self.assertIn("command_candidate", result.evidence_units[0].signals)

    def test_inline_path_style_keytool_command_in_prose_becomes_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "inline-keytool.txt"
            path.write_text(
                "./keytool -list -keystore my.jks -storepass changeit\n",
                encoding="utf-8",
            )

            result = run_pipeline(path)

            self.assertEqual(len(result.evidence_units), 1)
            self.assertEqual(result.evidence_units[0].unit_type, "command")
            self.assertEqual(result.evidence_units[0].confidence.value, "high")
            self.assertEqual(result.evidence_units[0].flags, [])
            self.assertIn("command_candidate", result.evidence_units[0].signals)

    def test_narrative_prose_with_inline_command_remains_prose(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "narrative.txt"
            path.write_text("Use `git status` to inspect the repo.\n", encoding="utf-8")

            result = run_pipeline(path)

            self.assertEqual(len(result.evidence_units), 1)
            self.assertEqual(result.evidence_units[0].unit_type, "prose")
            self.assertEqual(result.evidence_units[0].confidence.value, "medium")
            self.assertEqual(result.evidence_units[0].flags, [])
            self.assertNotIn("command_candidate", result.evidence_units[0].signals)

    def test_short_explanatory_paragraph_attaches_above_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "command-context.txt"
            path.write_text("Run this command:\n\n$ ls -la\npwd\n", encoding="utf-8")

            result = run_pipeline(path)
            command_unit = next(unit for unit in result.evidence_units if unit.unit_type == "command")

            self.assertIn("attached_context_above", command_unit.signals)
            self.assertEqual(command_unit.confidence.value, "medium")
            self.assertEqual(command_unit.flags, ["mixed_content", "weak_boundary"])
            self.assertEqual(command_unit.line_start, 1)
            self.assertEqual(command_unit.canonical_text, "Run this command:\n\n$ ls -la\npwd\n")
            self.assertEqual([link.role for link in command_unit.support_links], ["primary_support", "attached_context"])
            self.assertEqual(command_unit.support_links[1].locator.line_start, 1)
            self.assertEqual(command_unit.support_links[1].locator.line_end, 1)

    def test_non_explanatory_paragraph_does_not_attach_above_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "command-no-context.txt"
            path.write_text("Narrative text\n\n$ ls -la\npwd\n", encoding="utf-8")

            result = run_pipeline(path)
            command_unit = next(unit for unit in result.evidence_units if unit.unit_type == "command")

            self.assertNotIn("attached_context_above", command_unit.signals)
            self.assertEqual(command_unit.confidence.value, "high")
            self.assertEqual(command_unit.flags, [])
            self.assertEqual(command_unit.line_start, 3)
            self.assertEqual(command_unit.canonical_text, "$ ls -la\npwd\n")
            self.assertEqual([link.role for link in command_unit.support_links], ["primary_support"])

    def test_sql_starting_paragraph_updates_heading_section_to_sql(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "query.md"
            path.write_text("## Query\nSELECT * FROM users;\nWHERE id = 1;\n", encoding="utf-8")

            result = run_pipeline(path)

            self.assertEqual(len(result.evidence_units), 1)
            self.assertEqual(result.evidence_units[0].unit_type, "sql")
            self.assertIn("has_heading", result.evidence_units[0].signals)
            self.assertIn("sql_candidate", result.evidence_units[0].signals)
            self.assertEqual(result.evidence_units[0].confidence.value, "medium")
            self.assertEqual(result.evidence_units[0].flags, ["mixed_content"])
            self.assertEqual(
                result.evidence_units[0].canonical_text,
                "## Query\nSELECT * FROM users;\nWHERE id = 1;\n",
            )

    def test_short_explanatory_paragraph_attaches_above_sql(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sql-context.txt"
            path.write_text("Use this query:\n\nSELECT * FROM users;\nWHERE id = 1;\n", encoding="utf-8")

            result = run_pipeline(path)
            sql_unit = next(unit for unit in result.evidence_units if unit.unit_type == "sql")

            self.assertIn("sql_candidate", sql_unit.signals)
            self.assertIn("attached_context_above", sql_unit.signals)
            self.assertEqual(sql_unit.confidence.value, "medium")
            self.assertEqual(sql_unit.flags, ["mixed_content", "weak_boundary"])
            self.assertEqual(
                sql_unit.canonical_text,
                "Use this query:\n\nSELECT * FROM users;\nWHERE id = 1;\n",
            )
            self.assertEqual(sql_unit.support_links[1].role, "attached_context")

    def test_standalone_sql_paragraph_is_high_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "query.txt"
            path.write_text("SELECT * FROM users;\nWHERE id = 1;\n", encoding="utf-8")

            result = run_pipeline(path)

            self.assertEqual(len(result.evidence_units), 1)
            self.assertEqual(result.evidence_units[0].unit_type, "sql")
            self.assertEqual(result.evidence_units[0].confidence.value, "high")
            self.assertEqual(result.evidence_units[0].flags, [])
            self.assertIn("sql_candidate", result.evidence_units[0].signals)

    def test_opensearch_style_delete_is_command_not_sql(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "delete.txt"
            path.write_text("DELETE _scripts/EpisodeVodName\n", encoding="utf-8")

            result = run_pipeline(path)

            self.assertEqual(len(result.evidence_units), 1)
            self.assertEqual(result.evidence_units[0].unit_type, "command")
            self.assertEqual(result.evidence_units[0].confidence.value, "high")
            self.assertIn("command_candidate", result.evidence_units[0].signals)
            self.assertNotIn("sql_candidate", result.evidence_units[0].signals)

    def test_mixed_blob_with_separator_is_low_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "mixed.txt"
            path.write_text("git status\n---\nNarrative explanation without separation\n", encoding="utf-8")

            result = run_pipeline(path)

            self.assertEqual(len(result.evidence_units), 1)
            self.assertEqual(result.evidence_units[0].unit_type, "command")
            self.assertEqual(result.evidence_units[0].confidence.value, "low")
            self.assertIn("mixed_content", result.evidence_units[0].flags)
            self.assertIn("weak_boundary", result.evidence_units[0].flags)
            self.assertIn("suspicious_grouping", result.evidence_units[0].flags)

    def test_multiple_cli_commands_in_sequence_split_into_separate_units(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "multi-cli.txt"
            path.write_text(
                "aws s3 ls\n"
                "aws dynamodb list-tables --endpoint-url http://localhost:8001\n"
                "export AWS_PROFILE=dev\n"
                "python app.py\n",
                encoding="utf-8",
            )

            result = run_pipeline(path)

            self.assertEqual(len(result.evidence_units), 4)
            self.assertEqual([unit.unit_type for unit in result.evidence_units], ["command", "command", "command", "command"])
            self.assertEqual(
                [unit.canonical_text for unit in result.evidence_units],
                [
                    "aws s3 ls\n",
                    "aws dynamodb list-tables --endpoint-url http://localhost:8001\n",
                    "export AWS_PROFILE=dev\n",
                    "python app.py\n",
                ],
            )
            self.assertEqual(
                [(unit.line_start, unit.line_end) for unit in result.evidence_units],
                [(1, 1), (2, 2), (3, 3), (4, 4)],
            )

    def test_command_with_embedded_json_payload_is_not_high_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "payload.txt"
            path.write_text(
                "PAYLOAD='{\"name\":\"demo\",\"items\":["
                + "\"x\","
                * 120
                + "\"z\"]}' aws lambda invoke --payload \"$PAYLOAD\" out.json\n",
                encoding="utf-8",
            )

            result = run_pipeline(path)

            self.assertEqual(len(result.evidence_units), 1)
            self.assertEqual(result.evidence_units[0].unit_type, "command")
            self.assertEqual(result.evidence_units[0].confidence.value, "low")
            self.assertIn("embedded_payload", result.evidence_units[0].flags)
            self.assertIn("oversized_inline_payload", result.evidence_units[0].flags)

    def test_normalization_records_bom_and_newline_cleanup_without_changing_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bom.txt"
            path.write_bytes(b"\xef\xbb\xbfalpha\r\n\r\nbeta\r\n")

            source_document, raw_extracted = ingest_local_file(path)
            normalized = normalize_extracted_text(raw_extracted)
            segmentation_input = build_segmentation_input(normalized)
            regions = build_structural_regions(source_document, segmentation_input)
            evidence_units = build_evidence_units(source_document, normalized, regions)

            self.assertTrue(raw_extracted.text.startswith("\ufeff"))
            self.assertEqual(normalized.text, "alpha\n\nbeta\n")
            self.assertEqual(normalized.fidelity_state, FidelityState.NORMALIZED)
            self.assertEqual(normalized.source_snapshot_id, source_document.source_snapshot_id)
            self.assertEqual([note.note_type for note in normalized.normalization_notes], ["bom_removed", "newline_normalized"])
            self.assertNotIn("\r", segmentation_input.text)
            self.assertFalse(segmentation_input.text.startswith("\ufeff"))
            self.assertEqual([line.line_number for line in segmentation_input.lines], [1, 2, 3])
            self.assertEqual(
                [line.source_locator.line_start for line in segmentation_input.lines],
                [1, 2, 3],
            )
            self.assertEqual(segmentation_input.lines[0].text, "alpha\n")
            self.assertEqual(
                (evidence_units[0].char_start, evidence_units[0].char_end),
                (1, 8),
            )
            self.assertEqual(
                (evidence_units[1].char_start, evidence_units[1].char_end),
                (10, 16),
            )

    def test_ids_are_deterministic_for_unchanged_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "stable.md"
            path.write_text("# Stable\nParagraph\n", encoding="utf-8")

            first = run_pipeline(path)
            second = run_pipeline(path)

            self.assertEqual(first.source_document.source_snapshot_id, second.source_document.source_snapshot_id)
            self.assertEqual(
                [region.structural_region_id for region in first.structural_regions],
                [region.structural_region_id for region in second.structural_regions],
            )
            self.assertEqual(
                [unit.evidence_unit_id for unit in first.evidence_units],
                [unit.evidence_unit_id for unit in second.evidence_units],
            )

    def test_segmentation_input_is_the_explicit_contract_between_stages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "contract.md"
            path.write_bytes(b"\xef\xbb\xbf# Top\r\nPara\r\n\r\n```py\r\nprint(1)\r\n```\r\n")

            source_document, raw_extracted = ingest_local_file(path)
            normalized = normalize_extracted_text(raw_extracted)
            segmentation_input = build_segmentation_input(normalized)
            regions = build_structural_regions(source_document, segmentation_input)

            self.assertEqual(segmentation_input.source_document_id, source_document.source_document_id)
            self.assertEqual(segmentation_input.source_snapshot_id, source_document.source_snapshot_id)
            self.assertEqual(segmentation_input.extracted_text_id, normalized.extracted_text_id)
            self.assertEqual(segmentation_input.text, "# Top\nPara\n\n```py\nprint(1)\n```\n")
            self.assertEqual(segmentation_input.lines[0].source_locator.line_start, 1)
            self.assertEqual(segmentation_input.lines[1].source_locator.line_start, 2)
            self.assertEqual(segmentation_input.lines[0].text_span.start, 0)
            self.assertEqual(segmentation_input.lines[0].text, "# Top\n")
            self.assertEqual(
                [region.region_type for region in regions],
                ["document_root", "section_anchor", "paragraph_block", "fenced_code_block"],
            )

    def test_structural_region_exposes_direct_boundary_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "regions.md"
            path.write_text("# Title\n\nPara one\n\n```py\nprint(1)\n```\n", encoding="utf-8")

            regions = run_structural_segmentation(path)
            self.assertTrue(all(region.region_type for region in regions))
            self.assertEqual(
                [region.region_type for region in regions],
                ["document_root", "section_anchor", "paragraph_block", "fenced_code_block"],
            )
            paragraph_region = regions[2]
            code_region = regions[3]
            self.assertEqual((paragraph_region.line_start, paragraph_region.line_end), (3, 3))
            self.assertEqual((code_region.line_start, code_region.line_end), (5, 7))
            self.assertIsNotNone(paragraph_region.char_start)
            self.assertIsNotNone(code_region.char_end)
            self.assertEqual(paragraph_region.to_dict()["line_start"], 3)
            self.assertEqual(code_region.to_dict()["region_type"], "fenced_code_block")

    def test_every_evidence_unit_has_recoverable_primary_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "source.md"
            path.write_text("# Title\nFirst paragraph\n\nSecond paragraph\n", encoding="utf-8")

            result = run_pipeline(path)
            for evidence_unit in result.evidence_units:
                self.assertTrue(evidence_unit.support_links)
                support_link = evidence_unit.support_links[0]
                self.assertEqual(support_link.source_document_id, evidence_unit.source_document_id)
                self.assertEqual(support_link.source_snapshot_id, evidence_unit.source_snapshot_id)
                self.assertTrue(support_link.recoverable)
                self.assertIsNotNone(evidence_unit.line_start)
                self.assertIsNotNone(evidence_unit.source_uri)
                self.assertIsNotNone(evidence_unit.parent_region_id)
                self.assertIsNotNone(evidence_unit.text_span)
                self.assertTrue(evidence_unit.structural_region_ids)

    def test_unclosed_fence_emits_ambiguity_and_still_produces_code_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "broken.md"
            path.write_text("# Broken\n```python\nprint(1)\n", encoding="utf-8")

            result = run_pipeline(path)
            code_regions = [
                region for region in result.structural_regions if region.region_type == "fenced_code_block"
            ]
            self.assertEqual(len(code_regions), 1)
            self.assertEqual(len(code_regions[0].ambiguity), 1)
            self.assertEqual(code_regions[0].ambiguity[0].ambiguity_type, "unclosed_fenced_code_block")
            self.assertEqual(len(result.evidence_units), 1)
            self.assertEqual(result.evidence_units[0].unit_type, "code")
            self.assertEqual(result.evidence_units[0].ambiguity[0].ambiguity_type, "unclosed_fenced_code_block")
            self.assertEqual(result.evidence_units[0].confidence.value, "low")
            self.assertEqual(result.evidence_units[0].flags, ["weak_boundary"])


if __name__ == "__main__":
    unittest.main()
