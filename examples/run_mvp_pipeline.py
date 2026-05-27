"""Run the MVP evidence pipeline against one local file and print evidence units."""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mvp_pipeline import run_pipeline  # noqa: E402


def _format_range(start: int | None, end: int | None) -> str:
    if start is None and end is None:
        return "n/a"
    return f"{start}:{end}"


def _format_text_span(start: int | None, end: int | None) -> str:
    if start is None and end is None:
        return "n/a"
    return f"{start}:{end}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Path to a local .txt or .md file")
    args = parser.parse_args()

    result = run_pipeline(args.path)
    regions_by_id = {region.structural_region_id: region for region in result.structural_regions}

    print(f"Source: {result.source_document.source_uri}")
    print(f"Snapshot: {result.source_document.source_snapshot_id}")
    print(f"Evidence units: {len(result.evidence_units)}")
    print()

    for index, evidence_unit in enumerate(result.evidence_units, start=1):
        parent_anchor = regions_by_id.get(evidence_unit.parent_region_id) if evidence_unit.parent_region_id else None
        print(f"[{index}] {evidence_unit.evidence_unit_id}")
        print(f"  unit_type: {evidence_unit.unit_type}")
        print(f"  source: {evidence_unit.source_uri}")
        print(f"  line_range: {_format_range(evidence_unit.line_start, evidence_unit.line_end)}")
        print(f"  char_range: {_format_range(evidence_unit.char_start, evidence_unit.char_end)}")
        text_span = evidence_unit.text_span
        print(
            "  text_span: "
            f"{_format_text_span(text_span.start if text_span else None, text_span.end if text_span else None)}"
        )
        print(f"  parent_region_id: {evidence_unit.parent_region_id or 'None'}")
        print(f"  previous_unit_id: {evidence_unit.previous_unit_id or 'None'}")
        print(f"  next_unit_id: {evidence_unit.next_unit_id or 'None'}")
        print(f"  parent_structural_anchor: {parent_anchor.structural_region_id if parent_anchor else 'None'}")
        print(
            f"  context_labels: {', '.join(evidence_unit.context_labels) if evidence_unit.context_labels else 'None'}"
        )
        print(f"  confidence: {evidence_unit.confidence.value}")
        print(f"  flags: {', '.join(evidence_unit.flags) if evidence_unit.flags else 'None'}")
        print(f"  signals: {', '.join(evidence_unit.signals) if evidence_unit.signals else 'None'}")
        print(f"  support_roles: {', '.join(link.role for link in evidence_unit.support_links)}")
        print("  canonical_text:")
        print(textwrap.indent(evidence_unit.canonical_text, "    "))
        print()

    if result.evidence_units:
        target_index = 1 if len(result.evidence_units) > 1 else 0
        target_unit = result.evidence_units[target_index]
        neighbors = result.get_neighbors(target_unit, 1)
        expanded = result.expand_context(target_unit, 1)
        print(f"Neighbors around {target_unit.evidence_unit_id}:")
        print("  " + ", ".join(neighbor.evidence_unit_id for neighbor in neighbors) if neighbors else "  None")
        print(f"Expanded context for {target_unit.evidence_unit_id}:")
        print("  " + ", ".join(unit.evidence_unit_id for unit in expanded))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
