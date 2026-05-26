"""Local file ingestion for the MVP evidence pipeline."""

from __future__ import annotations

import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import Callable, Iterable
from xml.etree import ElementTree

from canonical_data_model import ExtractedText, FidelityState, SourceDocument

from .ids import (
    build_content_hash,
    build_extracted_text_id,
    build_source_document_id,
    build_source_snapshot_id,
)
from .normalize import build_exact_text_artifacts

EXTRACTOR_NAME = "local_file_reader"
EXTRACTOR_VERSION = "0.2.0"
DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


@dataclass(frozen=True)
class SourceTypeSpec:
    source_kind: str
    mime_type: str
    content_facets: tuple[str, ...]
    reader_kind: str = "text"
    extraction_mode: str = "native_text"


MARKDOWN_SPEC = SourceTypeSpec("markdown", "text/markdown", ("prose", "markdown"))
TEXT_SPEC = SourceTypeSpec("text", "text/plain", ("prose",))
RST_SPEC = SourceTypeSpec("rst", "text/x-rst", ("prose", "markup"))
LOG_SPEC = SourceTypeSpec("log", "text/plain", ("log", "prose"))
SQL_SPEC = SourceTypeSpec("sql", "application/sql", ("sql", "code"))
JSON_SPEC = SourceTypeSpec("json", "application/json", ("config", "data"))
YAML_SPEC = SourceTypeSpec("yaml", "application/yaml", ("config", "data"))
TOML_SPEC = SourceTypeSpec("toml", "application/toml", ("config", "data"))
CODE_SPEC = SourceTypeSpec("code", "text/plain", ("code",))
CONFIG_SPEC = SourceTypeSpec("config", "text/plain", ("config",))
DOCX_SPEC = SourceTypeSpec(
    "docx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ("prose",),
    reader_kind="docx",
    extraction_mode="docx_text",
)
PDF_SPEC = SourceTypeSpec(
    "pdf",
    "application/pdf",
    ("prose",),
    reader_kind="pdf",
    extraction_mode="pdf_text",
)

SUPPORTED_SUFFIX_SPECS: dict[str, SourceTypeSpec] = {
    ".txt": TEXT_SPEC,
    ".md": MARKDOWN_SPEC,
    ".markdown": MARKDOWN_SPEC,
    ".rst": RST_SPEC,
    ".log": LOG_SPEC,
    ".sql": SQL_SPEC,
    ".json": JSON_SPEC,
    ".yaml": YAML_SPEC,
    ".yml": YAML_SPEC,
    ".toml": TOML_SPEC,
    ".py": CODE_SPEC,
    ".js": CODE_SPEC,
    ".ts": CODE_SPEC,
    ".tsx": CODE_SPEC,
    ".jsx": CODE_SPEC,
    ".java": CODE_SPEC,
    ".kt": CODE_SPEC,
    ".go": CODE_SPEC,
    ".rs": CODE_SPEC,
    ".sh": CODE_SPEC,
    ".bash": CODE_SPEC,
    ".zsh": CODE_SPEC,
    ".ini": CONFIG_SPEC,
    ".conf": CONFIG_SPEC,
    ".properties": CONFIG_SPEC,
    ".xml": CONFIG_SPEC,
    ".html": CONFIG_SPEC,
    ".css": CONFIG_SPEC,
    ".scss": CONFIG_SPEC,
    ".c": CODE_SPEC,
    ".cpp": CODE_SPEC,
    ".h": CODE_SPEC,
    ".hpp": CODE_SPEC,
    ".docx": DOCX_SPEC,
    ".pdf": PDF_SPEC,
}

SUPPORTED_NAME_SPECS: dict[str, SourceTypeSpec] = {
    ".env": CONFIG_SPEC,
}


def _detect_source_spec(path: Path) -> SourceTypeSpec:
    suffix = path.suffix.lower()
    if suffix in SUPPORTED_SUFFIX_SPECS:
        return SUPPORTED_SUFFIX_SPECS[suffix]
    if path.name.lower() in SUPPORTED_NAME_SPECS:
        return SUPPORTED_NAME_SPECS[path.name.lower()]
    raise ValueError(f"Unsupported file type for MVP pipeline: {path.suffix or path.name}")


def is_supported_source_file(path: str | Path) -> bool:
    file_path = Path(path)
    try:
        _detect_source_spec(file_path)
    except ValueError:
        return False
    return True


def supported_source_suffixes() -> set[str]:
    return set(SUPPORTED_SUFFIX_SPECS)


def supported_source_names() -> set[str]:
    return set(SUPPORTED_NAME_SPECS)


def _relative_path(path: Path) -> str | None:
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return None


def _read_utf8_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _docx_paragraph_text(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "t" and node.text:
            parts.append(node.text)
        elif tag == "tab":
            parts.append("\t")
        elif tag in {"br", "cr"}:
            parts.append("\n")
    return "".join(parts).strip()


def _read_docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            document_xml = archive.read("word/document.xml")
    except KeyError as exc:
        raise ValueError(f"DOCX file is missing word/document.xml: {path}") from exc
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Unreadable DOCX file: {path}") from exc

    root = ElementTree.fromstring(document_xml)
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", DOCX_NS):
        text = _docx_paragraph_text(paragraph)
        if text:
            paragraphs.append(text)

    if not paragraphs:
        raise ValueError(f"No extractable DOCX text found: {path}")
    return "\n\n".join(paragraphs)


def _read_pdf_text_with_pypdf(path: Path) -> str:
    from pypdf import PdfReader  # type: ignore[import-not-found]

    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        page_text = page_text.strip()
        if page_text:
            pages.append(page_text)
    if not pages:
        raise ValueError(f"No extractable PDF text found: {path}")
    return "\n\n".join(pages)


def _read_pdf_text_with_pypdf2(path: Path) -> str:
    from PyPDF2 import PdfReader  # type: ignore[import-not-found]

    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        page_text = page_text.strip()
        if page_text:
            pages.append(page_text)
    if not pages:
        raise ValueError(f"No extractable PDF text found: {path}")
    return "\n\n".join(pages)


def _read_pdf_text_with_pdftotext(path: Path) -> str:
    binary = which("pdftotext")
    if binary is None:
        raise RuntimeError("pdftotext binary not available")

    result = subprocess.run(
        [binary, "-enc", "UTF-8", str(path), "-"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        error_message = result.stderr.strip() or "unknown pdftotext failure"
        raise ValueError(f"pdftotext failed for {path}: {error_message}")

    text = result.stdout.strip()
    if not text:
        raise ValueError(f"No extractable PDF text found: {path}")
    return text


def _first_successful_text_reader(
    readers: Iterable[tuple[str, Callable[[Path], str]]],
    path: Path,
) -> str:
    failures: list[str] = []
    for reader_name, reader in readers:
        try:
            return reader(path)
        except ModuleNotFoundError:
            failures.append(f"{reader_name}:missing")
        except Exception as exc:
            failures.append(f"{reader_name}:{exc}")
    raise ValueError(
        f"No PDF extraction backend succeeded for {path} "
        f"({' ; '.join(failures) if failures else 'no backends tried'})"
    )


def _read_pdf_text(path: Path) -> str:
    return _first_successful_text_reader(
        (
            ("pypdf", _read_pdf_text_with_pypdf),
            ("PyPDF2", _read_pdf_text_with_pypdf2),
            ("pdftotext", _read_pdf_text_with_pdftotext),
        ),
        path,
    )


def _read_source_text(path: Path, spec: SourceTypeSpec) -> str:
    if spec.reader_kind == "text":
        return _read_utf8_text(path)
    if spec.reader_kind == "docx":
        return _read_docx_text(path)
    if spec.reader_kind == "pdf":
        return _read_pdf_text(path)
    raise ValueError(f"Unsupported reader kind: {spec.reader_kind}")


def ingest_local_file(path: str | Path) -> tuple[SourceDocument, ExtractedText]:
    file_path = Path(path).expanduser().resolve()
    spec = _detect_source_spec(file_path)

    raw_bytes = file_path.read_bytes()
    raw_text = _read_source_text(file_path, spec)
    content_hash = build_content_hash(raw_bytes)
    source_document_id = build_source_document_id(file_path)
    source_snapshot_id = build_source_snapshot_id(source_document_id, content_hash)
    source_uri = str(file_path)

    source_document = SourceDocument(
        source_document_id=source_document_id,
        source_snapshot_id=source_snapshot_id,
        source_uri=source_uri,
        source_kind=spec.source_kind,
        mime_type=spec.mime_type,
        content_hash=content_hash,
        size_bytes=len(raw_bytes),
        relative_path=_relative_path(file_path),
        encoding="utf-8",
        content_facets=list(spec.content_facets),
    )

    artifacts = build_exact_text_artifacts(raw_text, source_uri)
    raw_extracted = ExtractedText(
        extracted_text_id=build_extracted_text_id(source_snapshot_id, "raw"),
        source_document_id=source_document_id,
        source_snapshot_id=source_snapshot_id,
        extractor_name=EXTRACTOR_NAME,
        extractor_version=EXTRACTOR_VERSION,
        extraction_mode=spec.extraction_mode,
        text=artifacts.text,
        text_hash=build_content_hash(artifacts.text),
        span_map=artifacts.span_map,
        fidelity_state=FidelityState.EXACT,
        line_index=artifacts.line_index,
        normalization_notes=[],
        ambiguity=[],
        content_facets=list(spec.content_facets),
    )

    return source_document, raw_extracted


def ingest_folder(folder_path: str | Path) -> tuple[list[SourceDocument], list[ExtractedText]]:
    source_documents: list[SourceDocument] = []
    extracted_texts: list[ExtractedText] = []
    total_files = 0
    processed_files = 0
    skipped_files = 0

    folder = Path(folder_path).expanduser().resolve()

    for path in folder.rglob("*"):
        if not path.is_file():
            continue

        total_files += 1

        if not is_supported_source_file(path):
            skipped_files += 1
            continue

        try:
            source_document, extracted_text = ingest_local_file(path)
        except (OSError, UnicodeDecodeError, ValueError):
            skipped_files += 1
            continue

        source_documents.append(source_document)
        extracted_texts.append(extracted_text)
        processed_files += 1

    print(
        "Ingestion summary: "
        f"total={total_files} "
        f"processed={processed_files} "
        f"skipped={skipped_files}"
    )

    return source_documents, extracted_texts
