"""Build the Step 12 EvidenceUnit SQLite database from a source folder."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mvp_pipeline.pipeline import run_folder_pipeline


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""

    parser = argparse.ArgumentParser(
        description="Ingest a folder and build the SQLite EvidenceUnit database."
    )
    parser.add_argument(
        "input_folder",
        help="Folder containing source files to ingest.",
    )
    parser.add_argument(
        "--db-path",
        default="evidence_units.db",
        help="SQLite database path to build. Defaults to evidence_units.db.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run folder ingestion and build the EvidenceUnit SQLite database."""

    parser = build_parser()
    args = parser.parse_args(argv)

    input_folder = Path(args.input_folder).expanduser().resolve()
    db_path = args.db_path

    if not input_folder.exists() or not input_folder.is_dir():
        print(f"Input folder is not a readable directory: {input_folder}", file=sys.stderr)
        return 2

    print("Building EvidenceUnit database...")
    print(f"Input folder: {input_folder}")
    print(f"DB path: {db_path}")

    try:
        results = run_folder_pipeline(input_folder, db_path=db_path)
    except KeyboardInterrupt:
        print("Build interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Failed to build EvidenceUnit DB: {exc}", file=sys.stderr)
        return 1

    processed_files = len(results)
    indexed_units = sum(len(result.evidence_units) for result in results)

    print("Build complete.")
    print(f"Files processed: {processed_files}")
    print(f"EvidenceUnits indexed: {indexed_units}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
