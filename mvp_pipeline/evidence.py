"""Evidence-unit shaping for the MVP evidence pipeline."""

from __future__ import annotations

import re

from canonical_data_model import (
    AnchoredSpan,
    ConfidenceLevel,
    EvidenceUnit,
    ExtractedText,
    ProvenanceLink,
    RecordRef,
    SourceDocument,
    SourceLocator,
    StructuralRegion,
    TextSpan,
    TrustState,
)

from .ids import build_evidence_unit_id

LEAF_REGION_TYPES = {"paragraph_block", "fenced_code_block"}
SQL_START_RE = re.compile(
    r"^(select|insert|update|with|delete\s+from|create\s+table|alter\s+table|drop\s+table|truncate)\b",
    re.IGNORECASE,
)
SQL_CONTINUATION_RE = re.compile(
    r"^(from|where|group\s+by|order\s+by|having|limit|join|left\s+join|right\s+join|inner\s+join|outer\s+join|values|set|and|or|on|returning)\b",
    re.IGNORECASE,
)
FENCE_LANGUAGE_RE = re.compile(r"^(?:`{3,}|~{3,})([A-Za-z0-9_+-]*)")
HTTP_REQUEST_LINE_RE = re.compile(r"^(GET|POST|PUT|DELETE|HEAD|PATCH)\s+(\S+)$", re.IGNORECASE)
COMMAND_PROMPT_RE = re.compile(r"^(?:[$%]|[\w.-]+@[\w.-]+[:~/$._-]*[$#])\s+\S")
COMMAND_PROMPT_PREFIX_RE = re.compile(r"^(?:[$%]\s*|[\w.-]+@[\w.-]+[:~/$._-]*[$#]\s+)")
EXPLANATORY_START_RE = re.compile(
    r"^(run|use|execute|query|sql|command|commands|example|examples|try|note)\b", re.IGNORECASE
)
ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.+$")
SHELL_OPERATOR_RE = re.compile(r"(\|\||&&|2>|>>|[|<>])")
SEPARATOR_LINE_RE = re.compile(r"^(?:[-=_*]){3,}$")
MARKDOWN_TABLE_SEPARATOR_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?$")
UNAMBIGUOUS_COMMAND_HEAD_TOKENS = {
    "aws",
    "awslocal",
    "bash",
    "curl",
    "docker",
    "export",
    "gh",
    "git",
    "helm",
    "jq",
    "keytool",
    "kubectl",
    "node",
    "npm",
    "openssl",
    "pip",
    "pip3",
    "python",
    "python3",
    "scp",
    "sh",
    "sde",
    "ssh",
    "uvicorn",
    "vault",
    "wget",
    "yarn",
    "psql",
}
GENERIC_COMMAND_HEAD_TOKENS = {
    "awk",
    "cat",
    "cd",
    "grep",
    "ls",
    "pwd",
    "sed",
}
COMMAND_SUBCOMMAND_HINT_TOKENS = {
    "add",
    "apply",
    "auth",
    "branch",
    "build",
    "checkout",
    "ci",
    "clone",
    "commit",
    "config",
    "delete",
    "describe",
    "diff",
    "exec",
    "fetch",
    "get",
    "init",
    "install",
    "issue",
    "kv",
    "lint",
    "list",
    "log",
    "login",
    "logs",
    "merge",
    "patch",
    "pr",
    "ps",
    "pull",
    "push",
    "read",
    "repo",
    "rebase",
    "run",
    "s3",
    "start",
    "status",
    "tag",
    "template",
    "test",
    "uninstall",
    "upgrade",
    "write",
}
REST_STYLE_COMMAND_VERBS = {"delete", "get", "head", "patch", "post", "put"}
SHELL_FENCE_LANGUAGES = {"bash", "console", "commandline", "shell", "sh", "terminal", "zsh"}
COMMAND_SPLIT_HEAD_TOKENS = {
    "aws",
    "awslocal",
    "docker",
    "export",
    "git",
    "psql",
    "python",
    "python3",
}
SEARCH_API_ENDPOINT_HINTS = ("_alias", "_aliases", "_msearch", "_search")
PAYLOAD_FLAG_HINTS = {
    "--attributes",
    "--body",
    "--cli-input-json",
    "--data",
    "--data-binary",
    "--data-raw",
    "--document",
    "--payload",
    "--policy",
}
SHELLISH_TAIL_MARKERS = ("/", ".", "~", "-", "|", ">", "<", "=")
MAX_ATTACHED_CONTEXT_LINES = 3
MAX_ATTACHED_CONTEXT_LINE_LENGTH = 100


def build_evidence_unit_index(evidence_units: list[EvidenceUnit]) -> dict[str, EvidenceUnit]:
    return {unit.evidence_unit_id: unit for unit in evidence_units}


def _walk_adjacent_units(
    start_id: str | None,
    units_by_id: dict[str, EvidenceUnit],
    direction: str,
    limit: int,
) -> list[EvidenceUnit]:
    neighbors: list[EvidenceUnit] = []
    next_id = start_id
    while next_id is not None and len(neighbors) < limit:
        neighbor = units_by_id.get(next_id)
        if neighbor is None:
            break
        neighbors.append(neighbor)
        if direction == "previous":
            next_id = neighbor.previous_unit_id
        else:
            next_id = neighbor.next_unit_id
    return neighbors


def collect_neighbor_units(
    unit: EvidenceUnit,
    units_by_id: dict[str, EvidenceUnit],
    k: int,
) -> list[EvidenceUnit]:
    if k < 0:
        raise ValueError("k must be >= 0")
    previous_units = _walk_adjacent_units(unit.previous_unit_id, units_by_id, "previous", k)
    next_units = _walk_adjacent_units(unit.next_unit_id, units_by_id, "next", k)
    return list(reversed(previous_units)) + next_units


def expand_unit_context(
    unit: EvidenceUnit,
    units_by_id: dict[str, EvidenceUnit],
    n_units: int,
) -> list[EvidenceUnit]:
    if n_units < 0:
        raise ValueError("n_units must be >= 0")
    previous_units = _walk_adjacent_units(unit.previous_unit_id, units_by_id, "previous", n_units)
    next_units = _walk_adjacent_units(unit.next_unit_id, units_by_id, "next", n_units)
    return list(reversed(previous_units)) + [unit] + next_units


def _sort_key(region: StructuralRegion) -> tuple[int, int, int]:
    text_span = region.primary_span.text_span
    start = text_span.start if text_span else 0
    end = text_span.end if text_span else 0
    return start, end, region.ordinal


def _ancestor_ids(
    region: StructuralRegion,
    regions_by_id: dict[str, StructuralRegion],
) -> list[str]:
    ordered_ids = [region.structural_region_id]
    parent_region_id = region.parent_region_id
    while parent_region_id is not None:
        ordered_ids.append(parent_region_id)
        parent_region_id = regions_by_id[parent_region_id].parent_region_id
    return ordered_ids


def _children_by_parent(regions: list[StructuralRegion]) -> dict[str, list[StructuralRegion]]:
    children: dict[str, list[StructuralRegion]] = {}
    for region in regions:
        if region.parent_region_id is None:
            continue
        children.setdefault(region.parent_region_id, []).append(region)
    for parent_id in children:
        children[parent_id] = sorted(children[parent_id], key=_sort_key)
    return children


def _combined_span(source_uri: str, regions: list[StructuralRegion]) -> AnchoredSpan:
    first_span = regions[0].primary_span
    last_span = regions[-1].primary_span
    first_text_span = first_span.text_span
    last_text_span = last_span.text_span
    if first_text_span is None or last_text_span is None:
        raise ValueError("MVP evidence shaping requires structural regions with text spans")
    return AnchoredSpan(
        locator=SourceLocator(
            source_uri=source_uri,
            line_start=first_span.locator.line_start,
            line_end=last_span.locator.line_end,
            char_start=first_span.locator.char_start,
            char_end=last_span.locator.char_end,
        ),
        text_span=TextSpan(first_text_span.start, last_text_span.end),
    )


def _flatten_structural_ids(
    primary_region: StructuralRegion,
    extra_regions: list[StructuralRegion],
    regions_by_id: dict[str, StructuralRegion],
) -> list[str]:
    ordered_ids = [primary_region.structural_region_id]
    for region in extra_regions:
        if region.structural_region_id not in ordered_ids:
            ordered_ids.append(region.structural_region_id)
    parent_region_id = primary_region.parent_region_id
    while parent_region_id is not None:
        if parent_region_id not in ordered_ids:
            ordered_ids.append(parent_region_id)
        parent_region_id = regions_by_id[parent_region_id].parent_region_id
    return ordered_ids


def _classify_base_unit_type(regions: list[StructuralRegion]) -> str:
    region_types = {region.region_type for region in regions}
    content_facets = {facet for region in regions for facet in region.content_facets}
    if "section_anchor" in region_types and "paragraph_block" in region_types:
        return "heading_section"
    if region_types == {"fenced_code_block"}:
        return "code"
    if len(content_facets) > 1:
        return "mixed"
    return "prose"


def _detection_lines(
    normalized: ExtractedText,
    regions: list[StructuralRegion],
) -> list[str]:
    detection_regions = [region for region in regions if region.region_type != "section_anchor"] or regions
    lines: list[str] = []
    for region in detection_regions:
        text_span = region.primary_span.text_span
        if text_span is None:
            continue
        region_text = normalized.text[text_span.start : text_span.end]
        for raw_line in region_text.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            if region.region_type == "fenced_code_block" and (stripped.startswith("```") or stripped.startswith("~~~")):
                continue
            lines.append(stripped)
    return lines


def _region_text(normalized: ExtractedText, region: StructuralRegion) -> str:
    text_span = region.primary_span.text_span
    if text_span is None:
        return ""
    return normalized.text[text_span.start : text_span.end]


def _region_nonblank_lines(normalized: ExtractedText, region: StructuralRegion) -> list[str]:
    return [line.strip() for line in _region_text(normalized, region).splitlines() if line.strip()]


def _fenced_code_language(normalized: ExtractedText, regions: list[StructuralRegion]) -> str | None:
    for region in regions:
        if region.region_type != "fenced_code_block":
            continue
        region_text = _region_text(normalized, region)
        if not region_text:
            continue
        first_line = region_text.splitlines()[0].strip()
        match = FENCE_LANGUAGE_RE.match(first_line)
        if match is None:
            continue
        language = match.group(1).strip().lower()
        return language or None
    return None


def _is_markdown_table_separator(line: str) -> bool:
    return MARKDOWN_TABLE_SEPARATOR_RE.match(line.strip()) is not None


def _is_markdown_table_row(line: str) -> bool:
    stripped = line.strip()
    if not stripped or _is_markdown_table_separator(stripped):
        return False
    if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2:
        return True
    return stripped.count(" | ") >= 1 and stripped.count("|") >= 2


def _is_markdown_table(lines: list[str]) -> bool:
    if len(lines) < 2:
        return False
    if not _is_markdown_table_row(lines[0]):
        return False
    if not any(_is_markdown_table_separator(line) for line in lines[1:]):
        return False
    tableish_line_count = sum(1 for line in lines if _is_markdown_table_row(line) or _is_markdown_table_separator(line))
    return tableish_line_count >= max(2, len(lines) - 1)


def _strip_command_prompt(line: str) -> str:
    return COMMAND_PROMPT_PREFIX_RE.sub("", line, count=1).strip()


def _normalized_command_head(token: str) -> str:
    normalized = token.strip().rstrip(":")
    if not normalized:
        return ""
    normalized = normalized.rsplit("/", 1)[-1]
    return normalized.lower()


def _strip_env_assignments(tokens: list[str]) -> tuple[list[str], bool]:
    token_index = 0
    while token_index < len(tokens) and ENV_ASSIGNMENT_RE.match(tokens[token_index]):
        token_index += 1
    return tokens[token_index:], token_index > 0


def _is_rest_style_command(tokens: list[str]) -> bool:
    if len(tokens) < 2:
        return False
    if tokens[0].lower() not in REST_STYLE_COMMAND_VERBS:
        return False
    second_token = tokens[1]
    if second_token.lower() == "from":
        return False
    return second_token.startswith(("/", "_", "*", ".")) or "/" in second_token


def _has_command_tail_signal(tokens: list[str]) -> bool:
    for token in tokens[1:]:
        if token.startswith("-"):
            return True
        if token.startswith(("./", "../", "~", "/", "_", "*")):
            return True
        if any(marker in token for marker in ("/", ":", "=", ".", "@")):
            return True
    return False


def _is_jsonish_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return (
        stripped.startswith(("{", "[", '"'))
        or stripped in {"}", "]", "},", "],"}
        or stripped.endswith(("{", "["))
        or any(key in stripped.lower() for key in ('"query"', '"aggs"', '"aggregations"', '"filter"'))
    )


def _http_search_request_match(line: str) -> tuple[str, str] | None:
    match = HTTP_REQUEST_LINE_RE.match(line.strip())
    if match is None:
        return None
    method = match.group(1).upper()
    path = match.group(2)
    lowered_path = path.lower()
    if any(hint in lowered_path for hint in SEARCH_API_ENDPOINT_HINTS):
        return method, path
    return None


def _json_search_strength(lines: list[str]) -> int:
    if not lines:
        return 0

    request_match = _http_search_request_match(lines[0])
    if request_match is None:
        return 0

    score = 3
    for line in lines[1:]:
        if _is_jsonish_line(line):
            score += 1
        lowered = line.lower()
        if any(token in lowered for token in ('"query"', '"aggs"', '"aggregations"', '"aliases"')):
            score += 1
    return score


def _is_json_search_candidate(lines: list[str]) -> bool:
    return _json_search_strength(lines) >= 3


def _command_line_score(line: str) -> int:
    stripped = line.strip()
    if not stripped:
        return 0
    if _is_markdown_table_separator(stripped) or _is_markdown_table_row(stripped):
        return 0

    score = 0
    command_text = stripped
    if COMMAND_PROMPT_RE.match(stripped):
        score += 3
        command_text = _strip_command_prompt(stripped)

    if SHELL_OPERATOR_RE.search(command_text) or command_text.endswith("\\"):
        score += 2

    tokens = command_text.split()
    if not tokens:
        return score

    tokens, had_assignments = _strip_env_assignments(tokens)
    if had_assignments and tokens:
        score += 2
    if not tokens:
        return score

    first_token = tokens[0]
    first_lower = first_token.lower()
    normalized_head = _normalized_command_head(first_token)
    second_lower = tokens[1].lower() if len(tokens) > 1 else ""

    if _is_rest_style_command(tokens):
        score += 3
    elif normalized_head in UNAMBIGUOUS_COMMAND_HEAD_TOKENS:
        score += 2
        if len(tokens) >= 2:
            score += 1
        if "/" in first_token or first_token.startswith((".", "~")):
            score += 1
    elif normalized_head in GENERIC_COMMAND_HEAD_TOKENS:
        score += 2

    if second_lower in COMMAND_SUBCOMMAND_HINT_TOKENS:
        score += 1
    if any(token.startswith("--") for token in tokens[1:]):
        score += 1
    if _has_command_tail_signal(tokens):
        score += 1
    if len(tokens) == 1 and first_lower in GENERIC_COMMAND_HEAD_TOKENS:
        score += 1
    if stripped.endswith((".", "!", "?")) and score < 5:
        score -= 1
    if len(tokens) > 10 and not SHELL_OPERATOR_RE.search(command_text):
        score -= 1

    return max(score, 0)


def _command_strength(lines: list[str]) -> int:
    if not lines:
        return 0
    if _is_json_search_candidate(lines):
        return 0
    line_scores = [_command_line_score(line) for line in lines]
    strong_line_count = sum(score >= 3 for score in line_scores)
    if strong_line_count == 0:
        return 0
    return max(line_scores) + strong_line_count - 1


def _sql_line_score(line: str, *, is_first_line: bool) -> int:
    stripped = line.strip()
    if not stripped:
        return 0

    score = 0
    if is_first_line and SQL_START_RE.match(stripped):
        score += 3
    elif not is_first_line and SQL_CONTINUATION_RE.match(stripped):
        score += 1

    lowered = stripped.lower()
    if any(keyword in lowered for keyword in (" from ", " where ", " values ", " set ", " join ", " returning ")):
        score += 1
    if stripped.endswith(";"):
        score += 1
    return score


def _sql_strength(lines: list[str]) -> int:
    if not lines:
        return 0

    first_line_score = _sql_line_score(lines[0], is_first_line=True)
    if first_line_score < 3:
        return 0

    continuation_score = sum(_sql_line_score(line, is_first_line=False) for line in lines[1:])
    return first_line_score + continuation_score


def _is_sql_candidate(lines: list[str]) -> bool:
    return _sql_strength(lines) >= 3


def _is_command_candidate(lines: list[str]) -> bool:
    return _command_strength(lines) >= 3


def _specialize_unit_type(
    normalized: ExtractedText,
    regions: list[StructuralRegion],
    base_unit_type: str,
) -> tuple[str, bool, bool, bool, bool, bool, int, int, int]:
    detection_lines = _detection_lines(normalized, regions)
    fence_language = _fenced_code_language(normalized, regions)
    table_candidate = _is_markdown_table(detection_lines)
    mermaid_block = fence_language == "mermaid"
    json_query_strength = _json_search_strength(detection_lines)
    json_query_candidate = json_query_strength >= 3
    sql_strength = _sql_strength(detection_lines)
    command_strength = _command_strength(detection_lines)
    sql_candidate = sql_strength >= 3
    command_candidate = command_strength >= 3

    if mermaid_block:
        return (
            "diagram",
            False,
            False,
            False,
            table_candidate,
            True,
            sql_strength,
            command_strength,
            json_query_strength,
        )
    if table_candidate:
        return (
            "table",
            False,
            False,
            False,
            True,
            False,
            sql_strength,
            command_strength,
            json_query_strength,
        )
    if json_query_candidate:
        return (
            "json_query",
            False,
            False,
            True,
            False,
            False,
            sql_strength,
            command_strength,
            json_query_strength,
        )
    if fence_language in SHELL_FENCE_LANGUAGES and command_strength >= 2:
        return (
            "command",
            sql_candidate,
            True,
            False,
            False,
            False,
            sql_strength,
            command_strength,
            json_query_strength,
        )
    if sql_strength > command_strength and sql_candidate:
        return (
            "sql",
            sql_candidate,
            command_candidate,
            False,
            False,
            False,
            sql_strength,
            command_strength,
            json_query_strength,
        )
    if command_candidate:
        return (
            "command",
            sql_candidate,
            command_candidate,
            False,
            False,
            False,
            sql_strength,
            command_strength,
            json_query_strength,
        )
    return (
        base_unit_type,
        sql_candidate,
        command_candidate,
        False,
        False,
        False,
        sql_strength,
        command_strength,
        json_query_strength,
    )


def _is_explanatory_prose(lines: list[str]) -> bool:
    if not lines:
        return False
    if any(line.endswith(":") for line in lines):
        return True
    joined = " ".join(lines).lower()
    if any(keyword in joined for keyword in (" command", " commands", " query", " sql", "run ", "execute ")):
        return True
    return any(EXPLANATORY_START_RE.match(line) for line in lines)


def _find_attached_context_region(
    normalized: ExtractedText,
    region: StructuralRegion,
    regions_by_id: dict[str, StructuralRegion],
) -> StructuralRegion | None:
    if region.prev_region_id is None:
        return None

    candidate = regions_by_id.get(region.prev_region_id)
    if candidate is None or candidate.region_type != "paragraph_block":
        return None

    candidate_lines = _region_nonblank_lines(normalized, candidate)
    if not 1 <= len(candidate_lines) <= MAX_ATTACHED_CONTEXT_LINES:
        return None
    if any(len(line) > MAX_ATTACHED_CONTEXT_LINE_LENGTH for line in candidate_lines):
        return None
    if _is_command_candidate(candidate_lines) or _is_sql_candidate(candidate_lines):
        return None
    if not _is_explanatory_prose(candidate_lines):
        return None
    return candidate


def _build_signals(
    unit_type: str,
    regions: list[StructuralRegion],
    primary_region: StructuralRegion,
    *,
    sql_candidate: bool,
    command_candidate: bool,
    opensearch_candidate: bool,
    table_candidate: bool,
    mermaid_block: bool,
    attached_context_above: bool,
) -> list[str]:
    signals: list[str] = []
    region_types = {region.region_type for region in regions}
    if "section_anchor" in region_types:
        signals.append("has_heading")
    if "paragraph_block" in region_types:
        signals.append("has_paragraph_body")
    if "fenced_code_block" in region_types:
        signals.append("has_fenced_code_block")
    if len(regions) > 1:
        signals.append("multi_region_unit")
    else:
        signals.append("single_region_unit")
    if primary_region.heading_path:
        signals.append("has_heading_context")
    if command_candidate:
        signals.append("command_candidate")
    if sql_candidate:
        signals.append("sql_candidate")
    if opensearch_candidate:
        signals.append("json_query_candidate")
    if table_candidate:
        signals.append("table_candidate")
    if mermaid_block:
        signals.append("mermaid_block")
    if attached_context_above:
        signals.append("attached_context_above")
    signals.append(f"unit_type:{unit_type}")
    return signals


def _line_modes(lines: list[str]) -> set[str]:
    modes: set[str] = set()
    if _is_markdown_table(lines):
        modes.add("table")
        return modes
    if _is_json_search_candidate(lines):
        modes.add("json_query")
        return modes
    for line in lines:
        if line.strip().startswith("#"):
            modes.add("heading")
            continue
        if SEPARATOR_LINE_RE.match(line.strip()):
            modes.add("separator")
            continue
        if _http_search_request_match(line) is not None or _is_jsonish_line(line):
            modes.add("json_query")
            continue
        if _command_line_score(line) >= 3:
            modes.add("command")
            continue
        if _sql_line_score(line, is_first_line=False) >= 1 or _sql_line_score(line, is_first_line=True) >= 3:
            modes.add("sql")
            continue
        modes.add("prose")
    return modes


def _payload_flags(lines: list[str]) -> list[str]:
    flags: list[str] = []
    has_embedded_payload = False
    has_oversized_inline_payload = False

    for line in lines:
        stripped = line.strip()
        lowered = stripped.lower()

        if len(stripped) >= 120 and (
            ("{" in stripped and "}" in stripped and ":" in stripped)
            or any(token in lowered for token in ("base64", "jq -n", "python -c", "openssl base64"))
        ):
            has_embedded_payload = True

        if len(stripped) >= 180 and any(hint in stripped for hint in PAYLOAD_FLAG_HINTS):
            has_embedded_payload = True

        if len(stripped) >= 220 and (
            ENV_ASSIGNMENT_RE.match(stripped) or any(quote in stripped for quote in ('"', "'")) or "$(" in stripped
        ):
            has_oversized_inline_payload = True

    if has_embedded_payload:
        flags.append("embedded_payload")
    if has_oversized_inline_payload:
        flags.append("oversized_inline_payload")
    return flags


def _lines_for_text_span(normalized: ExtractedText, text_span: TextSpan) -> list[str]:
    return [line.strip() for line in normalized.text[text_span.start : text_span.end].splitlines() if line.strip()]


def _derive_confidence_and_flags(
    *,
    unit_type: str,
    content_facets: list[str],
    ambiguity_count: int,
    signals: list[str],
    lines: list[str],
    sql_strength: int,
    command_strength: int,
    opensearch_strength: int,
) -> tuple[ConfidenceLevel, list[str]]:
    flags: list[str] = []
    signal_set = set(signals)
    modes = _line_modes(lines)
    mixed_modes = ("command" in modes or "sql" in modes) and "prose" in modes
    separator_present = "separator" in modes

    if (
        unit_type == "mixed"
        or len(content_facets) > 1
        or "attached_context_above" in signal_set
        or ("has_heading" in signal_set and ("command_candidate" in signal_set or "sql_candidate" in signal_set))
        or mixed_modes
    ):
        flags.append("mixed_content")

    if ambiguity_count > 0 or "attached_context_above" in signal_set or unit_type == "mixed" or separator_present:
        flags.append("weak_boundary")

    if separator_present or (mixed_modes and "attached_context_above" not in signal_set):
        flags.append("suspicious_grouping")

    if unit_type == "command":
        flags.extend(_payload_flags(lines))

    strong_structural_evidence = False
    if unit_type == "code":
        strong_structural_evidence = "has_fenced_code_block" in signal_set
    elif unit_type == "diagram":
        strong_structural_evidence = "mermaid_block" in signal_set and "has_fenced_code_block" in signal_set
    elif unit_type == "heading_section":
        strong_structural_evidence = (
            "has_heading" in signal_set and "has_paragraph_body" in signal_set and "multi_region_unit" in signal_set
        )
    elif unit_type == "table":
        strong_structural_evidence = "table_candidate" in signal_set
    elif unit_type == "command":
        strong_structural_evidence = command_strength >= 3 and "attached_context_above" not in signal_set
    elif unit_type == "sql":
        strong_structural_evidence = sql_strength >= 4 and "attached_context_above" not in signal_set
    elif unit_type == "json_query":
        strong_structural_evidence = opensearch_strength >= 4 and "attached_context_above" not in signal_set

    if ambiguity_count > 0 or "suspicious_grouping" in flags or unit_type == "mixed":
        return ConfidenceLevel.LOW, flags
    if "oversized_inline_payload" in flags:
        return ConfidenceLevel.LOW, flags
    if "embedded_payload" in flags:
        return ConfidenceLevel.MEDIUM, flags
    if flags:
        return ConfidenceLevel.MEDIUM, flags
    if strong_structural_evidence:
        return ConfidenceLevel.HIGH, flags
    if (
        "has_paragraph_body" in signal_set
        or "single_region_unit" in signal_set
        or any(signal.startswith("unit_type:") for signal in signal_set)
    ):
        return ConfidenceLevel.MEDIUM, flags
    return ConfidenceLevel.LOW, flags


def _iter_region_line_segments(
    normalized: ExtractedText,
    region: StructuralRegion,
) -> list[tuple[str, TextSpan, SourceLocator]]:
    region_span = region.primary_span.text_span
    if region_span is None:
        return []

    segments: list[tuple[str, TextSpan, SourceLocator]] = []
    for line_entry, span_entry in zip(normalized.line_index, normalized.span_map):
        if line_entry.text_span.start < region_span.start:
            continue
        if line_entry.text_span.end > region_span.end:
            continue
        line_text = normalized.text[line_entry.text_span.start : line_entry.text_span.end]
        segments.append((line_text, line_entry.text_span, span_entry.locator))
    return segments


def _anchored_span_from_segments(source_uri: str, segments: list[tuple[str, TextSpan, SourceLocator]]) -> AnchoredSpan:
    first_text, first_span, first_locator = segments[0]
    last_text, last_span, last_locator = segments[-1]
    return AnchoredSpan(
        locator=SourceLocator(
            source_uri=source_uri,
            line_start=first_locator.line_start,
            line_end=last_locator.line_end,
            char_start=first_locator.char_start,
            char_end=last_locator.char_end,
        ),
        text_span=TextSpan(first_span.start, last_span.end),
    )


def _is_split_worthy_command_line(line: str) -> bool:
    stripped = _strip_command_prompt(line.strip())
    if not stripped:
        return False
    tokens = stripped.split()
    tokens, _ = _strip_env_assignments(tokens)
    if not tokens:
        return False
    head = _normalized_command_head(tokens[0])
    return head in COMMAND_SPLIT_HEAD_TOKENS


def _is_command_continuation_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    return (
        stripped.startswith(("{", "[", "}", "]", '"', "'"))
        or stripped.startswith("--")
        or stripped.endswith("\\")
        or _is_jsonish_line(stripped)
    )


def _split_command_segments(
    source_uri: str,
    segments: list[tuple[str, TextSpan, SourceLocator]],
) -> list[AnchoredSpan]:
    split_indices = [index for index, (text, _, _) in enumerate(segments) if _is_split_worthy_command_line(text)]
    if len(split_indices) < 2:
        return []

    first_split_index = split_indices[0]
    if any(text.strip() for text, _, _ in segments[:first_split_index]):
        return []

    for current_index, next_index in zip(split_indices, split_indices[1:]):
        between_segments = segments[current_index + 1 : next_index]
        if any(not _is_command_continuation_line(text) for text, _, _ in between_segments):
            return []

    spans: list[AnchoredSpan] = []
    for position, start_index in enumerate(split_indices):
        end_index = split_indices[position + 1] if position + 1 < len(split_indices) else len(segments)
        group_segments = segments[start_index:end_index]
        if not group_segments:
            continue
        spans.append(_anchored_span_from_segments(source_uri, group_segments))
    return spans


def _split_json_query_segments(
    source_uri: str,
    segments: list[tuple[str, TextSpan, SourceLocator]],
) -> list[AnchoredSpan]:
    request_indices = [
        index for index, (text, _, _) in enumerate(segments) if _http_search_request_match(text) is not None
    ]
    if len(request_indices) < 2:
        return []

    first_request_index = request_indices[0]
    if any(text.strip() for text, _, _ in segments[:first_request_index]):
        return []

    spans: list[AnchoredSpan] = []
    for position, start_index in enumerate(request_indices):
        end_index = request_indices[position + 1] if position + 1 < len(request_indices) else len(segments)
        group_segments = segments[start_index:end_index]
        if not group_segments:
            continue
        spans.append(_anchored_span_from_segments(source_uri, group_segments))
    return spans


def _split_leaf_region_spans(
    source_uri: str,
    normalized: ExtractedText,
    leaf_region: StructuralRegion,
    *,
    unit_type: str,
) -> list[AnchoredSpan]:
    segments = _iter_region_line_segments(normalized, leaf_region)
    if len(segments) < 2:
        return []
    if unit_type == "command":
        return _split_command_segments(source_uri, segments)
    if unit_type == "json_query":
        return _split_json_query_segments(source_uri, segments)
    return []


def build_evidence_units(
    source: SourceDocument,
    normalized: ExtractedText,
    regions: list[StructuralRegion],
) -> list[EvidenceUnit]:
    regions_by_id = {region.structural_region_id: region for region in regions}
    children_by_parent = _children_by_parent(regions)
    leaf_regions = sorted([region for region in regions if region.region_type in LEAF_REGION_TYPES], key=_sort_key)

    planned_units: list[
        tuple[
            StructuralRegion,
            list[StructuralRegion],
            AnchoredSpan,
            str,
            list[str],
            StructuralRegion | None,
            bool,
            bool,
            bool,
            int,
            int,
            int,
        ]
    ] = []
    consumed_region_ids: set[str] = set()

    for region in leaf_regions:
        if region.structural_region_id in consumed_region_ids:
            continue

        combined_regions = [region]
        primary_region = region
        parent_region_id = region.parent_region_id

        if (
            region.region_type == "paragraph_block"
            and parent_region_id is not None
            and parent_region_id in regions_by_id
            and regions_by_id[parent_region_id].region_type == "section_anchor"
        ):
            parent_region = regions_by_id[parent_region_id]
            siblings = children_by_parent.get(parent_region_id, [])
            if siblings and siblings[0].structural_region_id == region.structural_region_id:
                combined_regions = [parent_region, region]
                primary_region = parent_region
                consumed_region_ids.add(parent_region.structural_region_id)

        combined_regions = sorted(combined_regions, key=_sort_key)
        base_unit_type = _classify_base_unit_type(combined_regions)
        (
            unit_type,
            sql_candidate,
            command_candidate,
            opensearch_candidate,
            table_candidate,
            mermaid_block,
            sql_strength,
            command_strength,
            opensearch_strength,
        ) = _specialize_unit_type(
            normalized,
            combined_regions,
            base_unit_type,
        )
        attached_context_region = None
        if unit_type in {"command", "sql"}:
            attached_context_region = _find_attached_context_region(normalized, region, regions_by_id)
            if attached_context_region is not None:
                combined_regions = sorted([attached_context_region, *combined_regions], key=_sort_key)
        combined_spans = [_combined_span(source.source_uri, combined_regions)]
        if attached_context_region is None and primary_region is region:
            split_spans = _split_leaf_region_spans(
                source.source_uri,
                normalized,
                region,
                unit_type=unit_type,
            )
            if split_spans:
                combined_spans = split_spans

        for span_index, combined_span in enumerate(combined_spans):
            signals = _build_signals(
                unit_type,
                combined_regions,
                region,
                sql_candidate=sql_candidate,
                command_candidate=command_candidate,
                opensearch_candidate=opensearch_candidate,
                table_candidate=table_candidate,
                mermaid_block=mermaid_block,
                attached_context_above=(attached_context_region is not None and span_index == 0),
            )
            planned_units.append(
                (
                    primary_region,
                    combined_regions,
                    combined_span,
                    unit_type,
                    signals,
                    attached_context_region if span_index == 0 else None,
                    opensearch_candidate,
                    table_candidate,
                    mermaid_block,
                    sql_strength,
                    command_strength,
                    opensearch_strength,
                )
            )
        consumed_region_ids.add(region.structural_region_id)

    evidence_ids = [
        build_evidence_unit_id(source.source_snapshot_id, unit_type, combined_span.locator)
        for _, _, combined_span, unit_type, _, _, _, _, _, _, _, _ in planned_units
    ]

    evidence_units: list[EvidenceUnit] = []
    for index, (
        primary_region,
        combined_regions,
        combined_span,
        unit_type,
        signals,
        attached_context_region,
        _opensearch_candidate,
        _table_candidate,
        _mermaid_block,
        sql_strength,
        command_strength,
        opensearch_strength,
    ) in enumerate(planned_units):
        text_span = combined_span.text_span
        if text_span is None:
            continue
        leaf_region = combined_regions[-1]
        content_facets = sorted({facet for region in combined_regions for facet in region.content_facets})
        ambiguity = [marker for region in combined_regions for marker in region.ambiguity]
        unit_lines = _lines_for_text_span(normalized, text_span)
        confidence, flags = _derive_confidence_and_flags(
            unit_type=unit_type,
            content_facets=content_facets,
            ambiguity_count=len(ambiguity),
            signals=signals,
            lines=unit_lines,
            sql_strength=sql_strength,
            command_strength=command_strength,
            opensearch_strength=opensearch_strength,
        )

        support_links = [
            ProvenanceLink(
                source_document_id=source.source_document_id,
                source_snapshot_id=source.source_snapshot_id,
                locator=combined_span.locator,
                role="primary_support",
                recoverable=True,
                upstream_entity_refs=[
                    RecordRef("StructuralRegion", region.structural_region_id) for region in combined_regions
                ],
                text_span=text_span,
            )
        ]
        if attached_context_region is not None and attached_context_region.primary_span.text_span is not None:
            support_links.append(
                ProvenanceLink(
                    source_document_id=source.source_document_id,
                    source_snapshot_id=source.source_snapshot_id,
                    locator=attached_context_region.primary_span.locator,
                    role="attached_context",
                    recoverable=True,
                    upstream_entity_refs=[RecordRef("StructuralRegion", attached_context_region.structural_region_id)],
                    text_span=attached_context_region.primary_span.text_span,
                )
            )

        evidence_units.append(
            EvidenceUnit(
                evidence_unit_id=evidence_ids[index],
                source_document_id=source.source_document_id,
                source_snapshot_id=source.source_snapshot_id,
                unit_type=unit_type,
                canonical_text=normalized.text[text_span.start : text_span.end],
                support_links=support_links,
                structural_region_ids=_flatten_structural_ids(
                    primary_region=leaf_region,
                    extra_regions=combined_regions[:-1],
                    regions_by_id=regions_by_id,
                ),
                ordinal=index,
                boundary_rationale=(
                    "Attached short explanatory prose immediately above the command or SQL evidence."
                    if attached_context_region is not None
                    else (
                        "Combined a section heading with its immediate paragraph body."
                        if unit_type == "heading_section"
                        else "Derived directly from a structural region in the MVP pipeline."
                    )
                ),
                content_facets=content_facets,
                trust_state=TrustState.SEGMENTED,
                confidence=confidence,
                parent_region_id=primary_region.parent_region_id,
                prev_evidence_unit_id=evidence_ids[index - 1] if index > 0 else None,
                next_evidence_unit_id=evidence_ids[index + 1] if index + 1 < len(evidence_ids) else None,
                context_labels=list(leaf_region.heading_path),
                ambiguity=ambiguity,
                signals=signals,
                flags=flags,
                integrity_flags=list(flags),
            )
        )

    return evidence_units
