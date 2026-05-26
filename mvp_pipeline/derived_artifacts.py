"""Derived artifact builders for commands, SQL queries, and links."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from canonical_data_model import ArtifactTarget, DerivedArtifact, EvidenceUnit, RecordRef, SourceDocument, SourceLocator, SourceScope

from .evidence import (
    COMMAND_PROMPT_RE,
    GENERIC_COMMAND_HEAD_TOKENS,
    SHELLISH_TAIL_MARKERS,
    SQL_START_RE,
    UNAMBIGUOUS_COMMAND_HEAD_TOKENS,
)
from .ids import build_content_hash

ARTIFACT_SCHEMA_VERSION = "0.1.0"
PRODUCER_NAME = "mvp_derived_artifact_builder"
PRODUCER_VERSION = "0.1.0"
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
BARE_URL_RE = re.compile(r"https?://[^\s<>()]+")
REMOTE_PROMPT_RE = re.compile(r"^[\w.-]+@[\w.-]+[:~/$._-]*[$#]\s+(.*)$")


@dataclass(frozen=True)
class _TargetEntry:
    target_ref: ArtifactTarget
    payload_entry: dict[str, Any]
    evidence_unit_id: str


def build_derived_artifacts(
    source: SourceDocument,
    evidence_units: list[EvidenceUnit],
) -> list[DerivedArtifact]:
    artifacts: list[DerivedArtifact] = []

    command_artifact = build_command_index(source, evidence_units)
    if command_artifact is not None:
        artifacts.append(command_artifact)

    query_artifact = build_query_index(source, evidence_units)
    if query_artifact is not None:
        artifacts.append(query_artifact)

    link_artifact = build_link_index(source, evidence_units)
    if link_artifact is not None:
        artifacts.append(link_artifact)

    return artifacts


def build_command_index(
    source: SourceDocument,
    evidence_units: list[EvidenceUnit],
) -> DerivedArtifact | None:
    entries: list[_TargetEntry] = []
    for unit in _sorted_units(evidence_units):
        if not _is_command_unit(unit):
            continue
        entries.extend(_command_entries_for_unit(unit))
    return _build_artifact(source, "command_index", "command", entries)


def build_query_index(
    source: SourceDocument,
    evidence_units: list[EvidenceUnit],
) -> DerivedArtifact | None:
    entries: list[_TargetEntry] = []
    for unit in _sorted_units(evidence_units):
        if not _is_query_unit(unit):
            continue
        entry = _query_entry_for_unit(unit)
        if entry is not None:
            entries.append(entry)
    return _build_artifact(source, "query_index", "query", entries)


def build_link_index(
    source: SourceDocument,
    evidence_units: list[EvidenceUnit],
) -> DerivedArtifact | None:
    entries: list[_TargetEntry] = []
    for unit in _sorted_units(evidence_units):
        entries.extend(_link_entries_for_unit(unit))
    return _build_artifact(source, "link_index", "link", entries)


def _sorted_units(evidence_units: list[EvidenceUnit]) -> list[EvidenceUnit]:
    return sorted(
        evidence_units,
        key=lambda unit: (
            unit.line_start if unit.line_start is not None else 0,
            unit.ordinal,
            unit.evidence_unit_id,
        ),
    )


def _is_command_unit(unit: EvidenceUnit) -> bool:
    return unit.unit_type == "command" or "command_candidate" in unit.signals


def _is_query_unit(unit: EvidenceUnit) -> bool:
    return unit.unit_type == "sql" or "sql_candidate" in unit.signals


def _command_entries_for_unit(unit: EvidenceUnit) -> list[_TargetEntry]:
    entries: list[_TargetEntry] = []
    for line_index, line_offset, raw_line in _iter_line_offsets(unit.canonical_text):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or SQL_START_RE.match(stripped):
            continue
        if not _looks_like_command_line(stripped):
            continue

        value = _normalize_command_value(stripped)
        locator = _line_locator(unit, line_index, line_offset, len(raw_line))
        entries.append(
            _TargetEntry(
                target_ref=ArtifactTarget(
                    target_type="command",
                    value=value,
                    evidence_unit_ids=[unit.evidence_unit_id],
                    locator=locator,
                ),
                payload_entry={"unit_line_index": line_index},
                evidence_unit_id=unit.evidence_unit_id,
            )
        )
    return entries


def _query_entry_for_unit(unit: EvidenceUnit) -> _TargetEntry | None:
    sql_lines: list[str] = []
    statement_kind: str | None = None
    statement_line_index: int | None = None
    statement_line_offset: int | None = None
    statement_line_length: int | None = None

    for line_index, line_offset, raw_line in _iter_line_offsets(unit.canonical_text):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = SQL_START_RE.match(stripped)
        if statement_kind is None:
            if not match:
                continue
            statement_kind = match.group(1).lower()
            statement_line_index = line_index
            statement_line_offset = line_offset
            statement_line_length = len(raw_line)
        sql_lines.append(stripped)

    if statement_kind is None or statement_line_index is None or statement_line_offset is None or statement_line_length is None:
        return None

    return _TargetEntry(
        target_ref=ArtifactTarget(
            target_type="query",
            value=sql_lines[0],
            evidence_unit_ids=[unit.evidence_unit_id],
            locator=_line_locator(unit, statement_line_index, statement_line_offset, statement_line_length),
        ),
        payload_entry={"statement_kind": statement_kind},
        evidence_unit_id=unit.evidence_unit_id,
    )


def _link_entries_for_unit(unit: EvidenceUnit) -> list[_TargetEntry]:
    entries: list[_TargetEntry] = []
    seen: set[tuple[int, str]] = set()
    text = unit.canonical_text

    for match in MARKDOWN_LINK_RE.finditer(text):
        value = match.group(2).strip()
        if not value:
            continue
        key = (match.start(2), value)
        if key in seen:
            continue
        seen.add(key)
        line_index, line_offset, line_length = _line_position(text, match.start(2))
        entries.append(
            _TargetEntry(
                target_ref=ArtifactTarget(
                    target_type="link",
                    value=value,
                    evidence_unit_ids=[unit.evidence_unit_id],
                    locator=_line_locator(unit, line_index, line_offset, line_length),
                ),
                payload_entry={"link_kind": "markdown_link"},
                evidence_unit_id=unit.evidence_unit_id,
            )
        )

    for match in BARE_URL_RE.finditer(text):
        value = match.group(0).rstrip(".,);]")
        if not value:
            continue
        key = (match.start(), value)
        if key in seen:
            continue
        seen.add(key)
        line_index, line_offset, line_length = _line_position(text, match.start())
        entries.append(
            _TargetEntry(
                target_ref=ArtifactTarget(
                    target_type="link",
                    value=value,
                    evidence_unit_ids=[unit.evidence_unit_id],
                    locator=_line_locator(unit, line_index, line_offset, line_length),
                ),
                payload_entry={"link_kind": "bare_url"},
                evidence_unit_id=unit.evidence_unit_id,
            )
        )

    return entries


def _looks_like_command_line(stripped_line: str) -> bool:
    if COMMAND_PROMPT_RE.match(stripped_line):
        return True
    tokens = stripped_line.split()
    if not tokens:
        return False
    first_token = tokens[0]
    if first_token in UNAMBIGUOUS_COMMAND_HEAD_TOKENS:
        return True
    if first_token in GENERIC_COMMAND_HEAD_TOKENS:
        if len(tokens) == 1:
            return True
        tail = " ".join(tokens[1:])
        return any(marker in tail for marker in SHELLISH_TAIL_MARKERS)
    return False


def _normalize_command_value(stripped_line: str) -> str:
    if stripped_line.startswith("$ ") or stripped_line.startswith("% "):
        return stripped_line[2:].strip()
    remote_prompt = REMOTE_PROMPT_RE.match(stripped_line)
    if remote_prompt:
        return remote_prompt.group(1).strip()
    return stripped_line


def _iter_line_offsets(text: str) -> list[tuple[int, int, str]]:
    offsets: list[tuple[int, int, str]] = []
    cursor = 0
    for index, line in enumerate(text.splitlines(keepends=True), start=0):
        offsets.append((index, cursor, line))
        cursor += len(line)
    if not offsets and text:
        offsets.append((0, 0, text))
    return offsets


def _line_position(text: str, char_index: int) -> tuple[int, int, int]:
    for line_index, line_offset, raw_line in _iter_line_offsets(text):
        line_end = line_offset + len(raw_line)
        if line_offset <= char_index < line_end:
            return line_index, line_offset, len(raw_line)
    if text:
        last_index, last_offset, last_line = _iter_line_offsets(text)[-1]
        return last_index, last_offset, len(last_line)
    return 0, 0, 0


def _line_locator(unit: EvidenceUnit, line_index: int, line_offset: int, line_length: int) -> SourceLocator | None:
    source_uri = unit.source_uri
    if source_uri is None:
        return None
    line_start = unit.line_start + line_index if unit.line_start is not None else None
    line_end = line_start
    char_start = unit.char_start + line_offset if unit.char_start is not None else None
    char_end = char_start + line_length if char_start is not None else None
    return SourceLocator(
        source_uri=source_uri,
        line_start=line_start,
        line_end=line_end,
        char_start=char_start,
        char_end=char_end,
    )


def _build_artifact(
    source: SourceDocument,
    artifact_type: str,
    target_type: str,
    entries: list[_TargetEntry],
) -> DerivedArtifact | None:
    if not entries:
        return None

    target_refs = [entry.target_ref for entry in entries]
    payload = {
        "index_type": artifact_type,
        "entry_type": target_type,
        "entries": [
            {"target_ref_index": index, **entry.payload_entry}
            for index, entry in enumerate(entries)
        ],
    }

    derived_from_ids: list[str] = []
    for entry in entries:
        if entry.evidence_unit_id not in derived_from_ids:
            derived_from_ids.append(entry.evidence_unit_id)

    source_scope = SourceScope(
        scope_type="snapshot",
        source_document_ids=[source.source_document_id],
        source_snapshot_ids=[source.source_snapshot_id],
    )
    artifact_hash = _build_artifact_hash(source_scope, artifact_type, payload, target_refs, derived_from_ids)

    return DerivedArtifact(
        derived_artifact_id=_build_derived_artifact_id(source.source_snapshot_id, artifact_type, artifact_hash),
        artifact_type=artifact_type,
        artifact_schema_version=ARTIFACT_SCHEMA_VERSION,
        source_scope=source_scope,
        derived_from=[RecordRef("EvidenceUnit", evidence_unit_id) for evidence_unit_id in derived_from_ids],
        payload=payload,
        producer_name=PRODUCER_NAME,
        producer_version=PRODUCER_VERSION,
        artifact_hash=artifact_hash,
        target_refs=target_refs,
    )


def _build_artifact_hash(
    source_scope: SourceScope,
    artifact_type: str,
    payload: dict[str, Any],
    target_refs: list[ArtifactTarget],
    derived_from_ids: list[str],
) -> str:
    target_ref_payload = [
        {
            "target_type": target_ref.target_type,
            "value": target_ref.value,
            "evidence_unit_ids": target_ref.evidence_unit_ids,
            "locator": None
            if target_ref.locator is None
            else {
                "source_uri": target_ref.locator.source_uri,
                "line_start": target_ref.locator.line_start,
                "line_end": target_ref.locator.line_end,
                "char_start": target_ref.locator.char_start,
                "char_end": target_ref.locator.char_end,
            },
        }
        for target_ref in target_refs
    ]
    signature = json.dumps(
        {
            "artifact_type": artifact_type,
            "source_scope": {
                "scope_type": source_scope.scope_type,
                "source_document_ids": source_scope.source_document_ids,
                "source_snapshot_ids": source_scope.source_snapshot_ids,
            },
            "derived_from_ids": derived_from_ids,
            "payload": payload,
            "target_refs": target_ref_payload,
        },
        sort_keys=True,
    )
    return build_content_hash(signature)


def _build_derived_artifact_id(source_snapshot_id: str, artifact_type: str, artifact_hash: str) -> str:
    digest = hashlib.sha256(f"{source_snapshot_id}|{artifact_type}|{artifact_hash}".encode("utf-8")).hexdigest()[:16]
    return f"derived-artifact:{digest}"


__all__ = [
    "build_command_index",
    "build_derived_artifacts",
    "build_link_index",
    "build_query_index",
]
