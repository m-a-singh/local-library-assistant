"""Structural segmentation for the MVP evidence pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Callable

from canonical_data_model import (
    AmbiguityMarker,
    AmbiguitySeverity,
    AnchoredSpan,
    ResolverStatus,
    SourceDocument,
    SourceLocator,
    StructuralRegion,
    TextSpan,
)

from .ids import build_structural_region_id
from .normalize import SegmentationInput, SegmentationLine

ATX_HEADING_RE = re.compile(r"^[ \t]{0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
FENCE_START_RE = re.compile(r"^[ \t]{0,3}((`{3,})|(~{3,}))(.*)$")

RegionBuilder = Callable[
    [SourceDocument, SegmentationInput, list[SegmentationLine], StructuralRegion],
    list[StructuralRegion],
]


@dataclass(frozen=True, slots=True)
class _SectionContext:
    level: int
    region_id: str
    label: str


@dataclass(frozen=True, slots=True)
class SegmentationProfile:
    profile_name: str
    source_kinds: tuple[str, ...]
    build_regions: RegionBuilder


def _aggregate_span(lines: list[SegmentationLine], source_uri: str) -> AnchoredSpan:
    if not lines:
        return AnchoredSpan(locator=SourceLocator(source_uri=source_uri), text_span=TextSpan(0, 0))

    first = lines[0]
    last = lines[-1]
    return AnchoredSpan(
        locator=SourceLocator(
            source_uri=source_uri,
            line_start=first.source_locator.line_start,
            line_end=last.source_locator.line_end,
            char_start=first.source_locator.char_start,
            char_end=last.source_locator.char_end,
        ),
        text_span=TextSpan(first.text_span.start, last.text_span.end),
    )


def _make_region(
    source: SourceDocument,
    segmentation_input: SegmentationInput,
    ordinal: int,
    region_type: str,
    lines: list[SegmentationLine],
    boundary_basis: str,
    content_facets: list[str],
    parent_region_id: str | None = None,
    label: str | None = None,
    heading_path: list[str] | None = None,
    ambiguity: list[AmbiguityMarker] | None = None,
) -> StructuralRegion:
    source_uri = source.source_uri
    anchored_span = _aggregate_span(lines, source_uri)
    return StructuralRegion(
        structural_region_id=build_structural_region_id(
            source.source_snapshot_id,
            region_type,
            anchored_span.locator,
        ),
        source_document_id=source.source_document_id,
        source_snapshot_id=source.source_snapshot_id,
        extracted_text_id=segmentation_input.extracted_text_id,
        region_type=region_type,
        primary_span=anchored_span,
        ordinal=ordinal,
        content_facets=content_facets,
        boundary_basis=boundary_basis,
        parent_region_id=parent_region_id,
        label=label,
        heading_path=list(heading_path or []),
        ambiguity=list(ambiguity or []),
    )


def _matching_fence(line: str) -> tuple[str, int] | None:
    match = FENCE_START_RE.match(line)
    if not match:
        return None
    marker = match.group(1)
    return marker[0], len(marker)


def _is_closing_fence(line: str, fence_char: str, fence_length: int) -> bool:
    stripped = line.strip()
    return bool(stripped) and set(stripped) == {fence_char} and len(stripped) >= fence_length


def _apply_relationships(regions: list[StructuralRegion]) -> list[StructuralRegion]:
    regions_by_id = {region.structural_region_id: region for region in regions}
    child_groups: dict[str, list[StructuralRegion]] = {}
    for region in regions:
        if region.parent_region_id is None:
            continue
        child_groups.setdefault(region.parent_region_id, []).append(region)

    sibling_links: dict[str, tuple[str | None, str | None]] = {}
    child_counts: dict[str, int] = {}
    for parent_id, children in child_groups.items():
        ordered = sorted(
            children,
            key=lambda region: (
                region.primary_span.text_span.start if region.primary_span.text_span else 0,
                region.primary_span.text_span.end if region.primary_span.text_span else 0,
                region.ordinal,
            ),
        )
        child_counts[parent_id] = len(ordered)
        for index, region in enumerate(ordered):
            prev_id = ordered[index - 1].structural_region_id if index > 0 else None
            next_id = ordered[index + 1].structural_region_id if index + 1 < len(ordered) else None
            sibling_links[region.structural_region_id] = (prev_id, next_id)

    updated: list[StructuralRegion] = []
    for region in regions:
        prev_id, next_id = sibling_links.get(region.structural_region_id, (None, None))
        updated.append(
            replace(
                regions_by_id[region.structural_region_id],
                prev_region_id=prev_id,
                next_region_id=next_id,
                children_count=child_counts.get(region.structural_region_id, region.children_count),
            )
        )
    return updated


def _build_document_root(
    source: SourceDocument,
    segmentation_input: SegmentationInput,
    lines: list[SegmentationLine],
) -> StructuralRegion:
    return _make_region(
        source=source,
        segmentation_input=segmentation_input,
        ordinal=0,
        region_type="document_root",
        lines=lines,
        boundary_basis="document_root",
        content_facets=["prose"],
        parent_region_id=None,
        heading_path=[],
    )


def _build_plain_text_regions(
    source: SourceDocument,
    segmentation_input: SegmentationInput,
    lines: list[SegmentationLine],
    root_region: StructuralRegion,
) -> list[StructuralRegion]:
    regions: list[StructuralRegion] = []
    ordinal = 1
    index = 0

    while index < len(lines):
        if lines[index].is_blank:
            index += 1
            continue

        start = index
        while index < len(lines) and not lines[index].is_blank:
            index += 1

        regions.append(
            _make_region(
                source=source,
                segmentation_input=segmentation_input,
                ordinal=ordinal,
                region_type="paragraph_block",
                lines=lines[start:index],
                boundary_basis="blank_line_separated_block",
                content_facets=["prose"],
                parent_region_id=root_region.structural_region_id,
                heading_path=[],
            )
        )
        ordinal += 1

    return regions


def _build_markdown_regions(
    source: SourceDocument,
    segmentation_input: SegmentationInput,
    lines: list[SegmentationLine],
    root_region: StructuralRegion,
) -> list[StructuralRegion]:
    regions: list[StructuralRegion] = []
    sections: list[_SectionContext] = []
    ordinal = 1
    index = 0

    while index < len(lines):
        current = lines[index]
        stripped = current.stripped_line

        if current.is_blank:
            index += 1
            continue

        heading_match = ATX_HEADING_RE.match(stripped)
        if heading_match:
            level = len(heading_match.group(1))
            label = heading_match.group(2).strip()
            while sections and sections[-1].level >= level:
                sections.pop()

            parent_region_id = sections[-1].region_id if sections else root_region.structural_region_id
            heading_path = [section.label for section in sections] + [label]
            region = _make_region(
                source=source,
                segmentation_input=segmentation_input,
                ordinal=ordinal,
                region_type="section_anchor",
                lines=[current],
                boundary_basis="markdown_atx_heading",
                content_facets=["prose"],
                parent_region_id=parent_region_id,
                label=label,
                heading_path=heading_path,
            )
            regions.append(region)
            sections.append(_SectionContext(level=level, region_id=region.structural_region_id, label=label))
            ordinal += 1
            index += 1
            continue

        fence = _matching_fence(stripped)
        if fence:
            fence_char, fence_length = fence
            start = index
            index += 1
            found_close = False
            while index < len(lines):
                if _is_closing_fence(lines[index].stripped_line, fence_char, fence_length):
                    found_close = True
                    index += 1
                    break
                index += 1

            block_lines = lines[start:index]
            heading_path = [section.label for section in sections]
            parent_region_id = sections[-1].region_id if sections else root_region.structural_region_id
            ambiguity: list[AmbiguityMarker] = []
            if not found_close:
                block_span = _aggregate_span(block_lines, source.source_uri)
                ambiguity.append(
                    AmbiguityMarker(
                        ambiguity_type="unclosed_fenced_code_block",
                        reason="Reached end of file before a matching closing fence was found.",
                        severity=AmbiguitySeverity.LOW,
                        resolver_status=ResolverStatus.UNRESOLVED,
                        affected_locator=block_span.locator,
                    )
                )

            regions.append(
                _make_region(
                    source=source,
                    segmentation_input=segmentation_input,
                    ordinal=ordinal,
                    region_type="fenced_code_block",
                    lines=block_lines,
                    boundary_basis="markdown_fence_block",
                    content_facets=["code"],
                    parent_region_id=parent_region_id,
                    heading_path=heading_path,
                    ambiguity=ambiguity,
                )
            )
            ordinal += 1
            continue

        start = index
        while index < len(lines):
            candidate = lines[index]
            candidate_stripped = candidate.stripped_line
            if candidate.is_blank:
                break
            if ATX_HEADING_RE.match(candidate_stripped) or _matching_fence(candidate_stripped):
                break
            index += 1

        block_lines = lines[start:index]
        parent_region_id = sections[-1].region_id if sections else root_region.structural_region_id
        heading_path = [section.label for section in sections]
        regions.append(
            _make_region(
                source=source,
                segmentation_input=segmentation_input,
                ordinal=ordinal,
                region_type="paragraph_block",
                lines=block_lines,
                boundary_basis="blank_line_separated_block",
                content_facets=["prose"],
                parent_region_id=parent_region_id,
                heading_path=heading_path,
            )
        )
        ordinal += 1

    return regions


PLAIN_TEXT_SEGMENTATION_PROFILE = SegmentationProfile(
    profile_name="plain_text",
    source_kinds=("text", "plain_text"),
    build_regions=_build_plain_text_regions,
)

MARKDOWN_SEGMENTATION_PROFILE = SegmentationProfile(
    profile_name="markdown",
    source_kinds=("markdown",),
    build_regions=_build_markdown_regions,
)

SEGMENTATION_PROFILES = {
    profile.profile_name: profile
    for profile in (
        PLAIN_TEXT_SEGMENTATION_PROFILE,
        MARKDOWN_SEGMENTATION_PROFILE,
    )
}

SOURCE_KIND_TO_PROFILE_NAME = {
    source_kind: profile.profile_name
    for profile in SEGMENTATION_PROFILES.values()
    for source_kind in profile.source_kinds
}


def select_segmentation_profile(source: SourceDocument) -> SegmentationProfile:
    profile_name = SOURCE_KIND_TO_PROFILE_NAME.get(
        source.source_kind,
        PLAIN_TEXT_SEGMENTATION_PROFILE.profile_name,
    )
    return SEGMENTATION_PROFILES[profile_name]


def build_structural_regions(
    source: SourceDocument,
    segmentation_input: SegmentationInput,
) -> list[StructuralRegion]:
    lines = list(segmentation_input.lines)
    root_region = _build_document_root(source, segmentation_input, lines)
    profile = select_segmentation_profile(source)
    regions = [root_region, *profile.build_regions(source, segmentation_input, lines, root_region)]
    return _apply_relationships(regions)
