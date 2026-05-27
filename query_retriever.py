"""Step 12 EvidenceUnit index and retrieval layer backed by SQLite."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvidenceUnit:
    """Structured evidence unit stored and retrieved by the Step 12 layer."""

    unit_id: str
    type: str
    source_file: str
    region: dict[str, Any]
    signals: dict[str, Any]
    text: str
    structural_context: dict[str, Any] = field(default_factory=dict)
    prev_unit_id: str | None = None
    next_unit_id: str | None = None


@dataclass
class RetrievalHit:
    """One ranked retrieval match."""

    unit: EvidenceUnit
    score: float
    rank: int


@dataclass
class RetrievalResult:
    """Structured retrieval response with optional neighbor expansion."""

    query: str
    hits: list[RetrievalHit]
    neighbors: dict[str, list[EvidenceUnit]]


class EvidenceUnitIndex:
    """SQLite-backed EvidenceUnit store used by both ingestion and retrieval.

    Ingestion writes EvidenceUnits into the SQLite store through
    ``index_units(...)``, ``replace_index(...)``, or ``ingest_units(...)``.
    Retrieval reads from the same store through ``retrieve(...)``.
    """

    _TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")

    def __init__(self, db_path: str = ":memory:"):
        """Initialize the index and create storage tables."""

        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._fts_enabled = False
        self._initialize_schema()

    def index_units(self, units: list[EvidenceUnit]) -> None:
        """Upsert EvidenceUnits into the SQLite store used by retrieval."""

        serialized_units = [self._serialize_unit(unit) for unit in units]
        if not serialized_units:
            return

        with self._conn:
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO evidence_units (
                    unit_id,
                    type,
                    source_file,
                    region_json,
                    signals_json,
                    text,
                    structural_context_json,
                    prev_unit_id,
                    next_unit_id
                ) VALUES (
                    :unit_id,
                    :type,
                    :source_file,
                    :region_json,
                    :signals_json,
                    :text,
                    :structural_context_json,
                    :prev_unit_id,
                    :next_unit_id
                )
                """,
                serialized_units,
            )

            if self._fts_enabled:
                self._conn.executemany(
                    "DELETE FROM evidence_units_fts WHERE unit_id = ?",
                    [(item["unit_id"],) for item in serialized_units],
                )
                self._conn.executemany(
                    "INSERT INTO evidence_units_fts (unit_id, text) VALUES (?, ?)",
                    [(item["unit_id"], item["text"]) for item in serialized_units],
                )

    def replace_index(self, units: list[EvidenceUnit]) -> None:
        """Replace the full SQLite index contents during rebuild ingestion."""

        serialized_units = [self._serialize_unit(unit) for unit in units]

        with self._conn:
            self._conn.execute("DELETE FROM evidence_units")
            if self._fts_enabled:
                self._conn.execute("DELETE FROM evidence_units_fts")

            if not serialized_units:
                return

            self._conn.executemany(
                """
                INSERT OR REPLACE INTO evidence_units (
                    unit_id,
                    type,
                    source_file,
                    region_json,
                    signals_json,
                    text,
                    structural_context_json,
                    prev_unit_id,
                    next_unit_id
                ) VALUES (
                    :unit_id,
                    :type,
                    :source_file,
                    :region_json,
                    :signals_json,
                    :text,
                    :structural_context_json,
                    :prev_unit_id,
                    :next_unit_id
                )
                """,
                serialized_units,
            )

            if self._fts_enabled:
                self._conn.executemany(
                    "INSERT INTO evidence_units_fts (unit_id, text) VALUES (?, ?)",
                    [(item["unit_id"], item["text"]) for item in serialized_units],
                )

    def ingest_units(self, units: list[EvidenceUnit], mode: str = "upsert") -> None:
        """Write ingested EvidenceUnits into SQLite for later retrieval."""

        if mode == "replace":
            self.replace_index(units)
            return
        if mode == "upsert":
            self.index_units(units)
            return
        raise ValueError("mode must be 'replace' or 'upsert'")

    def retrieve(self, query: str, top_k: int = 5, expand_neighbors: int = 0) -> RetrievalResult:
        """Read ranked EvidenceUnits from the SQLite store plus optional neighbors."""

        normalized_query = query.strip()
        if not normalized_query or top_k <= 0:
            return RetrievalResult(query=query, hits=[], neighbors={})

        ranked_ids = self._search_unit_ids(normalized_query, top_k)
        units_by_id = {unit.unit_id: unit for unit in self._fetch_by_ids([unit_id for unit_id, _ in ranked_ids])}

        hits: list[RetrievalHit] = []
        for rank, (unit_id, score) in enumerate(ranked_ids, start=1):
            unit = units_by_id.get(unit_id)
            if unit is None:
                continue
            hits.append(RetrievalHit(unit=unit, score=score, rank=rank))

        neighbors: dict[str, list[EvidenceUnit]] = {}
        if expand_neighbors > 0:
            for hit in hits:
                neighbors[hit.unit.unit_id] = self._expand_neighbors(hit.unit, expand_neighbors)

        return RetrievalResult(query=query, hits=hits, neighbors=neighbors)

    def close(self) -> None:
        """Close the SQLite connection used for ingestion and retrieval."""

        self._conn.close()

    def _initialize_schema(self) -> None:
        """Create the base table, indexes, and optional FTS table."""

        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS evidence_units (
                    unit_id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    source_file TEXT NOT NULL,
                    region_json TEXT NOT NULL,
                    signals_json TEXT NOT NULL,
                    text TEXT NOT NULL,
                    structural_context_json TEXT NOT NULL,
                    prev_unit_id TEXT,
                    next_unit_id TEXT
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_evidence_units_source_file ON evidence_units (source_file)"
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_units_type ON evidence_units (type)")

            try:
                self._conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS evidence_units_fts
                    USING fts5(unit_id UNINDEXED, text)
                    """
                )
            except sqlite3.OperationalError:
                self._fts_enabled = False
            else:
                self._fts_enabled = True

    def _serialize_unit(self, unit: EvidenceUnit) -> dict[str, str | None]:
        """Convert an EvidenceUnit into SQLite-ready column values."""

        return {
            "unit_id": unit.unit_id,
            "type": unit.type,
            "source_file": unit.source_file,
            "region_json": json.dumps(unit.region, ensure_ascii=False, sort_keys=True),
            "signals_json": json.dumps(unit.signals, ensure_ascii=False, sort_keys=True),
            "text": unit.text,
            "structural_context_json": json.dumps(
                unit.structural_context,
                ensure_ascii=False,
                sort_keys=True,
            ),
            "prev_unit_id": unit.prev_unit_id,
            "next_unit_id": unit.next_unit_id,
        }

    def _deserialize_unit(self, row: sqlite3.Row) -> EvidenceUnit:
        """Convert a SQLite row into an EvidenceUnit object."""

        return EvidenceUnit(
            unit_id=row["unit_id"],
            type=row["type"],
            source_file=row["source_file"],
            region=json.loads(row["region_json"]),
            signals=json.loads(row["signals_json"]),
            text=row["text"],
            structural_context=json.loads(row["structural_context_json"]),
            prev_unit_id=row["prev_unit_id"],
            next_unit_id=row["next_unit_id"],
        )

    def _fetch_by_ids(self, unit_ids: list[str]) -> list[EvidenceUnit]:
        """Load full EvidenceUnit rows and preserve the requested order."""

        if not unit_ids:
            return []

        placeholders = ", ".join("?" for _ in unit_ids)
        rows = self._conn.execute(
            f"""
            SELECT
                unit_id,
                type,
                source_file,
                region_json,
                signals_json,
                text,
                structural_context_json,
                prev_unit_id,
                next_unit_id
            FROM evidence_units
            WHERE unit_id IN ({placeholders})
            """,
            unit_ids,
        ).fetchall()

        by_id = {row["unit_id"]: self._deserialize_unit(row) for row in rows}
        return [by_id[unit_id] for unit_id in unit_ids if unit_id in by_id]

    def _expand_neighbors(self, unit: EvidenceUnit, steps: int) -> list[EvidenceUnit]:
        """Expand neighbor units around a hit using prev/next links."""

        seen_ids = {unit.unit_id}
        previous_units: list[EvidenceUnit] = []
        next_units: list[EvidenceUnit] = []

        current_prev_id = unit.prev_unit_id
        for _ in range(steps):
            if current_prev_id is None or current_prev_id in seen_ids:
                break
            fetched = self._fetch_by_ids([current_prev_id])
            if not fetched:
                break
            prev_unit = fetched[0]
            previous_units.append(prev_unit)
            seen_ids.add(prev_unit.unit_id)
            current_prev_id = prev_unit.prev_unit_id

        current_next_id = unit.next_unit_id
        for _ in range(steps):
            if current_next_id is None or current_next_id in seen_ids:
                break
            fetched = self._fetch_by_ids([current_next_id])
            if not fetched:
                break
            next_unit = fetched[0]
            next_units.append(next_unit)
            seen_ids.add(next_unit.unit_id)
            current_next_id = next_unit.next_unit_id

        return list(reversed(previous_units)) + next_units

    def _search_unit_ids(self, query: str, top_k: int) -> list[tuple[str, float]]:
        """Search ranked unit ids using FTS5 when available, else LIKE scoring."""

        if self._fts_enabled:
            try:
                return self._search_with_fts(query, top_k)
            except sqlite3.OperationalError:
                return self._search_with_like(query, top_k)
        return self._search_with_like(query, top_k)

    def _search_with_fts(self, query: str, top_k: int) -> list[tuple[str, float]]:
        """Search using SQLite FTS5 and return (unit_id, score) pairs."""

        tokens = self._tokenize_query(query)
        if not tokens:
            return []

        fts_query = " ".join(f'"{token}"' for token in tokens)
        rows = self._conn.execute(
            """
            SELECT unit_id, bm25(evidence_units_fts) AS raw_score
            FROM evidence_units_fts
            WHERE evidence_units_fts MATCH ?
            ORDER BY raw_score ASC, unit_id ASC
            LIMIT ?
            """,
            (fts_query, top_k),
        ).fetchall()

        return [(row["unit_id"], 1.0 / (1.0 + abs(float(row["raw_score"])))) for row in rows]

    def _search_with_like(self, query: str, top_k: int) -> list[tuple[str, float]]:
        """Fallback search using simple SQL substring scoring."""

        normalized_query = query.lower().strip()
        tokens = self._tokenize_query(query)
        if not normalized_query and not tokens:
            return []

        score_clauses: list[str] = []
        score_params: list[str] = []
        where_clauses: list[str] = []
        where_params: list[str] = []

        if normalized_query:
            score_clauses.append("CASE WHEN instr(lower(text), ?) > 0 THEN 4.0 ELSE 0.0 END")
            score_params.append(normalized_query)
            where_clauses.append("instr(lower(text), ?) > 0")
            where_params.append(normalized_query)

        for token in tokens:
            score_clauses.append("CASE WHEN instr(lower(text), ?) > 0 THEN 1.0 ELSE 0.0 END")
            score_params.append(token)
            where_clauses.append("instr(lower(text), ?) > 0")
            where_params.append(token)

        rows = self._conn.execute(
            f"""
            SELECT unit_id, ({" + ".join(score_clauses)}) AS score
            FROM evidence_units
            WHERE {" OR ".join(where_clauses)}
            ORDER BY score DESC, unit_id ASC
            LIMIT ?
            """,
            [*score_params, *where_params, top_k],
        ).fetchall()

        return [(row["unit_id"], float(row["score"])) for row in rows]

    def _tokenize_query(self, query: str) -> list[str]:
        """Tokenize a query into deterministic lowercase search terms."""

        seen: set[str] = set()
        tokens: list[str] = []
        for token in self._TOKEN_RE.findall(query.lower()):
            if token in seen:
                continue
            seen.add(token)
            tokens.append(token)
        return tokens


EvidenceUnitRetriever = EvidenceUnitIndex


def ingest_evidence_units(
    units: list[EvidenceUnit],
    db_path: str = "evidence_units.db",
    mode: str = "upsert",
) -> None:
    """Open the SQLite store, ingest EvidenceUnits, and close it cleanly.

    This helper makes ingestion explicit: EvidenceUnits are written into the
    canonical SQLite store, and retrieval later reads from the same store.
    """

    index = EvidenceUnitIndex(db_path=db_path)
    try:
        index.ingest_units(units, mode=mode)
    finally:
        index.close()


if __name__ == "__main__":
    sample_units = [
        EvidenceUnit(
            unit_id="u1",
            type="prose",
            source_file="/notes/setup.md",
            region={"line_start": 1, "line_end": 2},
            signals={"unit_type": "prose"},
            text="Use the AWS CLI for environment setup.",
            structural_context={"section": "Setup"},
            next_unit_id="u2",
        ),
        EvidenceUnit(
            unit_id="u2",
            type="command",
            source_file="/notes/setup.md",
            region={"line_start": 3, "line_end": 3},
            signals={"command_candidate": True, "unit_type": "command"},
            text="aws s3 ls",
            structural_context={"section": "Setup"},
            prev_unit_id="u1",
            next_unit_id="u3",
        ),
        EvidenceUnit(
            unit_id="u3",
            type="command",
            source_file="/notes/setup.md",
            region={"line_start": 4, "line_end": 4},
            signals={"command_candidate": True, "unit_type": "command"},
            text="aws dynamodb list-tables --endpoint-url http://localhost:8001",
            structural_context={"section": "Setup"},
            prev_unit_id="u2",
            next_unit_id="u4",
        ),
        EvidenceUnit(
            unit_id="u4",
            type="opensearch_query",
            source_file="/notes/search.md",
            region={"line_start": 10, "line_end": 13},
            signals={"opensearch_query_candidate": True, "unit_type": "opensearch_query"},
            text='GET my-index/_search\n{"query": {"match_all": {}}}',
            structural_context={"section": "Search"},
            prev_unit_id="u3",
            next_unit_id="u5",
        ),
        EvidenceUnit(
            unit_id="u5",
            type="sql",
            source_file="/notes/query.sql",
            region={"line_start": 1, "line_end": 2},
            signals={"sql_candidate": True, "unit_type": "sql"},
            text="SELECT * FROM users WHERE active = 1;",
            structural_context={"section": "SQL"},
            prev_unit_id="u4",
        ),
    ]

    index = EvidenceUnitIndex()
    index.ingest_units(sample_units, mode="replace")
    retrieval_result = index.retrieve("aws endpoint url", top_k=3, expand_neighbors=1)

    print("Query:", retrieval_result.query)
    print("Hits:")
    for hit in retrieval_result.hits:
        print(
            {
                "rank": hit.rank,
                "score": hit.score,
                "unit_id": hit.unit.unit_id,
                "type": hit.unit.type,
                "source_file": hit.unit.source_file,
                "text": hit.unit.text,
            }
        )
    print("Neighbors:")
    for unit_id, neighbor_units in retrieval_result.neighbors.items():
        print(unit_id, [neighbor.unit_id for neighbor in neighbor_units])
    index.close()
