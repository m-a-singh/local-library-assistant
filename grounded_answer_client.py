"""Step 13 grounded answer synthesis over lexical EvidenceUnit retrieval.

This module keeps Step 12 lexical retrieval as the grounding source of truth.
It retrieves EvidenceUnits from the SQLite store, sends only those retrieved
units to a local LiteLLM proxy, and returns a concise grounded answer with
citations derived from the underlying evidence units.
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
from pathlib import Path
from typing import Any

from query_retriever import EvidenceUnit, EvidenceUnitIndex, RetrievalResult

LOGGER = logging.getLogger(__name__)
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)
LOGGER.setLevel(
    logging.DEBUG if os.getenv("LOCAL_LIBRARY_ASSISTANT_DEBUG") else logging.CRITICAL
)

TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+")
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "do",
    "does",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "please",
    "the",
    "this",
    "to",
    "what",
    "with",
}
INSUFFICIENT_PATTERNS = (
    "insufficient evidence",
    "evidence is insufficient",
    "evidence provided is insufficient",
    "evidence required to answer",
    "not enough evidence",
    "cannot answer from the provided evidence",
)


class GroundedAnswerError(Exception):
    """Base error for grounded answer synthesis."""


class GroundedAnswerServiceError(GroundedAnswerError):
    """Raised when the local LiteLLM proxy cannot produce an answer."""


class GroundedAnswerJSONError(GroundedAnswerError):
    """Raised when the answer model repeatedly fails to return valid JSON."""


@dataclass
class EvidenceCitation:
    unit_id: str
    source_file: str
    line_start: int | None = None
    line_end: int | None = None
    region_summary: str | None = None


@dataclass
class GroundedAnswer:
    query: str
    answer: str
    citations: list[EvidenceCitation] = field(default_factory=list)
    used_unit_ids: list[str] = field(default_factory=list)
    retrieval_summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class GroundedAnswerRetryPolicy:
    max_attempts: int = 2
    backoff_seconds: float = 0.5


@dataclass
class GroundedAnswerConfig:
    ollama_base_url: str = "http://localhost:4000"
    litellm_api_key: str = field(
        default_factory=lambda: os.getenv("LITELLM_PROXY_API_KEY", "local-dev-key")
    )
    # Model id/tag for the upstream chat-completions provider (LiteLLM/Ollama/etc).
    # Set via env var to avoid leaking internal model naming conventions.
    model: str = field(default_factory=lambda: os.getenv("LLA_ANSWER_MODEL", "llama3:8b"))
    max_tokens: int = 700
    timeout_seconds: float = 120.0
    temperature: float = 0.1
    retry_policy: GroundedAnswerRetryPolicy = field(
        default_factory=GroundedAnswerRetryPolicy
    )
    json_correction_attempts: int = 2
    max_citations: int = 4


def evidence_citation_to_dict(citation: EvidenceCitation) -> dict[str, Any]:
    """Convert an evidence citation into a JSON-safe dictionary."""

    return {
        "unit_id": citation.unit_id,
        "source_file": citation.source_file,
        "line_start": citation.line_start,
        "line_end": citation.line_end,
        "region_summary": citation.region_summary,
    }


def grounded_answer_to_dict(answer: GroundedAnswer) -> dict[str, Any]:
    """Convert a grounded answer into a JSON-safe dictionary."""

    return {
        "query": answer.query,
        "answer": answer.answer,
        "citations": [
            evidence_citation_to_dict(citation) for citation in answer.citations
        ],
        "used_unit_ids": list(answer.used_unit_ids),
        "retrieval_summary": dict(answer.retrieval_summary),
    }


def _line_range_label(citation: EvidenceCitation) -> str | None:
    if citation.line_start is None and citation.line_end is None:
        return None
    if citation.line_start is not None and citation.line_end is not None:
        if citation.line_start == citation.line_end:
            return f"L{citation.line_start}"
        return f"L{citation.line_start}-L{citation.line_end}"
    if citation.line_start is not None:
        return f"L{citation.line_start}"
    return f"L{citation.line_end}"


def format_grounded_answer_text(answer: GroundedAnswer) -> str:
    """Render a grounded answer as human-readable text."""

    lines = ["Answer", answer.answer.strip() or "<empty>", "", "Sources"]
    if not answer.citations:
        lines.append("- <none>")
    else:
        for citation in answer.citations:
            source_name = Path(citation.source_file).name or citation.source_file
            line_label = _line_range_label(citation)
            if line_label:
                lines.append(f"- {source_name}:{line_label} [{citation.unit_id}]")
            elif citation.region_summary:
                lines.append(
                    f"- {source_name} [{citation.region_summary}] [{citation.unit_id}]"
                )
            else:
                lines.append(f"- {source_name} [{citation.unit_id}]")

    support_lines = _support_lines(answer.retrieval_summary)
    if support_lines:
        lines.extend(["", "Support"])
        lines.extend(support_lines)

    return "\n".join(lines)


def _support_lines(summary: dict[str, Any]) -> list[str]:
    """Build a concise support section for grounded answers."""

    if not summary:
        return []

    lines: list[str] = []
    retrieval_mode = summary.get("retrieval_mode")
    if isinstance(retrieval_mode, str) and retrieval_mode:
        lines.append(f"- retrieval: {retrieval_mode}")

    hit_count = summary.get("hit_count")
    context_unit_count = summary.get("context_unit_count")
    if hit_count is not None or context_unit_count is not None:
        hits_label = str(hit_count) if hit_count is not None else "?"
        context_label = (
            str(context_unit_count) if context_unit_count is not None else "?"
        )
        lines.append(f"- evidence: {hits_label} hits, {context_label} context units")

    lexical_hit_count = summary.get("lexical_hit_count")
    semantic_hit_count = summary.get("semantic_hit_count")
    if lexical_hit_count is not None or semantic_hit_count is not None:
        lexical_label = str(lexical_hit_count) if lexical_hit_count is not None else "?"
        semantic_label = (
            str(semantic_hit_count) if semantic_hit_count is not None else "?"
        )
        lines.append(
            f"- retrieval mix: lexical={lexical_label}, semantic={semantic_label}"
        )

    fallback_reason = summary.get("fallback_reason")
    if isinstance(fallback_reason, str) and fallback_reason:
        lines.append(f"- fallback: {fallback_reason}")

    notes = summary.get("notes")
    if isinstance(notes, list):
        concise_notes = [str(note).strip() for note in notes if str(note).strip()]
        if concise_notes:
            lines.append(f"- notes: {', '.join(concise_notes)}")

    return lines


class GroundedAnswerClient:
    """Synthesize grounded answers from Step 12 lexical EvidenceUnit retrieval."""

    def __init__(
        self,
        db_path: str = "evidence_units.db",
        *,
        index: EvidenceUnitIndex | None = None,
        config: GroundedAnswerConfig | None = None,
    ):
        self.db_path = db_path
        self.config = config or GroundedAnswerConfig()
        self.index = index or EvidenceUnitIndex(db_path=db_path)
        self._owns_index = index is None
        self.last_model_used: str | None = None

    def close(self) -> None:
        """Close the underlying Step 12 index when this client owns it."""

        if self._owns_index:
            self.index.close()

    def answer_query(
        self,
        query: str,
        *,
        top_k: int = 5,
        expand_neighbors: int = 1,
    ) -> GroundedAnswer:
        """Retrieve lexical evidence and synthesize a grounded answer."""

        retrieval_result = self.index.retrieve(
            query, top_k=top_k, expand_neighbors=expand_neighbors
        )
        return self.answer_from_retrieval_result(
            query,
            retrieval_result,
            top_k=top_k,
            expand_neighbors=expand_neighbors,
        )

    def answer_from_retrieval_result(
        self,
        query: str,
        retrieval_result: RetrievalResult,
        *,
        top_k: int = 5,
        expand_neighbors: int = 1,
        extra_summary: dict[str, Any] | None = None,
    ) -> GroundedAnswer:
        """Synthesize a grounded answer from an already-computed retrieval result."""

        if not retrieval_result.hits:
            retrieval_summary = self._retrieval_summary(
                retrieval_result,
                context_unit_count=0,
                model_used=None,
                top_k=top_k,
                expand_neighbors=expand_neighbors,
            )
            if extra_summary:
                retrieval_summary.update(extra_summary)
            return GroundedAnswer(
                query=query,
                answer="I couldn't find grounded evidence in the current index to answer that query.",
                citations=[],
                used_unit_ids=[],
                retrieval_summary=retrieval_summary,
            )

        context_units = self._collect_context_units(retrieval_result)
        relevant_units = self._relevant_units(query, context_units)
        units_by_id = {unit.unit_id: unit for unit in context_units}
        raw_response = self._generate_answer_json(query, context_units)
        parsed = self._parse_answer_payload(raw_response, query)

        answer_text = str(parsed.get("answer", "")).strip()
        if not answer_text:
            if relevant_units:
                answer_text = self._best_effort_grounded_answer(relevant_units)
            else:
                answer_text = "I found relevant evidence, but I couldn't produce a concise grounded answer."
        elif self._looks_insufficient_answer(answer_text) and relevant_units:
            answer_text = self._best_effort_grounded_answer(relevant_units)

        used_unit_ids = self._normalize_citation_ids(
            parsed.get("citation_unit_ids"), units_by_id
        )
        if not used_unit_ids:
            if relevant_units:
                used_unit_ids = [
                    unit.unit_id for unit in relevant_units[: self.config.max_citations]
                ]
            else:
                used_unit_ids = [
                    hit.unit.unit_id
                    for hit in retrieval_result.hits[: self.config.max_citations]
                ]

        citations = [
            self._citation_from_unit(units_by_id[unit_id])
            for unit_id in used_unit_ids[: self.config.max_citations]
            if unit_id in units_by_id
        ]

        retrieval_summary = self._retrieval_summary(
            retrieval_result,
            context_unit_count=len(context_units),
            model_used=self.last_model_used,
            top_k=top_k,
            expand_neighbors=expand_neighbors,
        )
        if extra_summary:
            retrieval_summary.update(extra_summary)

        return GroundedAnswer(
            query=query,
            answer=answer_text,
            citations=citations,
            used_unit_ids=used_unit_ids[: self.config.max_citations],
            retrieval_summary=retrieval_summary,
        )

    def _generate_answer_json(
        self, query: str, context_units: list[EvidenceUnit]
    ) -> str:
        """Call the local LiteLLM proxy and require a JSON answer payload."""

        system_prompt = (
            "You are answering a user query using retrieved evidence from local files.\n\n"
            "Your goal is to provide the best possible answer grounded in the provided evidence.\n\n"
            "Rules:\n"
            "- Always try to answer using the provided evidence.\n"
            '- Do NOT default to saying "insufficient evidence" if relevant information exists.\n'
            "- If there is a direct command, return it clearly (use code blocks if appropriate).\n"
            "- If there is a definition, heading, or explanation, summarize it concisely.\n"
            "- Prefer the most relevant and actionable information.\n"
            "- Only say evidence is insufficient if NONE of the provided evidence is relevant to the query.\n"
            "- Do NOT use outside knowledge.\n\n"
            "Be helpful, concise, and grounded in the evidence.\n\n"
            "Return STRICT JSON ONLY with keys: answer (string), citation_unit_ids (array of strings). "
            "Choose citation_unit_ids only from the provided evidence unit ids."
        )
        user_prompt = json.dumps(
            {
                "query": query,
                "evidence_units": [
                    self._prompt_unit_payload(unit) for unit in context_units
                ],
                "citation_requirements": {
                    "max_citations": self.config.max_citations,
                    "prefer_filename_and_line_grounding": True,
                },
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
                parsed = self._parse_json_only(raw)
                if not isinstance(parsed, dict):
                    raise ValueError("grounded answer response must be a JSON object")
                return json.dumps(parsed, ensure_ascii=False)
            except Exception:
                if attempt >= self.config.json_correction_attempts:
                    raise GroundedAnswerJSONError(
                        "Failed to produce valid grounded-answer JSON"
                    )
                correction_prompt = (
                    "Your previous response was invalid. Return JSON only with keys "
                    "answer and citation_unit_ids. Do not include markdown or prose outside JSON.\n"
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
        raise GroundedAnswerJSONError("Failed to produce valid grounded-answer JSON")

    def _call_litellm(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Call the local LiteLLM proxy using the shared project pattern."""

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
                with urllib.request.urlopen(
                    request, timeout=self.config.timeout_seconds
                ) as response:
                    raw_response = response.read().decode("utf-8")
                parsed_response = json.loads(raw_response)
                choices = parsed_response.get("choices", [])
                if not choices:
                    raise GroundedAnswerServiceError(
                        "Grounded answer response did not include choices"
                    )
                message = choices[0].get("message", {})
                content = message.get("content")
                if not isinstance(content, str):
                    raise GroundedAnswerServiceError(
                        "Grounded answer response did not include message content"
                    )
                return content
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                last_exc = GroundedAnswerServiceError(
                    f"Grounded answer service failure: HTTP {exc.code}: {error_body}"
                )
            except Exception as exc:
                last_exc = exc
            if attempt < attempts:
                time.sleep(self.config.retry_policy.backoff_seconds * attempt)
                continue
            if isinstance(last_exc, GroundedAnswerServiceError):
                raise last_exc
            raise GroundedAnswerServiceError(
                f"Grounded answer service failure: {type(last_exc).__name__}: {last_exc}"
            ) from last_exc

        if last_exc is not None:
            raise GroundedAnswerServiceError(
                f"Grounded answer service failure: {type(last_exc).__name__}: {last_exc}"
            ) from last_exc
        raise GroundedAnswerServiceError("Unknown grounded answer failure")

    def _chat_completions_url(self) -> str:
        """Return the OpenAI-compatible LiteLLM proxy endpoint."""

        base = self.config.ollama_base_url.rstrip("/")
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    @staticmethod
    def _parse_json_only(raw_text: str) -> Any:
        stripped = raw_text.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`").strip()
            if stripped.startswith("json"):
                stripped = stripped[4:].strip()
        return json.loads(stripped)

    def _parse_answer_payload(self, raw_text: str, query: str) -> dict[str, Any]:
        """Parse the JSON answer payload returned by the local model."""

        parsed = self._parse_json_only(raw_text)
        if not isinstance(parsed, dict):
            raise GroundedAnswerJSONError(
                "Grounded answer payload must be a JSON object"
            )
        return parsed

    def _relevant_units(
        self, query: str, units: list[EvidenceUnit]
    ) -> list[EvidenceUnit]:
        """Return units with meaningful lexical overlap with the query."""

        if not units:
            return []

        query_tokens = self._query_tokens(query)
        if not query_tokens:
            return list(units)

        scored: list[tuple[int, int, EvidenceUnit]] = []
        for index, unit in enumerate(units):
            score = self._unit_relevance_score(query, query_tokens, unit)
            if score > 0:
                scored.append((score, -index, unit))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [unit for _score, _neg_index, unit in scored]

    def _unit_relevance_score(
        self,
        query: str,
        query_tokens: set[str],
        unit: EvidenceUnit,
    ) -> int:
        searchable_parts = [
            unit.text,
            unit.source_file,
            unit.type,
            " ".join(str(value) for value in unit.signals.values()),
            " ".join(str(value) for value in unit.structural_context.values()),
        ]
        searchable_text = "\n".join(part for part in searchable_parts if part)
        lowered_text = searchable_text.lower()
        unit_tokens = self._query_tokens(searchable_text)

        score = len(query_tokens & unit_tokens) * 3
        stripped_query = query.strip().lower()
        if stripped_query and stripped_query in lowered_text:
            score += 4
        if (
            unit.type in {"command", "sql", "json_query", "code"}
            and query_tokens & unit_tokens
        ):
            score += 1
        return score

    @staticmethod
    def _query_tokens(text: str) -> set[str]:
        tokens: set[str] = set()
        for token in TOKEN_RE.findall(text.lower()):
            if token in STOPWORDS:
                continue
            tokens.add(token)
        return tokens

    @staticmethod
    def _looks_insufficient_answer(answer_text: str) -> bool:
        lowered = answer_text.strip().lower()
        return any(pattern in lowered for pattern in INSUFFICIENT_PATTERNS)

    def _best_effort_grounded_answer(self, relevant_units: list[EvidenceUnit]) -> str:
        """Build a direct answer from the best available evidence when the model is overly conservative."""

        best_unit = relevant_units[0]
        best_text = best_unit.text.strip()
        if best_unit.type in {"command", "sql", "json_query", "code"}:
            language = {
                "command": "sh",
                "sql": "sql",
                "json_query": "http",
                "code": "",
            }.get(best_unit.type, "")
            fence = f"```{language}".rstrip()
            return f"{fence}\n{best_text}\n```"

        lines = [line.strip() for line in best_text.splitlines() if line.strip()]
        if not lines:
            return "I found relevant evidence, but I couldn't extract a concise grounded answer."
        if len(lines) == 1:
            return lines[0]
        if lines[0].startswith("#"):
            heading = lines[0].lstrip("#").strip()
            body = " ".join(lines[1:3]).strip()
            if body:
                return f"{heading}: {body}"
            return heading
        return " ".join(lines[:3]).strip()

    def _collect_context_units(
        self, retrieval_result: RetrievalResult
    ) -> list[EvidenceUnit]:
        """Collect hit units plus optional neighbors in deterministic order."""

        ordered_units: list[EvidenceUnit] = []
        seen_ids: set[str] = set()

        for hit in retrieval_result.hits:
            if hit.unit.unit_id not in seen_ids:
                ordered_units.append(hit.unit)
                seen_ids.add(hit.unit.unit_id)
            for neighbor in retrieval_result.neighbors.get(hit.unit.unit_id, []):
                if neighbor.unit_id in seen_ids:
                    continue
                ordered_units.append(neighbor)
                seen_ids.add(neighbor.unit_id)
        return ordered_units

    def _prompt_unit_payload(self, unit: EvidenceUnit) -> dict[str, Any]:
        """Build a compact but explicit prompt payload for one evidence unit."""

        citation = self._citation_from_unit(unit)
        return {
            "unit_id": unit.unit_id,
            "type": unit.type,
            "source_file": unit.source_file,
            "line_start": citation.line_start,
            "line_end": citation.line_end,
            "region_summary": citation.region_summary,
            "signals": unit.signals,
            "text": unit.text,
        }

    def _normalize_citation_ids(
        self,
        raw_citation_ids: Any,
        units_by_id: dict[str, EvidenceUnit],
    ) -> list[str]:
        """Normalize model-selected citation ids to known evidence unit ids."""

        if not isinstance(raw_citation_ids, list):
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw_citation_ids:
            unit_id = str(item).strip()
            if not unit_id or unit_id in seen or unit_id not in units_by_id:
                continue
            normalized.append(unit_id)
            seen.add(unit_id)
        return normalized

    def _citation_from_unit(self, unit: EvidenceUnit) -> EvidenceCitation:
        """Build a human-facing citation from an indexed EvidenceUnit."""

        line_start = self._int_value(unit.region.get("line_start"))
        line_end = self._int_value(unit.region.get("line_end"))
        region_summary = None
        if line_start is None and line_end is None:
            region_summary = self._region_summary(unit.region)
        return EvidenceCitation(
            unit_id=unit.unit_id,
            source_file=unit.source_file,
            line_start=line_start,
            line_end=line_end,
            region_summary=region_summary,
        )

    def _retrieval_summary(
        self,
        retrieval_result: RetrievalResult,
        *,
        context_unit_count: int,
        model_used: str | None,
        top_k: int,
        expand_neighbors: int,
    ) -> dict[str, Any]:
        """Build machine-readable metadata about the retrieval and answer pass."""

        return {
            "db_path": self.db_path,
            "model": model_used,
            "top_k": top_k,
            "expand_neighbors": expand_neighbors,
            "hit_count": len(retrieval_result.hits),
            "neighbor_group_count": len(retrieval_result.neighbors),
            "context_unit_count": context_unit_count,
        }

    @staticmethod
    def _int_value(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _region_summary(region: dict[str, Any]) -> str | None:
        parent_region_id = region.get("parent_region_id")
        if isinstance(parent_region_id, str) and parent_region_id.strip():
            return f"parent_region_id={parent_region_id}"

        structural_region_ids = region.get("structural_region_ids")
        if isinstance(structural_region_ids, list) and structural_region_ids:
            first_region = str(structural_region_ids[0]).strip()
            if first_region:
                return f"structural_region_id={first_region}"

        text_span_start = region.get("text_span_start")
        text_span_end = region.get("text_span_end")
        if text_span_start is not None or text_span_end is not None:
            return f"text_span={text_span_start}:{text_span_end}"
        return None


def answer_query(
    query: str,
    *,
    db_path: str = "evidence_units.db",
    top_k: int = 5,
    expand_neighbors: int = 1,
) -> GroundedAnswer:
    """Convenience helper for one-shot grounded answering."""

    client = GroundedAnswerClient(db_path=db_path)
    try:
        return client.answer_query(
            query, top_k=top_k, expand_neighbors=expand_neighbors
        )
    finally:
        client.close()
