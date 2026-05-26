"""Run the MVP pipeline for one file and print derived artifact structures."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mvp_pipeline import run_pipeline  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Path to a local .txt or .md file")
    args = parser.parse_args()

    result = run_pipeline(args.path)
    artifacts = result.build_derived_artifacts()

    print(f"Source: {result.source_document.source_uri}")
    print(f"Derived artifacts: {len(artifacts)}")
    print()

    for artifact in artifacts:
        print(f"{artifact.artifact_type}: {artifact.derived_artifact_id}")
        print(json.dumps(artifact.to_dict(), indent=2))
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
