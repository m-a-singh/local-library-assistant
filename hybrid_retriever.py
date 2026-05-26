"""Step 14 hybrid retrieval over the SQLite EvidenceUnit store.

This module keeps SQLite as the canonical EvidenceUnit store and adds an
auxiliary semantic index keyed by ``unit_id``. Final retrieval results are
always hydrated back into canonical ``EvidenceUnit`` objects from SQLite.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import logging
import math
import os
import sqlite3
import time
import urllib.error
import urllib.request
from array import array
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from query_retriever import (
    EvidenceUnit,
    EvidenceUnitIndex,
    RetrievalHit,
    RetrievalResult,
)

LOGGER = logging.getLogger(__name__)
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)
LOGGER.setLevel(
    logging.DEBUG if os.getenv("LOCAL_LIBRARY_ASSISTANT_DEBUG") else logging.CRITICAL
)


@dataclass(slots=True)
class SemanticHit:
    unit_id: str
    score: float
    rank: int


@dataclass(slots=True)
class HybridRetrievalResult:
    query: str
    lexical_hit_count: int
    semantic_hit_count: int
    hits: list[Any] = field(default_factory=list)
    neighbors: dict[str, list[Any]] = field(default_factory=dict)
    retrieval_summary: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class HybridRetrieverConfig:
    api_base: str = "http://localhost:4000"
    api_key: str = field(
        default_factory=lambda: os.getenv("LITELLM_PROXY_API_KEY", "local-dev-key")
    )
    embedding_model: str = field(
        default_factory=lambda: os.getenv(
            "STEP14_EMBED_MODEL", "text-embedding-ada-002"
        )
    )
    timeout_seconds: float = 120.0
    batch_size: int = 24
    lexical_weight: float = 1.0
    semantic_weight: float = 0.9
    rrf_k: float = 60.0
    max_text_chars: int = 4000


def hybrid_retrieval_result_to_dict(result: HybridRetrievalResult) -> dict[str, Any]:
    """Convert a hybrid retrieval result into a JSON-safe dictionary."""

    return {
        "query": result.query,
        "lexical_hit_count": result.lexical_hit_count,
        "semantic_hit_count": result.semantic_hit_count,
        "hits": [
            {
                "rank": hit.rank,
                "score": hit.score,
                "unit": {
                    "unit_id": hit.unit.unit_id,
                    "type": hit.unit.type,
                    "source_file": hit.unit.source_file,
                    "region": hit.unit.region,
                    "signals": hit.unit.signals,
                    "text": hit.unit.text,
                    "structural_context": hit.unit.structural_context,
                    "prev_unit_id": hit.unit.prev_unit_id,
                    "next_unit_id": hit.unit.next_unit_id,
                },
            }
            for hit in result.hits
        ],
        "neighbors": {
            anchor_unit_id: [
                {
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
                for unit in units
            ]
            for anchor_unit_id, units in result.neighbors.items()
        },
        "retrieval_summary": dict(result.retrieval_summary),
    }


def format_hybrid_retrieval_result_text(result: HybridRetrievalResult) -> str:
    """Render a hybrid retrieval result as human-readable text."""

    lines = [f"Query: {result.query}"]
    lines.append(
        f"Retrieval mix: lexical={result.lexical_hit_count}, semantic={result.semantic_hit_count}"
    )

    notes = result.retrieval_summary.get("notes", [])
    if isinstance(notes, list):
        concise_notes = [str(note).strip() for note in notes if str(note).strip()]
        if concise_notes:
            lines.append(f"Status: {', '.join(concise_notes)}")

    if not result.hits:
        lines.append("No results.")
        return "\n".join(lines)

    lines.append(f"Hits: {len(result.hits)}")
    lines.append("")
    for hit in result.hits:
        lines.append(f"[{hit.rank}]")
        lines.append(f"  rank: {hit.rank}")
        lines.append(f"  score: {hit.score:.6f}")
        lines.append(f"  unit_id: {hit.unit.unit_id}")
        lines.append(f"  type: {hit.unit.type}")
        lines.append(f"  source_file: {hit.unit.source_file}")
        lines.append(
            f"  region: {json.dumps(hit.unit.region, ensure_ascii=False, sort_keys=True)}"
        )
        lines.append(
            f"  signals: {json.dumps(hit.unit.signals, ensure_ascii=False, sort_keys=True)}"
        )
        lines.append("  text:")
        lines.extend(
            f"    {line}" for line in (hit.unit.text.splitlines() or ["<empty>"])
        )
        lines.append("")

    if result.neighbors:
        lines.append("Neighbors:")
        for anchor_unit_id, neighbors in result.neighbors.items():
            lines.append(f"  anchor: {anchor_unit_id}")
            if not neighbors:
                lines.append("    <none>")
                continue
            for index, unit in enumerate(neighbors, start=1):
                lines.append(f"    [{index}]")
                lines.append(f"      unit_id: {unit.unit_id}")
                lines.append(f"      type: {unit.type}")
                lines.append(f"      source_file: {unit.source_file}")
                lines.append(
                    f"      region: {json.dumps(unit.region, ensure_ascii=False, sort_keys=True)}"
                )
                lines.append(
                    f"      signals: {json.dumps(unit.signals, ensure_ascii=False, sort_keys=True)}"
                )
                lines.append("      text:")
                lines.extend(
                    f"        {line}"
                    for line in (unit.text.splitlines() or ["<empty>"])
                )

    return "\n".join(lines).rstrip()


class HybridRetriever:
    """Hybrid lexical + semantic retriever over canonical SQLite EvidenceUnits."""

    def __init__(
        self,
        db_path: str = "evidence_units.db",
        *,
        index: EvidenceUnitIndex | None = None,
        config: HybridRetrieverConfig | None = None,
    ):
        self.db_path = db_path
        self.index = index or EvidenceUnitIndex(db_path=db_path)
        self._owns_index = index is None
        self.config = config or HybridRetrieverConfig()
        self._semantic_cache: dict[str, tuple[array, float]] | None = None
        self._semantic_cache_model: str | None = None
        self._last_index_summary: dict[str, Any] = {}
        self._ensure_semantic_schema()

    def close(self) -> None:
        """Close the Step 12 index when this retriever owns it."""

        if self._owns_index:
            self.index.close()

    def ensure_semantic_index(self, *, rebuild: bool = False) -> dict[str, Any]:
        """Ensure the semantic embedding table is populated for current EvidenceUnits."""

        conn = self.index._conn
        model = self.config.embedding_model
        units = conn.execute(
            """
            SELECT unit_id, type, source_file, text
            FROM evidence_units
            ORDER BY unit_id ASC
            """
        ).fetchall()

        existing_rows = conn.execute(
            """
            SELECT unit_id, text_hash
            FROM evidence_unit_semantic_index
            WHERE embedding_model = ?
            """,
            (model,),
        ).fetchall()
        existing_hashes = {row["unit_id"]: row["text_hash"] for row in existing_rows}

        expected_hashes: dict[str, str] = {}
        records_to_embed: list[tuple[str, str, str]] = []
        for row in units:
            embedding_text = self._embedding_text(
                unit_type=row["type"],
                source_file=row["source_file"],
                text=row["text"],
            )
            text_hash = self._text_hash(embedding_text)
            unit_id = row["unit_id"]
            expected_hashes[unit_id] = text_hash
            if rebuild or existing_hashes.get(unit_id) != text_hash:
                records_to_embed.append((unit_id, embedding_text, text_hash))

        stale_unit_ids = set(existing_hashes) - set(expected_hashes)

        updated = 0
        with conn:
            if stale_unit_ids:
                conn.executemany(
                    """
                    DELETE FROM evidence_unit_semantic_index
                    WHERE embedding_model = ? AND unit_id = ?
                    """,
                    [(model, unit_id) for unit_id in sorted(stale_unit_ids)],
                )
            if rebuild and existing_hashes:
                conn.execute(
                    "DELETE FROM evidence_unit_semantic_index WHERE embedding_model = ?",
                    (model,),
                )

            for batch in _batched(records_to_embed, self.config.batch_size):
                embeddings = self._embed_texts([item[1] for item in batch])
                payloads = []
                for (unit_id, _embedding_text, text_hash), vector in zip(
                    batch, embeddings, strict=True
                ):
                    packed, dim, norm = self._pack_vector(vector)
                    payloads.append(
                        (
                            unit_id,
                            model,
                            text_hash,
                            packed.tobytes(),
                            dim,
                            norm,
                            datetime.now(timezone.utc).isoformat(),
                        )
                    )
                if payloads:
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO evidence_unit_semantic_index (
                            unit_id,
                            embedding_model,
                            text_hash,
                            vector_blob,
                            vector_dim,
                            vector_norm,
                            updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        payloads,
                    )
                    updated += len(payloads)

        self._semantic_cache = None
        self._semantic_cache_model = None
        total_rows = conn.execute(
            """
            SELECT COUNT(*) AS row_count
            FROM evidence_unit_semantic_index
            WHERE embedding_model = ?
            """,
            (model,),
        ).fetchone()["row_count"]
        summary = {
            "embedding_model": model,
            "unit_count": len(units),
            "updated_embeddings": updated,
            "stale_embeddings_removed": len(stale_unit_ids),
            "semantic_index_rows": int(total_rows),
            "rebuilt": rebuild,
        }
        self._last_index_summary = summary
        return summary

    def semantic_search(
        self, query: str, *, top_k: int = 5, ensure_index: bool = True
    ) -> list[SemanticHit]:
        """Return top semantic hits for a query using the auxiliary embedding index."""

        if ensure_index:
            self.ensure_semantic_index(rebuild=False)
        vectors = self._load_semantic_vectors()
        if not vectors or top_k <= 0 or not query.strip():
            return []

        query_vector = self._embed_texts([query])[0]
        query_array, _dim, query_norm = self._pack_vector(query_vector)
        if query_norm == 0.0:
            return []

        scored: list[tuple[float, str]] = []
        for unit_id, (doc_vector, doc_norm) in vectors.items():
            if doc_norm == 0.0:
                continue
            similarity = self._cosine_similarity(
                query_array, query_norm, doc_vector, doc_norm
            )
            scored.append((similarity, unit_id))

        top_hits = heapq.nlargest(top_k, scored, key=lambda item: item[0])
        return [
            SemanticHit(unit_id=unit_id, score=score, rank=rank)
            for rank, (score, unit_id) in enumerate(top_hits, start=1)
        ]

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        expand_neighbors: int = 0,
        lexical_top_k: int | None = None,
        semantic_top_k: int | None = None,
        lexical_result: RetrievalResult | None = None,
        rebuild_semantic_index: bool = False,
    ) -> HybridRetrievalResult:
        """Combine Step 12 lexical retrieval with semantic retrieval over the same units."""

        lexical_limit = lexical_top_k if lexical_top_k is not None else max(top_k, 6)
        semantic_limit = semantic_top_k if semantic_top_k is not None else max(top_k, 6)

        if lexical_result is None:
            lexical_result = self.index.retrieve(
                query, top_k=lexical_limit, expand_neighbors=expand_neighbors
            )

        notes: list[str] = []
        semantic_index_summary: dict[str, Any] | None = None
        semantic_hits: list[SemanticHit] = []
        try:
            semantic_index_summary = self.ensure_semantic_index(
                rebuild=rebuild_semantic_index
            )
            semantic_hits = self.semantic_search(
                query, top_k=semantic_limit, ensure_index=False
            )
        except Exception as exc:
            notes.append(f"semantic_unavailable:{type(exc).__name__}")

        merged_hits = self._merge_hits(
            lexical_result=lexical_result,
            semantic_hits=semantic_hits,
            top_k=top_k,
        )
        neighbors = self._expand_neighbors_for_hits(merged_hits, expand_neighbors)

        return HybridRetrievalResult(
            query=query,
            lexical_hit_count=len(lexical_result.hits),
            semantic_hit_count=len(semantic_hits),
            hits=merged_hits,
            neighbors=neighbors,
            retrieval_summary={
                "db_path": self.db_path,
                "embedding_model": self.config.embedding_model,
                "lexical_top_k": lexical_limit,
                "semantic_top_k": semantic_limit,
                "top_k": top_k,
                "expand_neighbors": expand_neighbors,
                "merge_strategy": "weighted_rrf + confidence_bonus",
                "semantic_index": semantic_index_summary or self._last_index_summary,
                "notes": notes,
            },
        )

    def _merge_hits(
        self,
        *,
        lexical_result: RetrievalResult,
        semantic_hits: list[SemanticHit],
        top_k: int,
    ) -> list[RetrievalHit]:
        lexical_rank_map = {hit.unit.unit_id: hit.rank for hit in lexical_result.hits}
        semantic_rank_map = {hit.unit_id: hit.rank for hit in semantic_hits}

        semantic_unit_ids = [
            hit.unit_id for hit in semantic_hits if hit.unit_id not in lexical_rank_map
        ]
        semantic_units = {
            unit.unit_id: unit for unit in self.index._fetch_by_ids(semantic_unit_ids)
        }

        all_unit_ids = sorted(set(lexical_rank_map) | set(semantic_rank_map))
        scored: list[tuple[float, str, EvidenceUnit]] = []
        lexical_units = {hit.unit.unit_id: hit.unit for hit in lexical_result.hits}

        for unit_id in all_unit_ids:
            lexical_rank = lexical_rank_map.get(unit_id)
            semantic_rank = semantic_rank_map.get(unit_id)
            unit = lexical_units.get(unit_id) or semantic_units.get(unit_id)
            if unit is None:
                continue

            score = 0.0
            if lexical_rank is not None:
                score += self.config.lexical_weight / (self.config.rrf_k + lexical_rank)
            if semantic_rank is not None:
                score += self.config.semantic_weight / (
                    self.config.rrf_k + semantic_rank
                )
            if lexical_rank is not None and semantic_rank is not None:
                score += 0.02
            score += self._confidence_bonus(unit)

            scored.append((score, unit_id, unit))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            RetrievalHit(unit=unit, score=score, rank=rank)
            for rank, (score, _unit_id, unit) in enumerate(scored[:top_k], start=1)
        ]

    def _expand_neighbors_for_hits(
        self,
        hits: list[RetrievalHit],
        expand_neighbors: int,
    ) -> dict[str, list[EvidenceUnit]]:
        if expand_neighbors <= 0:
            return {}
        neighbors: dict[str, list[EvidenceUnit]] = {}
        for hit in hits:
            expanded = self.index._expand_neighbors(hit.unit, expand_neighbors)
            if expanded:
                neighbors[hit.unit.unit_id] = expanded
        return neighbors

    def _ensure_semantic_schema(self) -> None:
        conn = self.index._conn
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS evidence_unit_semantic_index (
                    unit_id TEXT NOT NULL,
                    embedding_model TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    vector_blob BLOB NOT NULL,
                    vector_dim INTEGER NOT NULL,
                    vector_norm REAL NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (unit_id, embedding_model)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_evidence_unit_semantic_model
                ON evidence_unit_semantic_index (embedding_model)
                """
            )

    def _load_semantic_vectors(self) -> dict[str, tuple[array, float]]:
        if (
            self._semantic_cache is not None
            and self._semantic_cache_model == self.config.embedding_model
        ):
            return self._semantic_cache

        rows = self.index._conn.execute(
            """
            SELECT unit_id, vector_blob, vector_norm
            FROM evidence_unit_semantic_index
            WHERE embedding_model = ?
            """,
            (self.config.embedding_model,),
        ).fetchall()
        cache: dict[str, tuple[array, float]] = {}
        for row in rows:
            vector = array("f")
            vector.frombytes(row["vector_blob"])
            cache[row["unit_id"]] = (vector, float(row["vector_norm"]))
        self._semantic_cache = cache
        self._semantic_cache_model = self.config.embedding_model
        return cache

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {
            "model": self.config.embedding_model,
            "input": texts,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self._embeddings_url(),
            data=body,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_seconds
            ) as response:
                raw_response = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"embedding HTTP {exc.code}: {error_body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"embedding endpoint unreachable: {exc}") from exc

        parsed = json.loads(raw_response)
        data = parsed.get("data", [])
        if not isinstance(data, list) or len(data) != len(texts):
            raise RuntimeError("embedding response size mismatch")

        embeddings: list[list[float]] = []
        for item in data:
            vector = item.get("embedding")
            if not isinstance(vector, list):
                raise RuntimeError("embedding response missing vector")
            embeddings.append([float(value) for value in vector])
        return embeddings

    def _embedding_text(self, *, unit_type: str, source_file: str, text: str) -> str:
        trimmed_text = text.strip()
        if len(trimmed_text) > self.config.max_text_chars:
            trimmed_text = trimmed_text[: self.config.max_text_chars]
        source_name = Path(source_file).name
        return f"type={unit_type}\nsource={source_name}\ntext={trimmed_text}"

    @staticmethod
    def _pack_vector(values: list[float] | array) -> tuple[array, int, float]:
        vector = values if isinstance(values, array) else array("f", values)
        norm = math.sqrt(sum(float(value) * float(value) for value in vector))
        return vector, len(vector), norm

    @staticmethod
    def _cosine_similarity(
        query_vector: array, query_norm: float, doc_vector: array, doc_norm: float
    ) -> float:
        if query_norm == 0.0 or doc_norm == 0.0:
            return 0.0
        dot = 0.0
        for left, right in zip(query_vector, doc_vector):
            dot += float(left) * float(right)
        return dot / (query_norm * doc_norm)

    def _embeddings_url(self) -> str:
        base = self.config.api_base.rstrip("/")
        if base.endswith("/v1"):
            return f"{base}/embeddings"
        return f"{base}/v1/embeddings"

    @staticmethod
    def _text_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _confidence_bonus(unit: EvidenceUnit) -> float:
        confidence = unit.signals.get("confidence")
        if confidence == "high":
            return 0.005
        if confidence == "low":
            return -0.005
        flags = unit.signals.get("flags")
        if isinstance(flags, list) and "weak_boundary" in flags:
            return -0.003
        return 0.0


def retrieve_hybrid(
    query: str,
    *,
    db_path: str = "evidence_units.db",
    top_k: int = 5,
    expand_neighbors: int = 0,
) -> HybridRetrievalResult:
    """Convenience helper for one-shot hybrid retrieval."""

    retriever = HybridRetriever(db_path=db_path)
    try:
        return retriever.retrieve(query, top_k=top_k, expand_neighbors=expand_neighbors)
    finally:
        retriever.close()


def _batched(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]
