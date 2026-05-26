"""Corpus-level markdown audit export for evidence units."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from canonical_data_model import EvidenceUnit

from .ingest import is_supported_source_file
from .pipeline import PipelineResult, run_pipeline

BARE_URL_RE = re.compile(r"https?://[^\s<>()]+")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
BACKTICK_RUN_RE = re.compile(r"`+")
EXPORT_FILENAMES = {
    "prose": "prose.md",
    "commands": "commands.md",
    "sql": "sql.md",
    "links": "links.md",
}

EXPORT_TITLES = {
    "prose": "Prose Evidence Audit",
    "commands": "Command Evidence Audit",
    "sql": "SQL Evidence Audit",
    "links": "Link Evidence Audit",
}

EXPORT_INTROS = {
    "prose": (
        "Audit view for non-command and non-SQL evidence units. "
        "This includes prose, heading sections, code, and any other remaining units. "
        "Canonical pipeline objects remain the source of truth."
    ),
    "commands": (
        "Audit view for command-like evidence units. "
        "Canonical pipeline objects remain the source of truth."
    ),
    "sql": (
        "Audit view for SQL-like evidence units. "
        "Canonical pipeline objects remain the source of truth."
    ),
    "links": (
        "Cross-cut audit view for evidence units that contain link targets. "
        "Items may also appear in other files. Canonical pipeline objects remain the source of truth."
    ),
}


@dataclass(frozen=True)
class MarkdownExportFile:
    category: str
    path: Path
    item_count: int


@dataclass(frozen=True)
class MarkdownExportResult:
    dataset_dir: Path
    files: dict[str, MarkdownExportFile]


@dataclass(frozen=True)
class SkippedSourceFile:
    path: Path
    reason: str


@dataclass(frozen=True)
class BatchMarkdownExportResult:
    input_dir: Path
    dataset_dir: Path
    files: dict[str, MarkdownExportFile]
    processed_files: list[Path]
    skipped_files: list[SkippedSourceFile]
    total_evidence_units: int
    unit_type_counts: dict[str, int]

    @property
    def files_processed(self) -> int:
        return len(self.processed_files)

    @property
    def files_skipped(self) -> int:
        return len(self.skipped_files)


@dataclass(frozen=True)
class _ExportItem:
    source_file: str
    evidence_unit: EvidenceUnit
    link_targets: list[str]


def collect_link_targets(text: str) -> list[str]:
    targets: list[str] = []
    seen: set[str] = set()

    for match in MARKDOWN_LINK_RE.finditer(text):
        target = match.group(1).strip()
        if target and target not in seen:
            seen.add(target)
            targets.append(target)

    for match in BARE_URL_RE.finditer(text):
        target = match.group(0).rstrip(".,);]")
        if target and target not in seen:
            seen.add(target)
            targets.append(target)

    return targets


def export_evidence_units_to_markdown(
    evidence_units: Iterable[EvidenceUnit],
    output_root: str | Path,
    *,
    dataset_name: str | None = None,
) -> MarkdownExportResult:
    dataset_dir = _build_dataset_dir(output_root, dataset_name)
    dataset_dir.mkdir(parents=True, exist_ok=True)

    export_items = _collect_export_items(evidence_units)
    files: dict[str, MarkdownExportFile] = {}

    for category, filename in EXPORT_FILENAMES.items():
        items = _select_category_items(export_items, category)
        export_path = dataset_dir / filename
        export_path.write_text(_render_category_markdown(category, items), encoding="utf-8")
        files[category] = MarkdownExportFile(
            category=category,
            path=export_path,
            item_count=len(items),
        )

    return MarkdownExportResult(dataset_dir=dataset_dir, files=files)


def export_pipeline_results_to_markdown(
    results: Iterable[PipelineResult],
    output_root: str | Path,
    *,
    dataset_name: str | None = None,
) -> MarkdownExportResult:
    evidence_units: list[EvidenceUnit] = []
    for result in results:
        evidence_units.extend(result.evidence_units)
    return export_evidence_units_to_markdown(
        evidence_units,
        output_root,
        dataset_name=dataset_name,
    )


def discover_source_files(
    input_dir: str | Path,
    *,
    excluded_roots: Iterable[str | Path] = (),
) -> tuple[list[Path], list[SkippedSourceFile]]:
    root = Path(input_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Input folder does not exist or is not a directory: {root}")

    resolved_excluded_roots = [Path(path).expanduser().resolve() for path in excluded_roots]
    supported_files: list[Path] = []
    skipped_files: list[SkippedSourceFile] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        resolved_path = path.resolve()
        if any(_is_relative_to(resolved_path, excluded_root) for excluded_root in resolved_excluded_roots):
            continue
        if is_supported_source_file(resolved_path):
            supported_files.append(resolved_path)
            continue
        skipped_files.append(
            SkippedSourceFile(
                path=resolved_path,
                reason=f"unsupported file type: {resolved_path.suffix or resolved_path.name}",
            )
        )

    return supported_files, skipped_files


def export_folder_to_markdown(
    input_dir: str | Path,
    output_root: str | Path,
    *,
    dataset_name: str | None = None,
) -> BatchMarkdownExportResult:
    resolved_input_dir = Path(input_dir).expanduser().resolve()
    resolved_output_root = Path(output_root).expanduser().resolve()
    dataset_dir = _build_dataset_dir(resolved_output_root, dataset_name)

    excluded_roots = [dataset_dir]
    if resolved_output_root != resolved_input_dir and _is_relative_to(resolved_output_root, resolved_input_dir):
        excluded_roots.insert(0, resolved_output_root)

    supported_files, skipped_files = discover_source_files(resolved_input_dir, excluded_roots=excluded_roots)

    processed_files: list[Path] = []
    evidence_units: list[EvidenceUnit] = []
    unit_type_counts: Counter[str] = Counter()

    for source_file in supported_files:
        try:
            result = run_pipeline(source_file)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            skipped_files.append(SkippedSourceFile(path=source_file, reason=str(exc)))
            continue

        processed_files.append(source_file)
        evidence_units.extend(result.evidence_units)
        unit_type_counts.update(unit.unit_type for unit in result.evidence_units)

    export_result = export_evidence_units_to_markdown(
        evidence_units,
        resolved_output_root,
        dataset_name=dataset_name,
    )
    return BatchMarkdownExportResult(
        input_dir=resolved_input_dir,
        dataset_dir=export_result.dataset_dir,
        files=export_result.files,
        processed_files=processed_files,
        skipped_files=skipped_files,
        total_evidence_units=len(evidence_units),
        unit_type_counts=dict(sorted(unit_type_counts.items())),
    )


def _build_dataset_dir(output_root: str | Path, dataset_name: str | None) -> Path:
    root = Path(output_root).expanduser().resolve()
    if dataset_name:
        return root / dataset_name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return root / f"evidence_markdown_export_{timestamp}"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _collect_export_items(evidence_units: Iterable[EvidenceUnit]) -> list[_ExportItem]:
    items: list[_ExportItem] = []
    for evidence_unit in evidence_units:
        items.append(
            _ExportItem(
                source_file=evidence_unit.source_uri or "unknown_source",
                evidence_unit=evidence_unit,
                link_targets=collect_link_targets(evidence_unit.canonical_text),
            )
        )
    return items


def _select_category_items(items: list[_ExportItem], category: str) -> list[_ExportItem]:
    if category == "commands":
        selected = [item for item in items if item.evidence_unit.unit_type == "command"]
    elif category == "sql":
        selected = [item for item in items if item.evidence_unit.unit_type == "sql"]
    elif category == "links":
        selected = [item for item in items if item.link_targets]
    else:
        selected = [item for item in items if item.evidence_unit.unit_type not in {"command", "sql"}]

    return sorted(
        selected,
        key=lambda item: (
            item.source_file,
            item.evidence_unit.line_start if item.evidence_unit.line_start is not None else 0,
            item.evidence_unit.ordinal,
            item.evidence_unit.evidence_unit_id,
        ),
    )


def _render_category_markdown(category: str, items: list[_ExportItem]) -> str:
    lines = [
        f"# {EXPORT_TITLES[category]}",
        "",
        EXPORT_INTROS[category],
        "",
        f"Items: {len(items)}",
        "",
    ]

    if not items:
        lines.extend(["No matching evidence units.", ""])
        return "\n".join(lines)

    grouped_items: dict[str, list[_ExportItem]] = defaultdict(list)
    for item in items:
        grouped_items[item.source_file].append(item)

    for source_file in sorted(grouped_items):
        lines.append(f"## Source File: {source_file}")
        lines.append("")
        for index, item in enumerate(grouped_items[source_file], start=1):
            lines.extend(_render_export_item(index, item, include_links=(category == "links")))

    return "\n".join(lines).rstrip() + "\n"


def _render_export_item(index: int, item: _ExportItem, *, include_links: bool) -> list[str]:
    evidence_unit = item.evidence_unit

    lines = [
        f"### {index}. {evidence_unit.evidence_unit_id}",
        "",
        f"- Source file: {item.source_file}",
        f"- Evidence unit id: {evidence_unit.evidence_unit_id}",
        f"- Unit type: {evidence_unit.unit_type}",
        f"- Line range: {_format_range(evidence_unit.line_start, evidence_unit.line_end)}",
        f"- Char range: {_format_range(evidence_unit.char_start, evidence_unit.char_end)}",
        f"- Parent region id: {evidence_unit.parent_region_id or 'n/a'}",
        f"- Signals: {_format_list(evidence_unit.signals)}",
        f"- Confidence: {evidence_unit.confidence.value}",
    ]

    if evidence_unit.flags:
        lines.append(f"- Flags: {_format_list(evidence_unit.flags)}")
    if include_links:
        lines.append(f"- Link targets: {_format_list(item.link_targets)}")

    lines.extend(
        [
            "",
            "Original text:",
            _render_original_text_block(evidence_unit.canonical_text),
            "",
            "---",
            "",
        ]
    )
    return lines


def _format_range(start: int | None, end: int | None) -> str:
    if start is None and end is None:
        return "n/a"
    return f"{start}:{end}"


def _format_list(values: list[str]) -> str:
    if not values:
        return "n/a"
    return ", ".join(values)


def _code_fence(text: str) -> str:
    max_run = max((len(match.group(0)) for match in BACKTICK_RUN_RE.finditer(text)), default=0)
    return "`" * max(4, max_run + 1)


def _render_original_text_block(text: str) -> str:
    fence = _code_fence(text)
    suffix = "" if text.endswith("\n") or not text else "\n"
    return f"{fence}text\n{text}{suffix}{fence}"


__all__ = [
    "BatchMarkdownExportResult",
    "MarkdownExportFile",
    "MarkdownExportResult",
    "SkippedSourceFile",
    "collect_link_targets",
    "discover_source_files",
    "export_evidence_units_to_markdown",
    "export_folder_to_markdown",
    "export_pipeline_results_to_markdown",
]
