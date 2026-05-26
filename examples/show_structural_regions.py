"""Run structural segmentation only and print StructuralRegion boundaries."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mvp_pipeline import run_structural_segmentation  # noqa: E402


def _format_range(start: int | None, end: int | None) -> str:
    if start is None and end is None:
        return "n/a"
    return f"{start}:{end}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Path to a local .txt or .md file")
    args = parser.parse_args()

    structural_regions = run_structural_segmentation(args.path)

    print(f"Structural regions: {len(structural_regions)}")
    print()
    for index, region in enumerate(structural_regions, start=1):
        print(f"[{index}] {region.structural_region_id}")
        print(f"  region_type: {region.region_type}")
        print(f"  line_range: {_format_range(region.line_start, region.line_end)}")
        print(f"  char_range: {_format_range(region.char_start, region.char_end)}")
        print(f"  parent_region_id: {region.parent_region_id or 'None'}")
        print(f"  prev_region_id: {region.prev_region_id or 'None'}")
        print(f"  next_region_id: {region.next_region_id or 'None'}")
        print(f"  heading_path: {region.heading_path or []}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
