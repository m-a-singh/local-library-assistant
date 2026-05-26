"""Deterministic identifier helpers for the MVP evidence pipeline."""

from __future__ import annotations

import hashlib
from pathlib import Path

from canonical_data_model import SourceLocator


def _digest(value: str | bytes, length: int = 16) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:length]


def build_content_hash(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def build_source_document_id(path: Path) -> str:
    return f"source-document:{_digest(path.resolve().as_posix())}"


def build_source_snapshot_id(source_document_id: str, content_hash: str) -> str:
    return f"source-snapshot:{_digest(f'{source_document_id}|{content_hash}')}"


def build_extracted_text_id(source_snapshot_id: str, stage: str) -> str:
    return f"extracted-text:{_digest(f'{source_snapshot_id}|{stage}')}"


def _locator_key(locator: SourceLocator) -> str:
    return "|".join(
        [
            locator.source_uri,
            str(locator.line_start),
            str(locator.line_end),
            str(locator.char_start),
            str(locator.char_end),
            str(locator.page),
            str(locator.block),
        ]
    )


def build_structural_region_id(
    source_snapshot_id: str,
    region_type: str,
    locator: SourceLocator,
) -> str:
    return f"structural-region:{_digest(f'{source_snapshot_id}|{region_type}|{_locator_key(locator)}')}"


def build_evidence_unit_id(
    source_snapshot_id: str,
    unit_type: str,
    locator: SourceLocator,
) -> str:
    return f"evidence-unit:{_digest(f'{source_snapshot_id}|{unit_type}|{_locator_key(locator)}')}"
