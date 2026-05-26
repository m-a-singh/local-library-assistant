"""Local library assistant CLI over the SQLite EvidenceUnit store.

Recommended frozen V1 workflow:
1. ``python3 build_evidence_db.py <folder>``
2. ``python3 chat.py`` for retrieval-only use
3. optionally add ``--hybrid`` when embeddings are available
4. optionally add ``--answer`` when a local answer model is available
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from query_retriever import EvidenceUnit, EvidenceUnitIndex, RetrievalHit, RetrievalResult


def evidence_unit_to_dict(unit: EvidenceUnit) -> dict[str, Any]:
    """Convert an EvidenceUnit into a JSON-safe dictionary."""

    return {
        "unit_id": unit.unit_id,
        "type": unit.type,
        "source_file": unit.source_file,
        "region": unit.region,
        "signals": unit.signals,
        "text": unit.text,
        "structural_context": unit.structural_context,
        "prev_unit_id": unit.prev_unit_id,
        "next_unit_id": unit.next_unit_id,
    }


def retrieval_hit_to_dict(hit: RetrievalHit) -> dict[str, Any]:
    """Convert a RetrievalHit into a JSON-safe dictionary."""

    return {
        "rank": hit.rank,
        "score": hit.score,
        "unit": evidence_unit_to_dict(hit.unit),
    }


def retrieval_result_to_dict(result: RetrievalResult) -> dict[str, Any]:
    """Convert a RetrievalResult into a JSON-safe dictionary."""

    return {
        "query": result.query,
        "hits": [retrieval_hit_to_dict(hit) for hit in result.hits],
        "neighbors": {
            anchor_unit_id: [evidence_unit_to_dict(unit) for unit in units]
            for anchor_unit_id, units in result.neighbors.items()
        },
    }


def get_index_stats(index: EvidenceUnitIndex) -> dict[str, int]:
    """Return simple row-count diagnostics for the current SQLite index."""

    row = index._conn.execute(
        "SELECT COUNT(*) AS unit_count FROM evidence_units"
    ).fetchone()
    unit_count = int(row["unit_count"]) if row is not None else 0
    return {"unit_count": unit_count}


def print_startup_status(index: EvidenceUnitIndex, db_path: str) -> None:
    """Print concise startup diagnostics for the active database."""

    resolved_path = db_path
    if db_path != ":memory:":
        resolved_path = os.path.abspath(db_path)

    stats = get_index_stats(index)
    print("Local library assistant")
    print(f"DB: {resolved_path}")
    print(f"Indexed EvidenceUnits: {stats['unit_count']}")
    if stats["unit_count"] == 0:
        print("Status: empty index. Run `python3 build_evidence_db.py <folder>` first.")


def _indent_block(text: str, prefix: str = "    ") -> str:
    """Indent a block of text for display."""

    if not text:
        return prefix + "<empty>"
    return "\n".join(f"{prefix}{line}" for line in text.splitlines() or [""])


def _format_metadata(value: Any) -> str:
    """Format structured metadata as readable JSON."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def format_retrieval_result_text(result: RetrievalResult) -> str:
    """Render a RetrievalResult as human-readable text."""

    lines: list[str] = [f"Query: {result.query}"]

    if not result.hits:
        lines.append("No results.")
        return "\n".join(lines)

    lines.append(f"Hits: {len(result.hits)}")
    lines.append("")

    for hit in result.hits:
        lines.append(f"[{hit.rank}]")
        lines.append(f"  score: {hit.score:.6f}")
        lines.append(f"  unit_id: {hit.unit.unit_id}")
        lines.append(f"  type: {hit.unit.type}")
        lines.append(f"  source_file: {hit.unit.source_file}")
        lines.append(f"  region: {_format_metadata(hit.unit.region)}")
        lines.append(f"  signals: {_format_metadata(hit.unit.signals)}")
        lines.append("  text:")
        lines.append(_indent_block(hit.unit.text))
        lines.append("")

    if result.neighbors:
        lines.append("Neighbors:")
        for anchor_unit_id, neighbors in result.neighbors.items():
            lines.append(f"  anchor: {anchor_unit_id}")
            if not neighbors:
                lines.append("    <none>")
                continue
            for index, unit in enumerate(neighbors, start=1):
                lines.append(f"    [{index}] {unit.unit_id} ({unit.type})")
                lines.append(f"      source_file: {unit.source_file}")
                lines.append("      text:")
                lines.append(_indent_block(unit.text, prefix="        "))
    return "\n".join(lines).rstrip()


def _load_grounded_answer_support() -> tuple[Any, Any, Any, Any]:
    """Load grounded answer support lazily."""

    from grounded_answer_client import (
        GroundedAnswerClient,
        GroundedAnswerServiceError,
        format_grounded_answer_text,
        grounded_answer_to_dict,
    )

    return GroundedAnswerClient, GroundedAnswerServiceError, format_grounded_answer_text, grounded_answer_to_dict


def _load_hybrid_support() -> tuple[Any, Any, Any]:
    """Load hybrid retrieval support lazily."""

    from hybrid_retriever import (
        HybridRetriever,
        format_hybrid_retrieval_result_text,
        hybrid_retrieval_result_to_dict,
    )

    return HybridRetriever, format_hybrid_retrieval_result_text, hybrid_retrieval_result_to_dict


def _retrieve_with_fallback(
    index: EvidenceUnitIndex,
    db_path: str,
    query: str,
    top_k: int,
    neighbors: int,
    hybrid_mode: bool,
) -> tuple[RetrievalResult, dict[str, Any], Any | None]:
    """Run the shipping retrieval path, with lexical fallback if hybrid fails."""

    if not hybrid_mode:
        lexical_result = index.retrieve(query, top_k=top_k, expand_neighbors=neighbors)
        return lexical_result, {"retrieval_mode": "lexical"}, None

    HybridRetriever, _format_hybrid_retrieval_result_text, _hybrid_retrieval_result_to_dict = (
        _load_hybrid_support()
    )
    try:
        hybrid_retriever = HybridRetriever(db_path=db_path, index=index)
        hybrid_result = hybrid_retriever.retrieve(
            query,
            top_k=top_k,
            expand_neighbors=neighbors,
        )
    except Exception as exc:
        print("Status: hybrid retrieval unavailable; falling back to lexical retrieval.", file=sys.stderr)
        lexical_result = index.retrieve(query, top_k=top_k, expand_neighbors=neighbors)
        return lexical_result, {
            "retrieval_mode": "lexical",
            "fallback_reason": f"semantic_unavailable:{type(exc).__name__}",
        }, None

    retrieval_result = RetrievalResult(
        query=query,
        hits=hybrid_result.hits,
        neighbors=hybrid_result.neighbors,
    )
    return retrieval_result, {
        "retrieval_mode": "hybrid",
        "lexical_hit_count": hybrid_result.lexical_hit_count,
        "semantic_hit_count": hybrid_result.semantic_hit_count,
        "notes": list(hybrid_result.retrieval_summary.get("notes", [])),
    }, hybrid_result


def _print_no_results_hint(index: EvidenceUnitIndex) -> None:
    """Print a concise hint when a query returns no grounded evidence."""

    stats = get_index_stats(index)
    if stats["unit_count"] == 0:
        print("Status: the active database is empty. Run `python3 build_evidence_db.py <folder>` first.")
    else:
        print("Status: no grounded evidence found for that query.")


def _print_retrieval_output(
    retrieval_result: RetrievalResult,
    *,
    hybrid_result: Any | None,
    as_json: bool,
) -> None:
    """Print retrieval output in either hybrid or lexical form."""

    if hybrid_result is not None:
        _HybridRetriever, format_hybrid_retrieval_result_text, hybrid_retrieval_result_to_dict = (
            _load_hybrid_support()
        )
        if as_json:
            print(json.dumps(hybrid_retrieval_result_to_dict(hybrid_result), ensure_ascii=False, indent=2))
            return
        print(format_hybrid_retrieval_result_text(hybrid_result))
        return

    if as_json:
        print(json.dumps(retrieval_result_to_dict(retrieval_result), ensure_ascii=False, indent=2))
        return
    print(format_retrieval_result_text(retrieval_result))


def run_query(
    index: EvidenceUnitIndex,
    query: str,
    top_k: int,
    neighbors: int,
    as_json: bool,
    answer_mode: bool,
    hybrid_mode: bool,
    db_path: str,
) -> None:
    """Execute one retrieval query and print the result."""

    retrieval_result, retrieval_summary, hybrid_result = _retrieve_with_fallback(
        index,
        db_path,
        query,
        top_k,
        neighbors,
        hybrid_mode,
    )

    if answer_mode:
        try:
            GroundedAnswerClient, GroundedAnswerServiceError, format_grounded_answer_text, grounded_answer_to_dict = (
                _load_grounded_answer_support()
            )
            answer_client = GroundedAnswerClient(db_path=db_path, index=index)
            try:
                grounded_answer = answer_client.answer_from_retrieval_result(
                    query,
                    retrieval_result,
                    top_k=top_k,
                    expand_neighbors=neighbors,
                    extra_summary=retrieval_summary,
                )
            finally:
                answer_client.close()
        except GroundedAnswerServiceError:
            print("Status: answer synthesis unavailable; showing retrieved evidence instead.", file=sys.stderr)
        except Exception as exc:
            print(f"Status: answer synthesis failed; showing retrieved evidence instead ({type(exc).__name__}).", file=sys.stderr)
        else:
            if as_json:
                print(json.dumps(grounded_answer_to_dict(grounded_answer), ensure_ascii=False, indent=2))
                return
            print(format_grounded_answer_text(grounded_answer))
            return

    _print_retrieval_output(
        retrieval_result,
        hybrid_result=hybrid_result,
        as_json=as_json,
    )
    if not retrieval_result.hits:
        _print_no_results_hint(index)


def run_repl(
    index: EvidenceUnitIndex,
    db_path: str,
    top_k: int,
    neighbors: int,
    as_json: bool,
    answer_mode: bool,
    hybrid_mode: bool,
) -> None:
    """Run an interactive retrieval loop over the SQLite store."""

    print_startup_status(index, db_path)
    if answer_mode and hybrid_mode:
        print("Mode: hybrid retrieval with grounded answers")
    elif answer_mode:
        print("Mode: lexical retrieval with grounded answers")
    elif hybrid_mode:
        print("Mode: hybrid retrieval")
    else:
        print("Mode: lexical retrieval")
    print("Type a query and press Enter. Commands: :help, :stats, :quit")

    while True:
        try:
            raw_query = input("query> ")
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            break

        query = raw_query.strip()
        if not query:
            continue
        if query in {"exit", "quit", ":quit"}:
            break
        if query == ":help":
            if answer_mode and hybrid_mode:
                print("Enter a query to run hybrid retrieval and synthesize a grounded answer.")
            elif answer_mode:
                print("Enter a query to run lexical retrieval and synthesize a grounded answer.")
            elif hybrid_mode:
                print("Enter a query to run hybrid lexical + semantic retrieval.")
            else:
                print("Enter a query to run lexical retrieval.")
            print("Commands: :help, :stats, :quit, exit, quit")
            continue
        if query == ":stats":
            print_startup_status(index, db_path)
            continue

        run_query(
            index,
            query,
            top_k=top_k,
            neighbors=neighbors,
            as_json=as_json,
            answer_mode=answer_mode,
            hybrid_mode=hybrid_mode,
            db_path=db_path,
        )
        if not as_json:
            print("")


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Query the local EvidenceUnit database. "
            "Default is retrieval-only; add --hybrid for semantic retrieval and --answer for grounded answers."
        ),
    )
    parser.add_argument(
        "--db-path",
        default="evidence_units.db",
        help="SQLite database path for the EvidenceUnit index.",
    )
    parser.add_argument(
        "--query",
        help="One-shot query. If omitted, interactive mode starts.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Maximum number of hits to return.",
    )
    parser.add_argument(
        "--neighbors",
        type=int,
        default=1,
        help="Number of previous/next neighbors to expand per hit.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of formatted text.",
    )
    parser.add_argument(
        "--answer",
        action="store_true",
        help="Synthesize a grounded answer from retrieved EvidenceUnits.",
    )
    parser.add_argument(
        "--hybrid",
        action="store_true",
        help="Use hybrid lexical + semantic retrieval before answer synthesis.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI or interactive retrieval interface."""

    parser = build_parser()
    args = parser.parse_args(argv)

    index = EvidenceUnitIndex(db_path=args.db_path)
    try:
        if args.query is not None:
            run_query(
                index,
                query=args.query,
                top_k=args.top_k,
                neighbors=args.neighbors,
                as_json=args.json,
                answer_mode=args.answer,
                hybrid_mode=args.hybrid,
                db_path=args.db_path,
            )
            return 0

        run_repl(
            index,
            db_path=args.db_path,
            top_k=args.top_k,
            neighbors=args.neighbors,
            as_json=args.json,
            answer_mode=args.answer,
            hybrid_mode=args.hybrid,
        )
        return 0
    finally:
        index.close()


if __name__ == "__main__":
    raise SystemExit(main())
