"""Run the corpus-level markdown audit export for a source folder."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mvp_pipeline import export_folder_to_markdown  # noqa: E402


def _format_unit_type_counts(unit_type_counts: dict[str, int]) -> str:
    if not unit_type_counts:
        return "none"
    return ", ".join(f"{unit_type}={count}" for unit_type, count in unit_type_counts.items())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_folder", help="Folder to scan recursively for supported source files.")
    parser.add_argument("output_folder", help="Folder where the markdown audit export dataset will be written.")
    parser.add_argument(
        "--dataset-name",
        help="Optional dataset folder name. Defaults to a timestamped run folder.",
    )
    parser.add_argument(
        "--show-skipped",
        action="store_true",
        help="Print each skipped file and reason after the summary.",
    )
    args = parser.parse_args()

    export_result = export_folder_to_markdown(
        args.input_folder,
        args.output_folder,
        dataset_name=args.dataset_name,
    )

    print(f"Input folder: {export_result.input_dir}")
    print(f"Output folder: {export_result.dataset_dir}")
    print(f"Files processed: {export_result.files_processed}")
    print(f"Files skipped: {export_result.files_skipped}")
    print(f"Total evidence units: {export_result.total_evidence_units}")
    print(f"Counts by unit type: {_format_unit_type_counts(export_result.unit_type_counts)}")
    print("Markdown files:")
    for category, export_file in export_result.files.items():
        print(f"  - {category}: {export_file.path} ({export_file.item_count} items)")
    if args.show_skipped and export_result.skipped_files:
        print("Skipped files:")
        for skipped_file in export_result.skipped_files:
            print(f"  - {skipped_file.path}: {skipped_file.reason}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
