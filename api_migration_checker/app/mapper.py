from __future__ import annotations

import csv
import io
import json
import re
import shlex
from dataclasses import asdict, dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

import yaml


UNSAFE_CURL_TOKENS = ("|", ";", "&&", "`", "$(", ">", "<")


ABBREVIATIONS = {
    "addr": {"address"},
    "amt": {"amount", "total"},
    "acct": {"account"},
    "curr": {"currency"},
    "cust": {"customer"},
    "desc": {"description"},
    "dob": {"date", "birth"},
    "dt": {"date"},
    "fn": {"first", "name"},
    "id": {"id", "identifier"},
    "ln": {"last", "name"},
    "lvl": {"level", "tier"},
    "mob": {"mobile", "phone"},
    "nm": {"name"},
    "qty": {"quantity", "count"},
    "sku": {"sku", "product", "code"},
    "tel": {"phone", "telephone"},
    "ts": {"timestamp"},
    "usr": {"user"},
}

SYNONYMS = {
    "birth_date": {"birthdate", "dob", "date_birth", "date_of_birth"},
    "city": {"city_name"},
    "created_at": {"created_date", "order_date", "placed_date"},
    "customer_name": {"display_name", "full_name", "name"},
    "member_level": {"level", "tier", "tier_name"},
    "phone": {"mobile", "tel", "telephone"},
    "points": {"points_balance", "reward_points"},
    "price": {"amount", "unit_price"},
    "title": {"name", "product_name"},
    "total_amount": {"amount", "grand_total", "total"},
}

DATE_FORMATS = [
    ("yyyy-MM-dd", "%Y-%m-%d"),
    ("dd/MM/yyyy", "%d/%m/%Y"),
    ("yyyy/MM/dd", "%Y/%m/%d"),
    ("dd-MM-yyyy", "%d-%m-%Y"),
    ("MM/dd/yyyy", "%m/%d/%Y"),
]


@dataclass(frozen=True)
class FieldCandidate:
    api_name: str
    path: str
    value: Any
    tokens: list[str]
    expanded_tokens: list[str]
    key: str


@dataclass(frozen=True)
class MappingSuggestion:
    source_api: str
    source_field: str
    source_value: Any
    target_api: str
    target_field: str
    target_value: Any
    compare_type: str
    confidence: str
    score: int
    reason: str
    tolerance: float | None = None
    source_format: str | None = None
    target_format: str | None = None
    contains_direction: str | None = None


def parse_api_specs(raw: str) -> list[dict[str, Any]]:
    data = yaml.safe_load(raw or "[]")
    if not isinstance(data, list):
        raise ValueError("API specs must be a YAML/JSON list")
    for index, api in enumerate(data):
        if not isinstance(api, dict):
            raise ValueError(f"API spec at index {index} must be an object")
        if not api.get("name") or not api.get("url") or not api.get("response"):
            raise ValueError(f"API spec at index {index} requires name, url, and response")
    return data


def parse_json_response_mode(metadata_raw: str, responses_raw: str) -> list[dict[str, Any]]:
    metadata = yaml.safe_load(metadata_raw or "[]")
    responses = yaml.safe_load(responses_raw or "{}")
    if not isinstance(metadata, list):
        raise ValueError("API metadata must be a YAML/JSON list")
    if not isinstance(responses, dict):
        raise ValueError("API responses must be a JSON/YAML object keyed by API name")
    apis: list[dict[str, Any]] = []
    for index, api in enumerate(metadata):
        if not isinstance(api, dict):
            raise ValueError(f"API metadata at index {index} must be an object")
        name = api.get("name")
        if not name or not api.get("url"):
            raise ValueError(f"API metadata at index {index} requires name and url")
        if name not in responses:
            raise ValueError(f"Missing pasted response for API: {name}")
        merged = dict(api)
        merged["response"] = responses[name]
        apis.append(merged)
    return apis


def parse_curl_specs(raw: str) -> list[dict[str, Any]]:
    specs = yaml.safe_load(raw or "[]")
    if not isinstance(specs, list):
        raise ValueError("curl specs must be a YAML list")
    parsed: list[dict[str, Any]] = []
    for index, spec in enumerate(specs):
        if not isinstance(spec, dict) or not spec.get("name") or not spec.get("curl"):
            raise ValueError(f"curl spec at index {index} requires name and curl")
        request_data = parse_safe_curl(spec["curl"])
        request_data["name"] = spec["name"]
        sanitized_headers, sanitized_cookies = sanitize_request_secrets(
            request_data.get("execution_headers") or {},
            request_data.get("execution_cookies") or {},
            spec["name"],
        )
        request_data["headers"] = sanitized_headers
        request_data["cookies"] = sanitized_cookies
        request_data["url"] = spec.get("url_template") or request_data["url"]
        request_data["execution_url"] = request_data.pop("execution_url", request_data["url"])
        request_data["timeout_seconds"] = int(spec.get("timeout_seconds", 10))
        request_data["retry_count"] = int(spec.get("retry_count", 1))
        parsed.append(request_data)
    return parsed


def parse_safe_curl(raw: str) -> dict[str, Any]:
    compact = raw.replace("\\\n", " ").strip()
    if any(token in compact for token in UNSAFE_CURL_TOKENS):
        raise ValueError("Unsafe curl syntax rejected. Pipes, chaining, subshells, and redirects are not supported.")
    parts = shlex.split(compact)
    if not parts or parts[0] != "curl":
        raise ValueError("curl command must start with curl")

    method = "GET"
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    body: Any = None
    url: str | None = None
    index = 1
    while index < len(parts):
        part = parts[index]
        if part in {"-X", "--request"}:
            index += 1
            method = parts[index].upper()
        elif part in {"-H", "--header"}:
            index += 1
            header_name, header_value = split_header(parts[index])
            headers[header_name] = header_value
        elif part in {"-d", "--data", "--data-raw", "--data-binary"}:
            index += 1
            method = "POST" if method == "GET" else method
            body = parse_body(parts[index])
        elif part in {"-b", "--cookie", "--cookie-jar"}:
            index += 1
            cookies.update(parse_cookie_header(parts[index]))
        elif part in {"-o", "--output", "--config", "-K"}:
            raise ValueError(f"Unsupported unsafe curl option: {part}")
        elif part.startswith("-"):
            raise ValueError(f"Unsupported curl option: {part}")
        else:
            url = part
        index += 1
    if not url:
        raise ValueError("curl command is missing URL")
    sanitized_headers, sanitized_cookies = sanitize_request_secrets(headers, cookies, "API")
    result: dict[str, Any] = {
        "method": method,
        "url": url,
        "execution_url": url,
        "headers": sanitized_headers,
        "execution_headers": headers,
        "cookies": sanitized_cookies,
        "execution_cookies": cookies,
    }
    if body is not None:
        result["body"] = body
    return result


def execute_curl_specs(specs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        import httpx
    except ModuleNotFoundError as exc:
        raise RuntimeError("httpx is required to execute curl commands. Install dependencies first.") from exc

    apis: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    for spec in specs:
        try:
            response = httpx.request(
                spec.get("method", "GET"),
                spec["execution_url"],
                headers=spec.get("execution_headers") or {},
                cookies=spec.get("execution_cookies") or {},
                json=spec.get("body"),
                timeout=float(spec.get("timeout_seconds", 10)),
            )
            response.raise_for_status()
            response_body = response.json()
            api = {key: value for key, value in spec.items() if not key.startswith("execution_")}
            api["response"] = response_body
            apis.append(api)
            statuses.append({"name": spec["name"], "status_code": response.status_code, "ok": True, "message": "OK"})
        except Exception as exc:
            statuses.append({"name": spec.get("name", "unknown"), "status_code": None, "ok": False, "message": str(exc)})
    if any(not status["ok"] for status in statuses):
        details = "; ".join(f"{status['name']}: {status['message']}" for status in statuses if not status["ok"])
        raise ValueError(f"One or more curl requests failed: {details}")
    return apis, statuses


def flatten_api_responses(apis: list[dict[str, Any]]) -> list[FieldCandidate]:
    fields: list[FieldCandidate] = []
    for api in apis:
        fields.extend(flatten_json(api["response"], api["name"]))
    return fields


def flatten_json(data: Any, api_name: str, prefix: str = "") -> list[FieldCandidate]:
    fields: list[FieldCandidate] = []
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            fields.extend(flatten_json(value, api_name, path))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            path = f"{prefix}[{index}]"
            fields.extend(flatten_json(value, api_name, path))
    else:
        tokens = tokenize_path(prefix)
        expanded = expand_tokens(tokens)
        fields.append(FieldCandidate(api_name=api_name, path=prefix, value=data, tokens=tokens, expanded_tokens=expanded, key="_".join(expanded)))
    return fields


def suggest_mappings(source_fields: list[FieldCandidate], target_fields: list[FieldCandidate]) -> tuple[list[MappingSuggestion], list[FieldCandidate], list[FieldCandidate]]:
    candidates: list[tuple[int, MappingSuggestion]] = []
    for source in source_fields:
        for target in target_fields:
            suggestion = score_pair(source, target)
            if suggestion.score >= 35:
                candidates.append((suggestion.score, suggestion))

    used_source: set[tuple[str, str]] = set()
    used_target: set[tuple[str, str]] = set()
    selected: list[MappingSuggestion] = []
    for _, suggestion in sorted(candidates, key=lambda item: item[0], reverse=True):
        source_key = (suggestion.source_api, suggestion.source_field)
        target_key = (suggestion.target_api, suggestion.target_field)
        if source_key in used_source or target_key in used_target:
            continue
        selected.append(suggestion)
        used_source.add(source_key)
        used_target.add(target_key)

    unmatched_source = [field for field in source_fields if (field.api_name, field.path) not in used_source]
    unmatched_target = [field for field in target_fields if (field.api_name, field.path) not in used_target]
    return selected, unmatched_source, unmatched_target


def score_pair(source: FieldCandidate, target: FieldCandidate) -> MappingSuggestion:
    reasons: list[str] = []
    name_score = name_similarity(source, target, reasons)
    value_score, compare_data = value_similarity(source.value, target.value, reasons)
    context_score = context_similarity(source, target, reasons)
    score = int(name_score * 0.45 + value_score * 0.35 + context_score * 0.20)
    if compare_data["compare_type"] != "exact" or value_score >= 40:
        score += 10
    score = max(0, min(score, 100))
    confidence = "HIGH" if score >= 80 else "MEDIUM" if score >= 55 else "LOW"
    return MappingSuggestion(
        source_api=source.api_name,
        source_field=source.path,
        source_value=source.value,
        target_api=target.api_name,
        target_field=target.path,
        target_value=target.value,
        compare_type=compare_data["compare_type"],
        confidence=confidence,
        score=score,
        reason="; ".join(reasons[:5]) or "weak name/value similarity",
        tolerance=compare_data.get("tolerance"),
        source_format=compare_data.get("source_format"),
        target_format=compare_data.get("target_format"),
        contains_direction=compare_data.get("contains_direction"),
    )


def name_similarity(source: FieldCandidate, target: FieldCandidate, reasons: list[str]) -> int:
    source_set = set(source.expanded_tokens)
    target_set = set(target.expanded_tokens)
    overlap = len(source_set & target_set)
    total = max(len(source_set | target_set), 1)
    score = int(60 * overlap / total)
    if source.tokens[-1:] == target.tokens[-1:]:
        score += 25
        reasons.append("same final field token")
    if are_synonyms(source.key, target.key):
        score += 35
        reasons.append("field names are synonyms")
    ratio = SequenceMatcher(None, source.key, target.key).ratio()
    if ratio > 0.65:
        score += int(20 * ratio)
        reasons.append("field names are textually similar")
    if overlap:
        reasons.append(f"shared name tokens: {', '.join(sorted(source_set & target_set))}")
    return min(score, 100)


def value_similarity(source: Any, target: Any, reasons: list[str]) -> tuple[int, dict[str, Any]]:
    if source == target:
        reasons.append("values are exactly equal")
        return 100, {"compare_type": "exact"}
    if isinstance(source, str) and isinstance(target, str) and source.lower() == target.lower():
        reasons.append("values match ignoring case")
        return 90, {"compare_type": "ignore_case"}
    source_number = parse_number(source)
    target_number = parse_number(target)
    if source_number is not None and target_number is not None:
        diff = abs(source_number - target_number)
        if diff <= 0.01:
            reasons.append("numeric values are equal within 0.01")
            return 85, {"compare_type": "number", "tolerance": 0.01}
        return 45, {"compare_type": "number", "tolerance": 0.01}
    source_bool = parse_bool(source)
    target_bool = parse_bool(target)
    if source_bool is not None and target_bool is not None:
        if source_bool == target_bool:
            reasons.append("boolean values normalize to the same value")
            return 85, {"compare_type": "boolean"}
        return 20, {"compare_type": "boolean"}
    date_data = detect_date_pair(source, target)
    if date_data:
        reasons.append("dates match with different formats")
        return 85, date_data
    if isinstance(source, str) and isinstance(target, str):
        if source and source in target:
            reasons.append("target contains source value")
            return 70, {"compare_type": "contains", "contains_direction": "target_contains_source"}
        if target and target in source:
            reasons.append("source contains target value")
            return 70, {"compare_type": "contains", "contains_direction": "source_contains_target"}
    if type(source) is type(target):
        return 25, {"compare_type": "exact"}
    return 0, {"compare_type": "exact"}


def context_similarity(source: FieldCandidate, target: FieldCandidate, reasons: list[str]) -> int:
    source_context = set(source.expanded_tokens[:-1])
    target_context = set(target.expanded_tokens[:-1])
    overlap = source_context & target_context
    score = min(70, len(overlap) * 20)
    if abs(len(source.tokens) - len(target.tokens)) <= 1:
        score += 15
    if overlap:
        reasons.append(f"shared path context: {', '.join(sorted(overlap))}")
    return min(score, 100)


def parse_test_cases(raw: str) -> list[dict[str, Any]]:
    raw = (raw or "").strip()
    if not raw:
        return [{"id": "case_001", "params": {"customerId": "C1001", "orderId": "O9001", "loyaltyId": "L1001"}}]
    if raw.startswith("[") or raw.startswith("-"):
        data = yaml.safe_load(raw)
        if isinstance(data, list):
            return data
    rows = list(csv.DictReader(io.StringIO(raw)))
    return [{"id": f"case_{index:03d}", "params": dict(row)} for index, row in enumerate(rows, start=1)]


def build_config(source_apis: list[dict[str, Any]], target_apis: list[dict[str, Any]], test_cases: list[dict[str, Any]], mappings: list[dict[str, Any]], migration_name: str = "mapped-api-migration") -> dict[str, Any]:
    return {
        "migration_name": migration_name,
        "source_apis": [api_config(api) for api in source_apis],
        "target_apis": [api_config(api) for api in target_apis],
        "test_cases": test_cases,
        "field_mapping": mappings,
        "output": {"directory": "./results", "sqlite_path": "./results/migration_check.db"},
    }


def api_config(api: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": api["name"],
        "method": api.get("method", "GET"),
        "url": api["url"],
        "timeout_seconds": int(api.get("timeout_seconds", 10)),
        "retry_count": int(api.get("retry_count", 1)),
    }
    headers = dict(api.get("headers") or {})
    auth_type = api.get("auth_type", "none")
    if auth_type == "bearer":
        headers["Authorization"] = f"Bearer ${{{api.get('auth_env', 'API_TOKEN')}}}"
    elif auth_type == "api_key":
        headers[api.get("api_key_header", "X-API-Key")] = f"${{{api.get('auth_env', 'API_KEY')}}}"
    elif auth_type == "basic":
        headers["Authorization"] = f"Basic ${{{api.get('auth_env', 'BASIC_AUTH')}}}"
    elif auth_type == "cookie_header":
        headers["Cookie"] = api.get("cookie_header", "sessionid=${SESSION_ID}")
    if headers:
        result["headers"] = headers
    if api.get("cookies"):
        result["cookies"] = api["cookies"]
    if api.get("query_params"):
        result["query_params"] = api["query_params"]
    if api.get("body"):
        result["body"] = api["body"]
    return result


def split_header(raw: str) -> tuple[str, str]:
    if ":" not in raw:
        raise ValueError(f"Invalid header, expected Name: value: {raw}")
    name, value = raw.split(":", 1)
    return name.strip(), value.strip()


def parse_body(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        parsed = yaml.safe_load(raw)
        return parsed if parsed is not None else raw


def parse_cookie_header(raw: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for item in raw.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        cookies[name.strip()] = value.strip()
    return cookies


def sanitize_request_secrets(headers: dict[str, str], cookies: dict[str, str], env_prefix: str) -> tuple[dict[str, str], dict[str, str]]:
    sanitized_headers: dict[str, str] = {}
    for name, value in headers.items():
        env_name = normalize_env_name(f"{env_prefix}_{name}")
        if name.lower() == "authorization" and value.lower().startswith("bearer "):
            sanitized_headers[name] = f"Bearer ${{{env_name}}}"
        elif name.lower() == "cookie":
            sanitized_headers[name] = value
        elif looks_secret_header(name):
            sanitized_headers[name] = f"${{{env_name}}}"
        else:
            sanitized_headers[name] = value
    sanitized_cookies = {name: f"${{{normalize_env_name(f'{env_prefix}_{name}')}}}" for name in cookies}
    return sanitized_headers, sanitized_cookies


def looks_secret_header(name: str) -> bool:
    lowered = name.lower()
    return lowered in {"x-api-key", "api-key", "authorization"} or "token" in lowered or "secret" in lowered or "key" in lowered


def normalize_env_name(value: str) -> str:
    normalized = "".join(char if char.isalnum() else "_" for char in value.upper())
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.strip("_")


def suggestion_to_mapping(suggestion: MappingSuggestion, name: str) -> dict[str, Any]:
    mapping: dict[str, Any] = {
        "name": name,
        "source_api": suggestion.source_api,
        "source_field": suggestion.source_field,
        "target_api": suggestion.target_api,
        "target_field": suggestion.target_field,
        "compare_type": suggestion.compare_type,
    }
    if suggestion.tolerance is not None:
        mapping["tolerance"] = suggestion.tolerance
    if suggestion.source_format:
        mapping["source_format"] = suggestion.source_format
    if suggestion.target_format:
        mapping["target_format"] = suggestion.target_format
    if suggestion.contains_direction:
        mapping["contains_direction"] = suggestion.contains_direction
    return mapping


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def from_json(value: str) -> Any:
    return json.loads(value)


def dataclass_dict(value: Any) -> dict[str, Any]:
    return asdict(value)


def tokenize_path(path: str) -> list[str]:
    chunks: list[str] = []
    for part in re.split(r"[.\[\]_-]+", path):
        if not part or part.isdigit():
            continue
        split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", part).lower().split()
        chunks.extend(split)
    return chunks or [path.lower()]


def expand_tokens(tokens: list[str]) -> list[str]:
    expanded: list[str] = []
    for token in tokens:
        expanded.extend(sorted(ABBREVIATIONS.get(token, {token})))
    return expanded


def are_synonyms(source_key: str, target_key: str) -> bool:
    if source_key == target_key:
        return True
    return target_key in SYNONYMS.get(source_key, set()) or source_key in SYNONYMS.get(target_key, set())


def parse_number(value: Any) -> float | None:
    try:
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    return None


def detect_date_pair(source: Any, target: Any) -> dict[str, Any] | None:
    source_text = str(source)
    target_text = str(target)
    for source_name, source_fmt in DATE_FORMATS:
        for target_name, target_fmt in DATE_FORMATS:
            try:
                source_date = datetime.strptime(source_text, source_fmt).date()
                target_date = datetime.strptime(target_text, target_fmt).date()
            except ValueError:
                continue
            if source_date == target_date:
                return {"compare_type": "date_format", "source_format": source_name, "target_format": target_name}
    return None
