# Local Library Assistant

This project builds a local evidence database from text files and provides a CLI chat interface over that database.

The current shipping path is:

1. Build the SQLite evidence database from a folder of source files.
2. Query that database in retrieval-only mode with no LLM required.
3. Optionally use hybrid retrieval when an embedding backend is available.
4. Optionally synthesize a grounded answer from the retrieved evidence, with citations.

The code currently supports:

- ingestion from a broader set of local text and text-like files
- conservative text normalization
- structural segmentation into regions
- shaping those regions into `EvidenceUnit` records
- SQLite-backed lexical retrieval over `EvidenceUnit`s
- hybrid lexical + semantic retrieval over the same `EvidenceUnit`s when embeddings are available
- grounded answer synthesis over retrieved `EvidenceUnit`s when a local answer model is available

## What problem this project solves

The project turns a folder of local notes and documents into a queryable evidence store.

Instead of indexing raw chunks directly, it builds provenance-preserving `EvidenceUnit`s that carry:

- the source file path
- structural region metadata
- line and character ranges when available
- signals and context
- adjacency links to neighboring units

That lets the query layer retrieve structured evidence, expand nearby context, and produce answers that cite the source material.

## Current recommended workflow

Build the database:

```bash
python3 build_evidence_db.py /path/to/your/folder
```

Run the chat interface in retrieval-only mode:

```bash
python3 chat.py
```

Add hybrid retrieval when embeddings are available:

```bash
python3 chat.py --hybrid
```

Add answer synthesis when a local answer model is available:

```bash
python3 chat.py --hybrid --answer --query "your question here"
```

## High-level architecture

The project currently follows this data path:

```text
local file -> ExtractedText -> StructuralRegion -> EvidenceUnit -> SQLite index -> retrieval -> grounded answer
```

### 1. Ingestion and normalization

Implemented in `mvp_pipeline/ingest.py` and `mvp_pipeline/normalize.py`.

- The pipeline recursively discovers files for folder ingestion.
- Supported text and text-like files are processed.
- Files are read as UTF-8.
- Normalization is conservative:
  - BOM removal
  - newline normalization to LF
- Normalization produces an explicit segmentation contract with line-level source mapping.

### 2. Structural segmentation

Implemented in `mvp_pipeline/segment.py`.

Current structural segmentation supports:

- markdown ATX headings
- fenced code blocks
- paragraph-like blocks separated by blank lines
- plain text paragraph blocks

Segmentation is profile-based:

- `markdown`
- `plain_text`

### 3. Evidence shaping

Implemented in `mvp_pipeline/evidence.py`.

Structural regions are turned into `EvidenceUnit`s with:

- source file reference
- parent region reference
- line range
- char range when available
- previous/next adjacency
- signals
- confidence

The shaping code also includes type detection for several content patterns. Tests and code paths show units such as:

- `prose`
- `heading_section`
- `code`
- `command`
- `sql`
- `json_query`
- `table`
- `diagram`
- `mixed`

### 4. Canonical model

Implemented in `canonical_data_model.py`.

This file defines the canonical storage-agnostic object model used by the pipeline, including:

- `SourceDocument`
- `ExtractedText`
- `StructuralRegion`
- `EvidenceUnit`
- `DerivedArtifact`

### 5. SQLite indexing and lexical retrieval

Implemented in `query_retriever.py`.

SQLite is the canonical store for indexed `EvidenceUnit`s.

The retriever:

- stores `EvidenceUnit`s in `evidence_units`
- creates indexes on `source_file` and `type`
- uses SQLite FTS5 when available
- falls back to `LIKE`-based scoring if FTS5 is unavailable
- supports neighbor expansion through `prev_unit_id` and `next_unit_id`

### 6. Hybrid retrieval

Implemented in `hybrid_retriever.py`.

Hybrid retrieval:

- keeps SQLite as the source of truth
- adds an auxiliary semantic index keyed by `unit_id`
- embeds the same `EvidenceUnit`s already stored in SQLite
- merges lexical and semantic hits into a single ranked list
- hydrates final results back into canonical `EvidenceUnit` objects from SQLite

If semantic retrieval fails, `chat.py` falls back to lexical retrieval.

### 7. Grounded answer synthesis

Implemented in `grounded_answer_client.py`.

Grounded answers:

- use retrieved `EvidenceUnit`s as the grounding context
- call a local OpenAI-compatible LiteLLM proxy
- model is configurable via env vars (defaults to `llama3:8b`)
- return:
  - an answer
  - source citations
  - concise support metadata

If answer synthesis fails, `chat.py` falls back to showing retrieval results.

## Important files and folders

### Main user-facing entrypoints

- `build_evidence_db.py`
  Builds the SQLite evidence database from a folder.

- `chat.py`
  Queries the evidence database in lexical or hybrid mode and can synthesize grounded answers.

### Core runtime modules

- `canonical_data_model.py`
  Canonical object model and validation layer.

- `query_retriever.py`
  Step 12 SQLite-backed indexing and lexical retrieval.

- `hybrid_retriever.py`
  Step 14 hybrid lexical + semantic retrieval.

- `grounded_answer_client.py`
  Step 13 grounded answer synthesis over retrieved evidence.

- `mvp_pipeline/`
  Core ingestion and evidence-building pipeline.

### `mvp_pipeline/` contents

- `ingest.py`
  Local file ingestion across supported text-like formats.

- `normalize.py`
  Conservative normalization and segmentation handoff contract.

- `segment.py`
  Structural region segmentation.

- `evidence.py`
  Evidence-unit shaping and classification.

- `pipeline.py`
  End-to-end orchestration and SQLite write-through during ingestion.

- `derived_artifacts.py`
  Builds derived indexes such as command, query, and link indexes.

- `export_markdown.py`
  Markdown audit export helpers.

### Supporting folders

- `docs/`
  Design and architecture notes. These are documentation artifacts, not the runtime system.

- `tests/`
  `unittest`-based tests for the pipeline, derived artifacts, and export layer.

- `examples/`
  Small example scripts for inspecting pipeline stages.

## Security & privacy

This tool is designed to run locally over your files, but you are responsible for what you ingest and where you send data.

- **Do not commit your index**: the SQLite DB (default `evidence_units.db`) can contain sensitive or proprietary text from the files you ingested.
- **Model boundary**: `--hybrid` and `--answer` send text to an OpenAI-compatible endpoint (the code assumes a local LiteLLM proxy at `http://localhost:4000`). Treat that endpoint as a trust boundary.
- **Secrets & PII**: avoid ingesting secrets (API keys, tokens) and PII unless you have an explicit need; consider adding a redaction step if you plan to use this on broad folders.

## Setup

### Python

The core retrieval-only workflow is Python standard library only. Optional features (PDF extraction, hybrid semantic retrieval, grounded answers / rewrites / reranking) may require additional dependencies and/or a local OpenAI-compatible proxy.

Optional capabilities:
- **PDF extraction** works only if you have either `pypdf`/`PyPDF2` installed or a `pdftotext` binary available.
- **Hybrid retrieval / grounded answers** require a local OpenAI-compatible proxy (e.g. LiteLLM).

The main scripts are intended to be run with `python3`.

Dependencies:
- Retrieval-only mode: stdlib-only (no `pip install` required).
- Optional features: install optional deps via:

```bash
python3 -m pip install -r requirements.txt
```

### Retrieval-only operation

The project works in retrieval-only mode without any model stack running.

This path requires:

- a built SQLite evidence database
- Python 3

It does not require:

- embeddings
- a local answer model
- a running LiteLLM proxy

### Local model / proxy requirements

Hybrid retrieval and grounded answer synthesis require a local OpenAI-compatible LiteLLM proxy.

The code currently expects:

- API base: `http://localhost:4000`
- chat completions endpoint:
  - `/v1/chat/completions`
- embeddings endpoint:
  - `/v1/embeddings`

The code currently uses:

- answer model: configurable via `LLA_ANSWER_MODEL` (defaults to `llama3:8b`)
- rewrite model: configurable via `LLA_REWRITE_MODEL` (defaults to `llama3:8b`)
- rerank model: configurable via `LLA_RERANK_MODEL` (defaults to `llama3:8b`)
- embedding model default: `text-embedding-ada-002` (configurable via `STEP14_EMBED_MODEL`)

Relevant environment variables used by the code:

- `LITELLM_PROXY_API_KEY`
  API key used for your local OpenAI-compatible proxy (LiteLLM). Defaults to `local-dev-key` if unset.

- `STEP14_EMBED_MODEL`
  Overrides the embedding model used by `hybrid_retriever.py`.

If you do not have a working local proxy:

- lexical retrieval still works
- hybrid retrieval falls back to lexical retrieval
- answer mode falls back to retrieval-only output

## Supported file types

The current ingestion path supports these file types directly:

- plain text and markup:
  - `.txt`
  - `.md`
  - `.markdown`
  - `.rst`
  - `.log`
- office and document formats:
  - `.docx`
  - `.pdf`
- data, config, and query files:
  - `.sql`
  - `.json`
  - `.yaml`
  - `.yml`
  - `.toml`
  - `.env`
  - `.ini`
  - `.conf`
  - `.properties`
  - `.xml`
  - `.html`
  - `.css`
  - `.scss`
- code and shell files:
  - `.py`
  - `.js`
  - `.ts`
  - `.tsx`
  - `.jsx`
  - `.java`
  - `.kt`
  - `.go`
  - `.rs`
  - `.sh`
  - `.bash`
  - `.zsh`
  - `.c`
  - `.cpp`
  - `.h`
  - `.hpp`

Current extraction behavior from code:

- text-like files are decoded as UTF-8
- DOCX files are extracted in-process from `word/document.xml`
- PDF files require an available extraction backend
  - the code tries `pypdf`
  - then `PyPDF2`
  - then a local `pdftotext` binary
  - if none are available, the PDF file is skipped and the rest of the corpus continues indexing

## Building the evidence database

Main command:

```bash
python3 build_evidence_db.py <input_folder>
```

Optional custom database path:

```bash
python3 build_evidence_db.py <input_folder> --db-path /path/to/evidence_units.db
```

### What this command does

`build_evidence_db.py` calls `run_folder_pipeline(...)` from `mvp_pipeline/pipeline.py`.

That pipeline:

- recursively scans the input folder
- processes supported text and text-like files
- skips unsupported files
- builds `EvidenceUnit`s for each supported file
- writes all indexed units into SQLite using replace mode

### Output

The script prints:

- input folder
- database path
- files processed
- evidence units indexed

## Running the chat/query interface

Main command:

```bash
python3 chat.py
```

This starts an interactive REPL.

Startup output includes:

- database path
- indexed evidence unit count
- current mode

Interactive commands supported by the code:

- `:help`
- `:stats`
- `:quit`
- `exit`
- `quit`

### One-shot query mode

```bash
python3 chat.py --query "how do I build evidence units from a file"
```

### Lexical retrieval only

```bash
python3 chat.py --query "docker"
```

### Hybrid retrieval

```bash
python3 chat.py --hybrid --query "docker"
```

### Hybrid retrieval with grounded answer synthesis

```bash
python3 chat.py --hybrid --answer --query "docker"
```

### JSON output

```bash
python3 chat.py --hybrid --answer --query "docker" --json
```

## CLI flags

### `build_evidence_db.py`

From the current code:

- `input_folder`
  Required positional argument. Folder containing source files to ingest.

- `--db-path DB_PATH`
  SQLite database path to build. Defaults to `evidence_units.db`.

### `chat.py`

From the current code:

- `--db-path DB_PATH`
  SQLite database path for the evidence index.

- `--query QUERY`
  One-shot query. If omitted, interactive mode starts.

- `--top-k TOP_K`
  Maximum number of hits to return. Default: `5`.

- `--neighbors NEIGHBORS`
  Number of previous/next neighbors to expand per hit. Default: `1`.

- `--json`
  Print machine-readable JSON instead of formatted text.

- `--answer`
  Synthesize a grounded answer from retrieved `EvidenceUnit`s.

- `--hybrid`
  Use hybrid lexical + semantic retrieval before answer synthesis.

## Intended user workflow

Based on the actual code, the intended shipping workflow is:

1. Put supported local text and text-like files into a folder.
2. Build the SQLite database with `build_evidence_db.py`.
3. Query that database with `chat.py` in retrieval-only mode.
4. Add `--hybrid` if embeddings are available.
5. Add `--answer` if a local answer model is available.

## Output behavior

### Retrieval mode

Lexical or hybrid retrieval returns structured evidence hits.

Text output includes:

- query
- hits
- score
- unit id
- type
- source file
- region metadata
- signals
- original text
- optional neighbors

### Answer mode

Answer mode prints:

- `Answer`
- `Sources`
- optional `Support`

Sources are formatted as:

- filename + line range when line numbers exist
- otherwise a fallback region summary from the indexed region metadata

## Auxiliary and non-shipping scripts

These files exist in the codebase, but they are not part of the current shipping CLI path:

- `export_folder_markdown_audit.py`
  Exports a corpus-level markdown audit view from pipeline output.

- `mvp_pipeline/export_markdown.py`
  Markdown export helpers used by the audit export flow.

- `query_rewrite.py`
  Experimental extension for rewrite-aware retrieval.
  It can generate query variants (heuristic + optional LLM rewrites), run retrieval for each variant, then merge/rerank the combined hit list.
  Not wired into `chat.py` by default; you’d integrate it by calling `retrieve_with_rewrites(...)` (or `QueryRewriteClient`) in the query path before printing results.

- `candidate_reranker.py`
  Experimental extension for second-stage LLM reranking.
  Given the top-N retrieved candidates, it asks a local OpenAI-compatible endpoint to return a best-first ordering of `unit_id`s.
  Not wired into `chat.py` by default; you’d integrate it after retrieval (lexical or hybrid) and before neighbor expansion / answer synthesis.

- `examples/`
  Development and inspection scripts for intermediate pipeline stages.

## Troubleshooting

### `chat.py` says the index is empty

`chat.py` prints startup status from the active database path. If `Indexed EvidenceUnits: 0` appears, build the DB first:

```bash
python3 build_evidence_db.py <folder>
```

Also make sure `chat.py` is reading the same `--db-path` you built.

### Hybrid retrieval falls back to lexical retrieval

This is expected if semantic retrieval is unavailable.

From the code:

- `chat.py` catches hybrid retrieval failures
- it prints a fallback status message
- it reruns lexical retrieval instead

Common causes based on the code path:

- local LiteLLM proxy not running at `http://localhost:4000`
- embeddings endpoint unavailable
- invalid API key for the local proxy

### Answer mode falls back to retrieval-only output

This is also expected behavior in the current code.

If grounded answer synthesis fails:

- `chat.py` prints a status message to stderr
- retrieval results are shown instead of an answer

### PDF files are skipped during indexing

This is expected when no PDF extraction backend is available.

From the current code, PDF extraction is optional and tries:

- `pypdf`
- `PyPDF2`
- `pdftotext`

If none of those are available, the PDF file is skipped and indexing continues for the rest of the folder.

### No results

If the DB is populated but a query returns nothing, `chat.py` prints:

- `Status: no grounded evidence found for that query.`

Try:

- broader search terms
- lexical mode first, to inspect the raw retrieved evidence
- hybrid mode if the local embedding path is available

### Unsupported files are skipped

Unsupported files are skipped during folder ingestion. Supported files that cannot be read are also skipped, and indexing continues for the rest of the folder.

### Non-UTF-8 text

The ingestion code decodes files as UTF-8. Files that cannot be decoded as UTF-8 are skipped or fail ingestion.

## Known limitations

These are clear from the current code:

- Ingestion for text-like files is UTF-8 only.
- PDF support depends on an optional extraction backend and may not be available on every machine.
- DOCX extraction is text-only and paragraph-oriented.
- Hybrid retrieval depends on a local embeddings endpoint and model.
- Grounded answer synthesis depends on a local chat-completions endpoint and model.
- The answer layer requires JSON-shaped model output internally and may fall back to retrieval-only output when synthesis fails.
- Experimental rewrite/rerank modules exist in the repository but are not part of the shipping CLI.
- The semantic index is auxiliary; SQLite remains the canonical store of record.

## Verification and tests

Run unit tests:

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -p "test_*.py"
```

The repository contains `unittest` test files under `tests/`, including:

- `tests/test_mvp_pipeline.py`
- `tests/test_derived_artifacts.py`
- `tests/test_export_markdown.py`
- `tests/test_canonical_data_model.py`

These tests cover major pieces of the pipeline and supporting utilities, but the README does not assume any specific test runner command beyond the presence of these `unittest` modules.
