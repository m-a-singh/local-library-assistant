# Implementation Plan

This document is intentionally short and practical.

- If you want the system goals and invariants, read `FOUNDATION.md`.
- If you want the ingestion/normalization contract, read `Ingestion_And_Normalization_Architecture.md`.

## Status (as of current code)

The MVP "shipping path" described in `README.md` is implemented:

- `build_evidence_db.py` builds a SQLite EvidenceUnit index from a folder
- `mvp_pipeline/` performs ingestion → normalization → segmentation → evidence shaping
- `query_retriever.py` provides SQLite-backed lexical retrieval (FTS5 when available)
- `hybrid_retriever.py` optionally adds embeddings-based semantic search and merges results
- `grounded_answer_client.py` optionally synthesizes a grounded answer with citations via a local OpenAI-compatible proxy

## What’s done

Core pipeline and contracts:
- Canonical model present (`canonical_data_model.py`): `SourceDocument`, `ExtractedText`, `StructuralRegion`, `EvidenceUnit`, `DerivedArtifact`
- Conservative normalization (BOM removal, newline normalization) with explicit segmentation handoff
- Structural segmentation profiles for markdown and plain text
- EvidenceUnit shaping with provenance and adjacency links

Indexing and retrieval:
- SQLite store of record for evidence units
- Lexical retrieval with neighbor expansion
- Hybrid retrieval path that falls back cleanly to lexical when embeddings are unavailable

Quality controls:
- `unittest` coverage for segmentation/evidence shaping edge cases (tables, fenced code/mermaid, commands, SQL, deterministic IDs)

## What’s next (incremental, portfolio-friendly)

### 1) Shipping hygiene
- Add `.gitignore` rules for generated artifacts (especially `evidence_units.db` and `__pycache__/`)
- Ensure docs reflect the current behavior and defaults

### 2) Privacy/security hardening (optional but recommended)
- Add an opt-in redaction pass (secrets/PII patterns) before writing to SQLite and before sending context to the model endpoint
- Add a `--no-send` / retrieval-only guardrail when running in environments where model calls are not allowed

### 3) Retrieval quality improvements
- Wire in reranking (`candidate_reranker.py`) behind a CLI flag once stable
- Add query rewrite (`query_rewrite.py`) behind a CLI flag once stable
- Add lightweight evaluation harness: saved queries + expected citations for regression checks

### 4) Incremental indexing
- Detect changed files and only re-index deltas (content-hash based)

## Open decisions

These are still relevant design questions (even if the MVP works):

- Evidence unit identity strategy across pipeline changes (stability vs evolution)
- Attachment representation (e.g., short explanatory prose attached above a command/SQL)
- Ambiguity/trust schema (what gets marked and how it impacts retrieval/ranking)
- How to treat command output: primary evidence vs attached context
