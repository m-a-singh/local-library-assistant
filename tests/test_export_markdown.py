from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mvp_pipeline import export_folder_to_markdown  # noqa: E402


class MarkdownExportTest(unittest.TestCase):
    def test_exports_full_folder_recursively_and_preserves_run_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            input_dir = workspace / "corpus"
            nested_dir = input_dir / "nested"
            nested_dir.mkdir(parents=True)

            overview_path = input_dir / "overview.md"
            overview_path.write_text(
                "# Overview\n"
                "Review the project notes at [Docs](https://example.com/docs).\n",
                encoding="utf-8",
            )

            commands_path = nested_dir / "commands.txt"
            commands_path.write_text(
                "Run this command:\n\n"
                "$ ls -la\n"
                "pwd\n",
                encoding="utf-8",
            )

            query_path = nested_dir / "query.md"
            query_path.write_text(
                "Use this query:\n\n"
                "SELECT * FROM users;\n"
                "WHERE id = 1;\n",
                encoding="utf-8",
            )

            unsupported_path = input_dir / "ignore.bin"
            unsupported_path.write_bytes(b"\x00\x01")

            output_root = input_dir / "exports"
            stale_export = output_root / "stale.md"
            stale_export.parent.mkdir(parents=True)
            stale_export.write_text("# Old Export\nThis should not be re-ingested.\n", encoding="utf-8")

            export_result = export_folder_to_markdown(
                input_dir,
                output_root,
                dataset_name="audit_dataset",
            )

            self.assertEqual(export_result.input_dir, input_dir.resolve())
            self.assertEqual(export_result.dataset_dir, (output_root / "audit_dataset").resolve())
            self.assertEqual(export_result.files_processed, 3)
            self.assertEqual(export_result.files_skipped, 1)
            self.assertEqual(export_result.total_evidence_units, 5)
            self.assertEqual(
                export_result.unit_type_counts,
                {
                    "command": 1,
                    "heading_section": 1,
                    "prose": 2,
                    "sql": 1,
                },
            )
            self.assertEqual(sorted(export_result.files), ["commands", "links", "prose", "sql"])
            self.assertEqual(export_result.files["prose"].item_count, 3)
            self.assertEqual(export_result.files["commands"].item_count, 1)
            self.assertEqual(export_result.files["sql"].item_count, 1)
            self.assertEqual(export_result.files["links"].item_count, 1)

            processed_paths = {path for path in export_result.processed_files}
            self.assertEqual(processed_paths, {overview_path.resolve(), commands_path.resolve(), query_path.resolve()})

            self.assertEqual(export_result.skipped_files[0].path, unsupported_path.resolve())
            self.assertIn("unsupported file type", export_result.skipped_files[0].reason)

            prose_text = export_result.files["prose"].path.read_text(encoding="utf-8")
            command_text = export_result.files["commands"].path.read_text(encoding="utf-8")
            sql_text = export_result.files["sql"].path.read_text(encoding="utf-8")
            links_text = export_result.files["links"].path.read_text(encoding="utf-8")

            self.assertIn("# Prose Evidence Audit", prose_text)
            self.assertIn(f"## Source File: {commands_path.resolve()}", prose_text)
            self.assertIn(f"## Source File: {overview_path.resolve()}", prose_text)
            self.assertIn(f"## Source File: {query_path.resolve()}", prose_text)
            self.assertIn("- Evidence unit id: ", prose_text)
            self.assertIn("- Unit type: heading_section", prose_text)
            self.assertIn("- Line range: ", prose_text)
            self.assertIn("- Char range: ", prose_text)
            self.assertIn("- Parent region id: ", prose_text)
            self.assertIn("- Signals: ", prose_text)
            self.assertIn("- Confidence: ", prose_text)
            self.assertIn("Original text:", prose_text)
            self.assertIn("Review the project notes at [Docs](https://example.com/docs).", prose_text)
            self.assertIn(
                "````text\n# Overview\nReview the project notes at [Docs](https://example.com/docs).\n````",
                prose_text,
            )
            self.assertNotIn(str(stale_export.resolve()), prose_text)

            self.assertIn("# Command Evidence Audit", command_text)
            self.assertIn(f"## Source File: {commands_path.resolve()}", command_text)
            self.assertIn("- Unit type: command", command_text)
            self.assertIn("- Line range: ", command_text)
            self.assertIn("- Char range: ", command_text)
            self.assertIn("- Confidence: medium", command_text)
            self.assertIn("Run this command:", command_text)
            self.assertIn("$ ls -la", command_text)

            self.assertIn("# SQL Evidence Audit", sql_text)
            self.assertIn(f"## Source File: {query_path.resolve()}", sql_text)
            self.assertIn("- Unit type: sql", sql_text)
            self.assertIn("- Line range: ", sql_text)
            self.assertIn("- Char range: ", sql_text)
            self.assertIn("SELECT * FROM users;", sql_text)

            self.assertIn("# Link Evidence Audit", links_text)
            self.assertIn(f"## Source File: {overview_path.resolve()}", links_text)
            self.assertIn("- Link targets: https://example.com/docs", links_text)
            self.assertIn("Review the project notes at [Docs](https://example.com/docs).", links_text)


if __name__ == "__main__":
    unittest.main()
