"""Step 14.1 second-stage LLM reranking over retrieved EvidenceUnit candidates."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from query_retriever import RetrievalHit, RetrievalResult

LOGGER = logging.getLogger(__name__)
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.DEBUG if os.getenv("CANDIDATE_RERANKER_DEBUG") else logging.INFO)


@dataclass
class RerankedCandidate:
    unit_id: str
    rank: int
    rationale: str | None = None


@dataclass
class RerankResult:
    query: str
    original_count: int
    selected_count: int
    reranked_unit_ids: list[str] = field(default_factory=list)
    rationales: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class CandidateRerankerConfig:
    api_base: str = "http://localhost:4000"
    api_key: str = field(default_factory=lambda: os.getenv("LITELLM_PROXY_API_KEY", "local-dev-key"))
    # Model id/tag for the upstream chat-completions provider (LiteLLM/Ollama/etc).
    # Set via env var to avoid leaking internal model naming conventions.
    model: str = field(default_factory=lambda: os.getenv("LLA_RERANK_MODEL", "llama3:8b"))
    timeout_seconds: float = 120.0
    temperature: float = 0.0
    max_tokens: int = 400
    max_candidates: int = 8
    shortlist_size: int = 5
    candidate_text_chars: int = 500
    retry_attempts: int = 2
    retry_backoff_seconds: float = 0.5


def rerank_result_to_dict(result: RerankResult) -> dict[str, Any]:
    """Convert a rerank result into a JSON-safe dictionary."""

    return {
        "query": result.query,
        "original_count": result.original_count,
        "selected_count": result.selected_count,
        "reranked_unit_ids": list(result.reranked_unit_ids),
        "rationales": dict(result.rationales),
        "notes": list(result.notes),
    }


def parse_ranked_unit_ids(raw: str, allowed_unit_ids: set[str]) -> list[str]:
    """Parse one-unit-id-per-line output from the reranker model."""

    ranked_ids: list[str] = []
    seen: set[str] = set()
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line not in allowed_unit_ids or line in seen:
            continue
        seen.add(line)
        ranked_ids.append(line)
    return ranked_ids


class CandidateReranker:
    """LLM reranker over a small retrieved candidate set."""

    def __init__(self, config: CandidateRerankerConfig | None = None):
        self.config = config or CandidateRerankerConfig()
        self.last_model_used: str | None = None

    def rerank(
        self,
        query: str,
        retrieval_result: RetrievalResult,
        *,
        max_candidates: int | None = None,
        shortlist_k: int | None = None,
    ) -> RerankResult:
        """Rerank retrieved candidates and return a shortlisted ordering."""

        candidate_limit = max_candidates if max_candidates is not None else self.config.max_candidates
        selected_limit = shortlist_k if shortlist_k is not None else self.config.shortlist_size

        candidates = retrieval_result.hits[: max(0, candidate_limit)]
        if not candidates:
            return RerankResult(
                query=query,
                original_count=0,
                selected_count=0,
                reranked_unit_ids=[],
                rationales={},
                notes=["no_candidates"],
            )

        selected_limit = min(selected_limit, len(candidates))
        if len(candidates) == 1:
            unit_id = candidates[0].unit.unit_id
            return RerankResult(
                query=query,
                original_count=1,
                selected_count=1,
                reranked_unit_ids=[unit_id],
                rationales={unit_id: "only_candidate"},
                notes=["single_candidate"],
            )

        notes: list[str] = []
        try:
            raw_response = self._generate_rerank_output(query, candidates, selected_limit)
            rerank_result = self._parse_rerank_response(
                query=query,
                raw_text=raw_response,
                candidates=candidates,
                selected_limit=selected_limit,
            )
            if self.last_model_used:
                rerank_result.notes.append(f"model:{self.last_model_used}")
            return rerank_result
        except Exception as exc:
            message = str(exc)
            if "empty content" in message:
                notes.append("rerank_empty_response")
            elif "no valid unit_ids" in message:
                notes.append("rerank_no_valid_unit_ids")
            else:
                notes.append(f"llm_unavailable:{type(exc).__name__}")
            fallback_ids = [hit.unit.unit_id for hit in candidates[:selected_limit]]
            return RerankResult(
                query=query,
                original_count=len(candidates),
                selected_count=len(fallback_ids),
                reranked_unit_ids=fallback_ids,
                rationales={},
                notes=notes,
            )

    def rerank_retrieval_result(
        self,
        query: str,
        retrieval_result: RetrievalResult,
        *,
        max_candidates: int | None = None,
        shortlist_k: int | None = None,
    ) -> tuple[RetrievalResult, RerankResult]:
        """Apply LLM reranking to a RetrievalResult and return a reranked RetrievalResult."""

        rerank_result = self.rerank(
            query,
            retrieval_result,
            max_candidates=max_candidates,
            shortlist_k=shortlist_k,
        )
        hit_by_id = {hit.unit.unit_id: hit for hit in retrieval_result.hits}
        reranked_hits: list[RetrievalHit] = []
        for rank, unit_id in enumerate(rerank_result.reranked_unit_ids, start=1):
            hit = hit_by_id.get(unit_id)
            if hit is None:
                continue
            reranked_hits.append(
                RetrievalHit(
                    unit=hit.unit,
                    score=hit.score,
                    rank=rank,
                )
            )

        neighbors = {
            unit_id: retrieval_result.neighbors.get(unit_id, [])
            for unit_id in rerank_result.reranked_unit_ids
            if unit_id in retrieval_result.neighbors
        }
        return (
            RetrievalResult(
                query=query,
                hits=reranked_hits,
                neighbors=neighbors,
            ),
            rerank_result,
        )

    def _generate_rerank_output(
        self,
        query: str,
        candidates: list[RetrievalHit],
        selected_limit: int,
    ) -> str:
        system_prompt = (
            "You are reranking already-retrieved evidence candidates for a user query.\n\n"
            "Return ONLY unit_ids from the provided candidate list.\n"
            "Return one unit_id per line, in best-first order.\n"
            "Do not output anything else.\n\n"
            "Rules:\n"
            "- Use only provided unit_ids\n"
            "- Prefer candidates that directly answer the query\n"
            "- Prefer commands/facts over vague context\n"
            "- Penalize irrelevant, duplicate, or mixed-content candidates"
        )
        user_prompt = self._build_user_prompt(
            query=query,
            candidates=candidates,
            selected_limit=selected_limit,
        )
        return self._call_litellm(
            model=self.config.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )

    def _parse_rerank_response(
        self,
        *,
        query: str,
        raw_text: str,
        candidates: list[RetrievalHit],
        selected_limit: int,
    ) -> RerankResult:
        candidate_ids = [hit.unit.unit_id for hit in candidates]
        ordered_ids = parse_ranked_unit_ids(raw_text, set(candidate_ids))
        if not ordered_ids:
            raise RuntimeError("rerank response contained no valid unit_ids")

        ordered_ids = ordered_ids[:selected_limit]
        seen = set(ordered_ids)
        if len(ordered_ids) < selected_limit:
            for unit_id in candidate_ids:
                if unit_id in seen:
                    continue
                seen.add(unit_id)
                ordered_ids.append(unit_id)
                if len(ordered_ids) >= selected_limit:
                    break

        return RerankResult(
            query=query,
            original_count=len(candidates),
            selected_count=len(ordered_ids),
            reranked_unit_ids=ordered_ids,
            rationales={},
            notes=[],
        )

    def _build_user_prompt(
        self,
        *,
        query: str,
        candidates: list[RetrievalHit],
        selected_limit: int,
    ) -> str:
        blocks = [
            "Query:",
            query,
            "",
            f"Return up to {selected_limit} unit_ids from this candidate list, one per line.",
            "",
            "Candidates:",
            "",
        ]
        for index, hit in enumerate(candidates, start=1):
            payload = self._candidate_payload(hit)
            blocks.extend(
                [
                    f"{index}.",
                    f"unit_id={payload['unit_id']}",
                    f"type={payload['type']}",
                    f"source={payload['source_file']}",
                    f"lines={payload['lines']}",
                    f"confidence={payload['confidence']}",
                    f"flags={payload['flags']}",
                    f"text={payload['text_excerpt']}",
                    "",
                ]
            )
        return "\n".join(blocks).rstrip()

    def _candidate_payload(self, hit: RetrievalHit) -> dict[str, Any]:
        unit = hit.unit
        region = unit.region if isinstance(unit.region, dict) else {}
        line_start = _safe_int(region.get("line_start"))
        line_end = _safe_int(region.get("line_end"))
        confidence = unit.signals.get("confidence")
        flags = unit.signals.get("flags")
        excerpt = unit.text.strip()
        if len(excerpt) > self.config.candidate_text_chars:
            excerpt = excerpt[: self.config.candidate_text_chars].rstrip() + "..."

        if line_start is None and line_end is None:
            line_range = "unknown"
        elif line_start is not None and line_end is not None:
            line_range = f"{line_start}-{line_end}"
        else:
            line_range = str(line_start if line_start is not None else line_end)

        return {
            "unit_id": unit.unit_id,
            "current_rank": hit.rank,
            "retrieval_score": hit.score,
            "type": unit.type,
            "source_file": Path(unit.source_file).name,
            "line_start": line_start,
            "line_end": line_end,
            "lines": line_range,
            "confidence": str(confidence).strip() if confidence else "unknown",
            "flags": ", ".join(str(flag) for flag in flags) if isinstance(flags, list) and flags else "none",
            "text_excerpt": excerpt,
        }

    def _call_litellm(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        attempts = self.config.retry_attempts
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
                        "Authorization": f"Bearer {self.config.api_key}",
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
                    raise RuntimeError("rerank response did not include choices")
                message = choices[0].get("message", {})
                content = message.get("content")
                if not isinstance(content, str):
                    raise RuntimeError("rerank response did not include message content")
                if not content.strip():
                    raise RuntimeError("rerank response returned empty content")
                return content
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                last_exc = RuntimeError(f"HTTP {exc.code}: {error_body}")
            except Exception as exc:
                last_exc = exc

            if attempt < attempts:
                time.sleep(self.config.retry_backoff_seconds * attempt)
                continue

            raise RuntimeError(f"rerank service failure: {type(last_exc).__name__}: {last_exc}") from last_exc
        raise RuntimeError("rerank service failure: unknown")

    def _chat_completions_url(self) -> str:
        base = self.config.api_base.rstrip("/")
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
