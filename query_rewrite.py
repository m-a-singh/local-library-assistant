"""Step 13.5 query rewriting for fuzzy lexical EvidenceUnit retrieval.

This layer keeps Step 12 lexical retrieval as the actual retrieval engine.
It expands a natural-language query into a small set of lexical variants,
runs the existing SQLite-backed retrieval for each variant, then merges and
reranks the resulting hits with a transparent heuristic.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from query_retriever import EvidenceUnit, EvidenceUnitIndex, RetrievalHit

LOGGER = logging.getLogger(__name__)
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.DEBUG if os.getenv("LOCAL_LIBRARY_ASSISTANT_DEBUG") else logging.CRITICAL)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+")
_NON_WORD_RE = re.compile(r"[^a-z0-9_./:-]+")
_WHITESPACE_RE = re.compile(r"\s+")
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

STOPWORDS = {
    "a",
    "an",
    "are",
    "can",
    "command",
    "do",
    "does",
    "for",
    "how",
    "i",
    "is",
    "me",
    "my",
    "please",
    "run",
    "show",
    "start",
    "tell",
    "the",
    "to",
    "what",
}
COMMAND_INTENT_TOKENS = {
    "command",
    "docker",
    "launch",
    "localstack",
    "litellm",
    "open",
    "restart",
    "run",
    "running",
    "start",
    "startup",
    "stop",
}
QUERY_INTENT_TOKENS = {
    "query",
    "sql",
    "select",
    "insert",
    "update",
    "delete",
    "search",
    "dsl",
    "_search",
}
KNOWN_VARIANTS = {
    "docker": ["docker", "docker run", "docker compose up", "docker start", "docker ps"],
    "litellm": [
        "litellm",
        "litellm --config",
        "litellm port 4000",
        "localhost 4000",
        "litellm proxy",
    ],
    "localstack": [
        "localstack",
        "awslocal",
        "localhost 4566",
        "localstack start",
        "docker compose up",
    ],
    "aws": ["aws", "aws --endpoint-url", "awslocal", "localhost 4566"],
    "awslocal": ["awslocal", "localstack", "localhost 4566"],
    "docker-compose": ["docker compose up", "docker compose", "docker"],
    "compose": ["docker compose up", "docker compose", "docker"],
}
REWRITE_NOTES_MAX = 4


@dataclass
class QueryRewriteResult:
    original_query: str
    rewrites: list[str] = field(default_factory=list)
    model: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class MergedRetrievalResult:
    original_query: str
    rewrites: list[str] = field(default_factory=list)
    hits: list[Any] = field(default_factory=list)
    neighbors: dict[str, list[Any]] = field(default_factory=dict)
    retrieval_summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryRewriteRetryPolicy:
    max_attempts: int = 2
    backoff_seconds: float = 0.5


@dataclass
class QueryRewriteConfig:
    ollama_base_url: str = "http://localhost:4000"
    litellm_api_key: str = field(default_factory=lambda: os.getenv("LITELLM_PROXY_API_KEY", "local-dev-key"))
    # Model id/tag for the upstream chat-completions provider (LiteLLM/Ollama/etc).
    # Set via env var to avoid leaking internal model naming conventions.
    model: str = field(default_factory=lambda: os.getenv("LLA_REWRITE_MODEL", "llama3:8b"))
    max_tokens: int = 350
    timeout_seconds: float = 60.0
    temperature: float = 0.0
    retry_policy: QueryRewriteRetryPolicy = field(default_factory=QueryRewriteRetryPolicy)
    json_correction_attempts: int = 1
    max_rewrites: int = 6


@dataclass
class _MergeAccumulator:
    unit: EvidenceUnit
    max_lexical_score: float
    best_rank: int
    matched_variants: list[str] = field(default_factory=list)
    matched_variant_set: set[str] = field(default_factory=set)
    score_components: dict[str, float] = field(default_factory=dict)


def query_rewrite_result_to_dict(result: QueryRewriteResult) -> dict[str, Any]:
    """Convert a rewrite result into a JSON-safe dictionary."""

    return {
        "original_query": result.original_query,
        "rewrites": list(result.rewrites),
        "model": result.model,
        "notes": list(result.notes),
    }


def merged_retrieval_result_to_dict(result: MergedRetrievalResult) -> dict[str, Any]:
    """Convert a merged retrieval result into a JSON-safe dictionary."""

    return {
        "original_query": result.original_query,
        "rewrites": list(result.rewrites),
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


def format_merged_retrieval_result_text(result: MergedRetrievalResult) -> str:
    """Render a merged rewrite-aware retrieval result as human-readable text."""

    lines = [f"Query: {result.original_query}"]
    if result.rewrites:
        lines.append(f"Rewrites: {', '.join(result.rewrites)}")
    else:
        lines.append("Rewrites: <none>")

    if result.retrieval_summary:
        summary = result.retrieval_summary
        notes = summary.get("notes") or []
        if notes:
            lines.append(f"Notes: {', '.join(str(note) for note in notes)}")

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
        lines.append(f"  region: {json.dumps(hit.unit.region, ensure_ascii=False, sort_keys=True)}")
        lines.append(f"  signals: {json.dumps(hit.unit.signals, ensure_ascii=False, sort_keys=True)}")
        lines.append("  text:")
        lines.extend(f"    {line}" for line in (hit.unit.text.splitlines() or ["<empty>"]))
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
                lines.append(f"      region: {json.dumps(unit.region, ensure_ascii=False, sort_keys=True)}")
                lines.append(f"      signals: {json.dumps(unit.signals, ensure_ascii=False, sort_keys=True)}")
                lines.append("      text:")
                lines.extend(f"        {line}" for line in (unit.text.splitlines() or ["<empty>"]))

    if result.retrieval_summary:
        lines.append("Retrieval summary:")
        lines.append(f"  {json.dumps(result.retrieval_summary, ensure_ascii=False, sort_keys=True)}")
    return "\n".join(lines).rstrip()


class QueryRewriteClient:
    """Rewrite natural-language queries into lexical variants for Step 12 retrieval."""

    def __init__(
        self,
        db_path: str = "evidence_units.db",
        *,
        index: EvidenceUnitIndex | None = None,
        config: QueryRewriteConfig | None = None,
    ):
        self.db_path = db_path
        self.config = config or QueryRewriteConfig()
        self.index = index or EvidenceUnitIndex(db_path=db_path)
        self._owns_index = index is None
        self.last_model_used: str | None = None

    def close(self) -> None:
        """Close the Step 12 index when this client owns it."""

        if self._owns_index:
            self.index.close()

    def rewrite_query(self, query: str) -> QueryRewriteResult:
        """Generate concise lexical rewrites for a natural-language query."""

        heuristic_rewrites = self._heuristic_rewrites(query)
        notes: list[str] = []
        llm_rewrites: list[str] = []
        model_used: str | None = None

        try:
            llm_rewrites = self._llm_rewrites(query)
            if llm_rewrites:
                notes.append("local_litellm_rewrites")
                model_used = self.last_model_used
        except Exception as exc:
            LOGGER.warning("Query rewrite LLM unavailable, falling back to heuristics: %s", exc)
            notes.append(f"llm_unavailable:{type(exc).__name__}")

        rewrites = _dedupe_strings([*heuristic_rewrites, *llm_rewrites], exclude={query.strip()})
        rewrites = rewrites[: self.config.max_rewrites]
        if heuristic_rewrites:
            notes.append("heuristic_rewrites")
        if not rewrites:
            notes.append("no_rewrites_generated")
        return QueryRewriteResult(
            original_query=query,
            rewrites=rewrites,
            model=model_used,
            notes=notes[:REWRITE_NOTES_MAX],
        )

    def retrieve_with_rewrites(
        self,
        query: str,
        *,
        top_k: int = 5,
        expand_neighbors: int = 0,
        per_rewrite_top_k: int | None = None,
    ) -> MergedRetrievalResult:
        """Run Step 12 retrieval for the original query and rewrite variants, then merge the hits."""

        rewrite_result = self.rewrite_query(query)
        variants = [query, *rewrite_result.rewrites]
        per_variant_top_k = per_rewrite_top_k if per_rewrite_top_k is not None else max(top_k, 4)

        merged: dict[str, _MergeAccumulator] = {}
        merged_neighbors: dict[str, list[EvidenceUnit]] = {}
        variant_hit_counts: dict[str, int] = {}
        query_tokens = _meaningful_tokens(query)
        intent = self._query_intent(query, query_tokens)

        for variant in variants:
            retrieval = self.index.retrieve(variant, top_k=per_variant_top_k, expand_neighbors=expand_neighbors)
            variant_hit_counts[variant] = len(retrieval.hits)
            for hit in retrieval.hits:
                unit_id = hit.unit.unit_id
                accumulator = merged.get(unit_id)
                if accumulator is None:
                    accumulator = _MergeAccumulator(
                        unit=hit.unit,
                        max_lexical_score=hit.score,
                        best_rank=hit.rank,
                    )
                    merged[unit_id] = accumulator
                else:
                    accumulator.max_lexical_score = max(accumulator.max_lexical_score, hit.score)
                    accumulator.best_rank = min(accumulator.best_rank, hit.rank)

                if variant not in accumulator.matched_variant_set:
                    accumulator.matched_variant_set.add(variant)
                    accumulator.matched_variants.append(variant)

                if unit_id not in merged_neighbors:
                    merged_neighbors[unit_id] = []
                merged_neighbors[unit_id] = _merge_neighbor_units(
                    merged_neighbors[unit_id],
                    retrieval.neighbors.get(unit_id, []),
                )

        ranked_hits = self._rerank_hits(
            merged,
            query_tokens=query_tokens,
            intent=intent,
            top_k=top_k,
        )
        final_neighbors = {
            hit.unit.unit_id: merged_neighbors.get(hit.unit.unit_id, [])
            for hit in ranked_hits
            if merged_neighbors.get(hit.unit.unit_id)
        }

        return MergedRetrievalResult(
            original_query=query,
            rewrites=rewrite_result.rewrites,
            hits=ranked_hits,
            neighbors=final_neighbors,
            retrieval_summary={
                "db_path": self.db_path,
                "rewrite_model": rewrite_result.model,
                "variant_count": len(variants),
                "rewrites_used": len(rewrite_result.rewrites),
                "per_variant_top_k": per_variant_top_k,
                "top_k": top_k,
                "expand_neighbors": expand_neighbors,
                "total_merged_hits": len(merged),
                "returned_hits": len(ranked_hits),
                "variant_hit_counts": variant_hit_counts,
                "notes": list(rewrite_result.notes),
                "rerank_strategy": "max_lexical_score + support_count + overlap + unit_type + confidence",
            },
        )

    def _rerank_hits(
        self,
        merged: dict[str, _MergeAccumulator],
        *,
        query_tokens: list[str],
        intent: dict[str, Any],
        top_k: int,
    ) -> list[RetrievalHit]:
        ranked: list[tuple[float, int, str, EvidenceUnit]] = []

        for unit_id, accumulator in merged.items():
            unit = accumulator.unit
            score = accumulator.max_lexical_score
            support_bonus = 0.25 * max(0, len(accumulator.matched_variants) - 1)
            overlap_bonus = self._overlap_bonus(query_tokens, unit)
            type_bonus = self._type_bonus(intent, unit)
            confidence_bonus = self._confidence_bonus(unit)
            original_bonus = 0.0
            if intent["original_query"] in accumulator.matched_variant_set:
                original_bonus = 0.4
            final_score = score + support_bonus + overlap_bonus + type_bonus + confidence_bonus + original_bonus
            ranked.append((final_score, accumulator.best_rank, unit_id, unit))

        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        return [
            RetrievalHit(unit=unit, score=score, rank=rank)
            for rank, (score, _best_rank, _unit_id, unit) in enumerate(ranked[:top_k], start=1)
        ]

    def _heuristic_rewrites(self, query: str) -> list[str]:
        normalized = _normalize_query(query)
        tokens = _meaningful_tokens(normalized)
        rewrites: list[str] = []

        for token in tokens:
            if token in KNOWN_VARIANTS:
                rewrites.extend(KNOWN_VARIANTS[token])

        subject_tokens = [token for token in tokens if token not in STOPWORDS]
        if subject_tokens:
            subject = " ".join(subject_tokens[:2])
            rewrites.extend([subject, f"{subject} run", f"{subject} start"])

        if "docker" in tokens and {"start", "run", "running", "up"} & set(tokens):
            rewrites.extend(["docker compose up", "docker start", "docker ps"])
        if "litellm" in tokens and {"start", "run", "running"} & set(tokens):
            rewrites.extend(["litellm run", "litellm --config", "localhost 4000"])
        if "localstack" in tokens and {"start", "run", "running"} & set(tokens):
            rewrites.extend(["localstack start", "awslocal", "localhost 4566"])

        if not rewrites and subject_tokens:
            rewrites.extend(subject_tokens[:3])

        return _dedupe_strings(rewrites, exclude={query.strip()})[: self.config.max_rewrites]

    def _llm_rewrites(self, query: str) -> list[str]:
        system_prompt = (
            "You rewrite user search queries into short lexical search variants for a local EvidenceUnit FTS index. "
            "Return STRICT JSON ONLY with keys: rewrites (array of strings), notes (array of strings). "
            "Rules: do not answer the question, do not add commentary, keep rewrites short, keyword-oriented, and literal. "
            "Preserve product names, command names, port numbers, and likely command tokens when present."
        )
        user_prompt = json.dumps(
            {
                "query": query,
                "examples": [
                    {
                        "query": "command to start docker?",
                        "rewrites": [
                            "docker",
                            "docker start",
                            "docker compose up",
                            "docker run",
                            "docker ps",
                        ],
                    },
                    {
                        "query": "start litellm",
                        "rewrites": [
                            "litellm",
                            "litellm run",
                            "litellm --config",
                            "litellm port 4000",
                        ],
                    },
                    {
                        "query": "how do I run localstack?",
                        "rewrites": [
                            "localstack",
                            "awslocal",
                            "localstack start",
                            "localhost 4566",
                        ],
                    },
                ],
            },
            ensure_ascii=False,
        )
        raw = self._call_litellm(
            model=self.config.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        for attempt in range(self.config.json_correction_attempts + 1):
            try:
                parsed = _parse_json_only(raw)
                rewrites = parsed.get("rewrites", [])
                if not isinstance(rewrites, list):
                    raise ValueError("rewrites must be a list")
                return _dedupe_strings([str(item).strip() for item in rewrites], exclude={query.strip()})
            except Exception:
                if attempt >= self.config.json_correction_attempts:
                    raise
                correction_prompt = (
                    "Your previous response was invalid. Return JSON only with keys rewrites and notes.\n"
                    f"Original query: {query}\n"
                    f"Previous output:\n{raw}"
                )
                raw = self._call_litellm(
                    model=self.config.model,
                    system_prompt=system_prompt,
                    user_prompt=correction_prompt,
                    temperature=0.0,
                    max_tokens=self.config.max_tokens,
                )
        return []

    def _call_litellm(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        attempts = self.config.retry_policy.max_attempts
        last_exc: Exception | None = None

        for attempt in range(1, attempts + 1):
            self.last_model_used = model.strip()
            try:
                payload = {
                    "model": self.last_model_used,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                request = urllib.request.Request(
                    self._chat_completions_url(),
                    data=body,
                    headers={
                        "Authorization": f"Bearer {self.config.litellm_api_key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                    raw_response = response.read().decode("utf-8")
                parsed_response = json.loads(raw_response)
                choices = parsed_response.get("choices", [])
                if not choices:
                    raise RuntimeError("rewrite response did not include choices")
                message = choices[0].get("message", {})
                content = message.get("content")
                if not isinstance(content, str):
                    raise RuntimeError("rewrite response did not include message content")
                return content
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                last_exc = RuntimeError(f"HTTP {exc.code}: {error_body}")
            except Exception as exc:
                last_exc = exc
            if attempt < attempts:
                time.sleep(self.config.retry_policy.backoff_seconds * attempt)
                continue
            raise RuntimeError(f"rewrite service failure: {type(last_exc).__name__}: {last_exc}") from last_exc
        raise RuntimeError("rewrite service failure: unknown")

    def _chat_completions_url(self) -> str:
        base = self.config.ollama_base_url.rstrip("/")
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def _query_intent(self, original_query: str, query_tokens: list[str]) -> dict[str, Any]:
        token_set = set(query_tokens)
        return {
            "original_query": original_query.strip(),
            "command_like": bool(token_set & COMMAND_INTENT_TOKENS),
            "query_like": bool(token_set & QUERY_INTENT_TOKENS),
            "subject_tokens": [token for token in query_tokens if token not in STOPWORDS][:3],
        }

    def _overlap_bonus(self, query_tokens: list[str], unit: EvidenceUnit) -> float:
        haystack = f"{unit.type} {unit.source_file} {unit.text}".lower()
        matches = sum(1 for token in query_tokens if token and token in haystack)
        return min(0.6, matches * 0.12)

    def _type_bonus(self, intent: dict[str, Any], unit: EvidenceUnit) -> float:
        score = 0.0
        if intent["command_like"] and unit.type == "command":
            score += 0.75
        if intent["query_like"] and unit.type in {"sql", "json_query"}:
            score += 0.75
        if intent["command_like"] and unit.type in {"diagram", "table"}:
            score -= 0.25
        subject_tokens = intent["subject_tokens"]
        haystack = f"{unit.source_file} {unit.text}".lower()
        subject_matches = sum(1 for token in subject_tokens if token in haystack)
        score += min(0.4, subject_matches * 0.15)
        return score

    def _confidence_bonus(self, unit: EvidenceUnit) -> float:
        confidence = unit.signals.get("confidence")
        if confidence == "high":
            return 0.2
        if confidence == "low":
            return -0.25
        flags = unit.signals.get("flags")
        if isinstance(flags, list) and ("weak_boundary" in flags or "suspicious_grouping" in flags):
            return -0.15
        return 0.0


def retrieve_with_rewrites(
    query: str,
    *,
    db_path: str = "evidence_units.db",
    top_k: int = 5,
    expand_neighbors: int = 0,
) -> MergedRetrievalResult:
    """Convenience helper for one-shot rewrite-aware lexical retrieval."""

    client = QueryRewriteClient(db_path=db_path)
    try:
        return client.retrieve_with_rewrites(query, top_k=top_k, expand_neighbors=expand_neighbors)
    finally:
        client.close()


def _normalize_query(query: str) -> str:
    normalized = _NON_WORD_RE.sub(" ", query.lower())
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    return normalized.strip()


def _meaningful_tokens(query: str) -> list[str]:
    seen: set[str] = set()
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(query.lower()):
        cleaned = token.strip("?.!,")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        tokens.append(cleaned)
    return tokens


def _dedupe_strings(values: list[str], *, exclude: set[str] | None = None) -> list[str]:
    exclude = {item.strip().lower() for item in (exclude or set()) if item.strip()}
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        cleaned = _WHITESPACE_RE.sub(" ", value.strip())
        if not cleaned:
            continue
        lower_cleaned = cleaned.lower()
        if lower_cleaned in exclude or lower_cleaned in seen:
            continue
        seen.add(lower_cleaned)
        deduped.append(cleaned)
    return deduped


def _merge_neighbor_units(existing: list[EvidenceUnit], incoming: list[EvidenceUnit]) -> list[EvidenceUnit]:
    seen = {unit.unit_id for unit in existing}
    merged = list(existing)
    for unit in incoming:
        if unit.unit_id in seen:
            continue
        seen.add(unit.unit_id)
        merged.append(unit)
    return merged


def _parse_json_only(raw_text: str) -> Any:
    stripped = raw_text.strip()
    stripped = _JSON_FENCE_RE.sub("", stripped).strip()
    return json.loads(stripped)
