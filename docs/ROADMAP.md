# Roadmap

This roadmap is a lightweight plan for iterating on **Local Library Assistant** after the current MVP.

For the project mission and invariants, see `FOUNDATION.md`. For current behavior and usage, see `../README.md`.

## Current status (implemented)

- Folder ingestion → normalization → segmentation → `EvidenceUnit` shaping (`mvp_pipeline/`)
- SQLite index as the source of truth (`evidence_units.db`)
- Lexical retrieval with neighbor expansion (`query_retriever.py`)
- Optional hybrid retrieval (lexical + embeddings) with clean fallback (`hybrid_retriever.py`)
- Optional grounded answer synthesis with citations via a local OpenAI-compatible endpoint (`grounded_answer_client.py`)
- Unit tests covering segmentation and evidence shaping edge cases (`tests/`)

## Next milestones

### M1 — Shipping hygiene
- Add/verify `.gitignore` for generated artifacts (`evidence_units.db`, `__pycache__/`, etc.)
- Ensure docs don’t contradict code defaults
- Provide a small sample corpus for demos (synthetic, no real notes)

### M2 — Privacy & safety hardening (recommended)
- Add an opt-in redaction pass (secrets/PII patterns) before:
  - writing text into SQLite
  - sending context to the model endpoint
- Make the “model boundary” explicit in CLI help (what data is sent, where)

### M3 — Retrieval quality improvements
- Wire in reranking behind a CLI flag (`candidate_reranker.py`)
- Wire in query rewrite behind a CLI flag (`query_rewrite.py`)
- Add a tiny regression suite: saved queries → expected citations/hits

### M4 — Incremental indexing
- Detect file changes via content hash and only re-index deltas
- Keep EvidenceUnit identity stable when content is unchanged

### M5 — Better PDF/doc handling (optional)
- Improve PDF extraction fidelity where backends exist
- Preserve stronger location metadata when available

## Notes / open questions

- EvidenceUnit identity stability vs evolution as heuristics improve
- Attachment model: when to attach short prose to a command/SQL unit
- Ambiguity/trust schema: how it affects ranking and citation behavior
- Whether command output should be treated as primary evidence or attached context
