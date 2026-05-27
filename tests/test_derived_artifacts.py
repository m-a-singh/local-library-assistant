from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from canonical_data_model import EvidenceGraph  # noqa: E402
from mvp_pipeline import build_link_index, run_pipeline  # noqa: E402


class DerivedArtifactsTest(unittest.TestCase):
    def test_builds_command_query_and_link_indexes_with_evidence_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.md"
            path.write_text(
                "# Audit\n"
                "Read docs at [Docs](https://example.com/docs).\n\n"
                "Run this command:\n\n"
                "$ ls -la\n"
                "pwd\n\n"
                "Use this query:\n\n"
                "SELECT * FROM users;\n"
                "WHERE id = 1;\n",
                encoding="utf-8",
            )

            result = run_pipeline(path)
            artifacts = result.build_derived_artifacts()
            artifacts_by_type = {artifact.artifact_type: artifact for artifact in artifacts}

            self.assertEqual(sorted(artifacts_by_type), ["command_index", "link_index", "query_index"])

            command_index = artifacts_by_type["command_index"]
            self.assertEqual([target.value for target in command_index.target_refs], ["ls -la", "pwd"])
            self.assertEqual(
                command_index.payload["entries"],
                [
                    {"target_ref_index": 0, "unit_line_index": 2},
                    {"target_ref_index": 1, "unit_line_index": 3},
                ],
            )
            self.assertEqual(command_index.derived_from[0].entity_type, "EvidenceUnit")
            self.assertEqual(command_index.target_refs[0].locator.source_uri, result.source_document.source_uri)
            self.assertEqual(command_index.target_refs[0].locator.line_start, 6)

            query_index = artifacts_by_type["query_index"]
            self.assertEqual([target.value for target in query_index.target_refs], ["SELECT * FROM users;"])
            self.assertEqual(
                query_index.payload["entries"],
                [{"target_ref_index": 0, "statement_kind": "select"}],
            )
            self.assertEqual(query_index.target_refs[0].locator.line_start, 11)

            link_index = artifacts_by_type["link_index"]
            self.assertEqual([target.value for target in link_index.target_refs], ["https://example.com/docs"])
            self.assertEqual(
                link_index.payload["entries"],
                [{"target_ref_index": 0, "link_kind": "markdown_link"}],
            )
            self.assertEqual(link_index.target_refs[0].locator.line_start, 2)

            graph = EvidenceGraph()
            graph.add_source_document(result.source_document)
            graph.add_extracted_text(result.normalized_extracted_text)
            for region in result.structural_regions:
                graph.add_structural_region(region)
            for evidence_unit in result.evidence_units:
                graph.add_evidence_unit(evidence_unit)
            for artifact in artifacts:
                graph.add_derived_artifact(artifact)

    def test_link_index_dedupes_markdown_link_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "links.md"
            path.write_text("See [Docs](https://example.com/docs)\n", encoding="utf-8")

            result = run_pipeline(path)
            link_index = build_link_index(result.source_document, result.evidence_units)

            self.assertIsNotNone(link_index)
            assert link_index is not None
            self.assertEqual(len(link_index.target_refs), 1)
            self.assertEqual(link_index.target_refs[0].value, "https://example.com/docs")


if __name__ == "__main__":
    unittest.main()
