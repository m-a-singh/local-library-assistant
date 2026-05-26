"""Show the normalization-to-segmentation contract for one local file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mvp_pipeline import (  # noqa: E402
    build_segmentation_input,
    build_structural_regions,
    ingest_local_file,
    normalize_extracted_text,
    select_segmentation_profile,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Path to a local .txt or .md file")
    args = parser.parse_args()

    source_document, raw_extracted_text = ingest_local_file(args.path)
    normalized_extracted_text = normalize_extracted_text(raw_extracted_text)
    segmentation_input = build_segmentation_input(normalized_extracted_text)
    segmentation_profile = select_segmentation_profile(source_document)
    structural_regions = build_structural_regions(source_document, segmentation_input)

    print("Normalized text contract")
    print(f"  source: {source_document.source_uri}")
    print(f"  snapshot: {source_document.source_snapshot_id}")
    print(f"  segmentation_profile: {segmentation_profile.profile_name}")
    print(f"  normalized_mode: {normalized_extracted_text.extraction_mode}")
    print(f"  fidelity_state: {segmentation_input.fidelity_state}")
    print(f"  has_cr: {'\\r' in segmentation_input.text}")
    print(f"  has_bom: {segmentation_input.text.startswith(chr(0xfeff))}")
    print(f"  line_count: {len(segmentation_input.lines)}")
    print("  normalization_notes:")
    if segmentation_input.normalization_notes:
        for note in segmentation_input.normalization_notes:
            print(f"    - {note.note_type}: {note.description}")
    else:
        print("    - none")

    print("  line_mapping:")
    for line in segmentation_input.lines:
        print(
            "    "
            f"line {line.line_number} "
            f"normalized_span=({line.text_span.start},{line.text_span.end}) "
            f"source_line={line.source_locator.line_start} "
            f"source_chars=({line.source_locator.char_start},{line.source_locator.char_end}) "
            f"text={line.text!r}"
        )

    print()
    print("Structural regions built from the contract:")
    for region in structural_regions:
        locator = region.primary_span.locator
        print(
            "  "
            f"{region.region_type} "
            f"lines=({locator.line_start},{locator.line_end}) "
            f"parent={region.parent_region_id} "
            f"heading_path={region.heading_path}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
