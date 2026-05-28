from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from api_migration_checker.app.mapper import FieldCandidate, MappingSuggestion, score_pair


@dataclass(frozen=True)
class AIResult:
    available_providers: list[str]
    selected_provider: str | None
    prompt: str
    raw_response: str | None
    error: str | None
    suggestions: list[MappingSuggestion]


def available_providers() -> list[str]:
    providers: list[str] = []
    if shutil.which("claude"):
        providers.append("claude")
    if shutil.which("opencode"):
        providers.append("opencode")
    return providers


def run_ai_mapping(
    source_fields: list[FieldCandidate],
    target_fields: list[FieldCandidate],
    provider: str = "auto",
    include_values: bool = True,
    manual_response: str | None = None,
) -> AIResult:
    providers = available_providers()
    selected = choose_provider(provider, providers)
    prompt = build_mapping_prompt(source_fields, target_fields, include_values=include_values)
    raw_response = manual_response.strip() if manual_response and manual_response.strip() else None
    error: str | None = None

    if raw_response is None and selected:
        raw_response, error = execute_provider(selected, prompt)

    suggestions: list[MappingSuggestion] = []
    if raw_response:
        try:
            suggestions = parse_ai_response(raw_response, source_fields, target_fields)
        except Exception as exc:
            error = f"AI response parse failed: {exc}"

    return AIResult(
        available_providers=providers,
        selected_provider=selected,
        prompt=prompt,
        raw_response=raw_response,
        error=error,
        suggestions=suggestions,
    )


def choose_provider(requested: str, providers: list[str]) -> str | None:
    if requested != "auto":
        return requested if requested in providers else None
    for name in ("claude", "opencode"):
        if name in providers:
            return name
    return None


def execute_provider(provider: str, prompt: str) -> tuple[str | None, str | None]:
    commands = {
        "claude": [["claude", "--print"]],
        # OpenCode CLI flags can vary by install; try common non-interactive forms.
        "opencode": [["opencode", "run", "--print"], ["opencode", "run"]],
    }.get(provider, [])
    last_error = "No command configured"
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=90,
                shell=False,
                check=False,
            )
        except Exception as exc:
            last_error = str(exc)
            continue
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.strip(), None
        last_error = completed.stderr.strip() or f"{provider} exited with code {completed.returncode}"
    return None, last_error


def build_mapping_prompt(source_fields: list[FieldCandidate], target_fields: list[FieldCandidate], include_values: bool = True) -> str:
    old_fields = [field_payload(field, include_values) for field in source_fields]
    new_fields = [field_payload(field, include_values) for field in target_fields]
    return f"""You are helping map old API response fields to new API response fields for API migration verification.

Return ONLY valid JSON. Do not include markdown, comments, or explanations outside JSON.

Task:
Suggest field mappings between old API fields and new API fields.

Rules:
- Match fields with the same business meaning, even if names differ.
- Understand abbreviations such as cust=customer, nm=name, dob=date of birth, amt=amount, qty=quantity, addr=address, lvl=level.
- Use sample values to improve matching when values are provided.
- Detect compare_type: exact, ignore_case, number, boolean, date_format, contains.
- For date_format, include source_format and target_format when known.
- For number, include tolerance if needed.
- Do not invent fields. Only use paths from the old_fields and new_fields arrays.
- Prefer one best target field for each source field.
- Confidence must be HIGH, MEDIUM, or LOW.

old_fields:
{json.dumps(old_fields, ensure_ascii=False, indent=2)}

new_fields:
{json.dumps(new_fields, ensure_ascii=False, indent=2)}

Return JSON in this exact shape:
{{
  "mappings": [
    {{
      "source_api": "old_customer_api",
      "source_field": "data.customer_name",
      "target_api": "new_profile_api",
      "target_field": "profile.identity.fullName",
      "compare_type": "exact",
      "confidence": "HIGH",
      "reason": "same business meaning and values match"
    }}
  ]
}}
"""


def field_payload(field: FieldCandidate, include_values: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "api": field.api_name,
        "path": field.path,
        "type": type(field.value).__name__,
    }
    if include_values:
        payload["value"] = field.value
    return payload


def parse_ai_response(raw_response: str, source_fields: list[FieldCandidate], target_fields: list[FieldCandidate]) -> list[MappingSuggestion]:
    data = json.loads(extract_json_object(raw_response))
    mappings = data.get("mappings")
    if not isinstance(mappings, list):
        raise ValueError("AI JSON must contain mappings list")

    source_lookup = {(field.api_name, field.path): field for field in source_fields}
    target_lookup = {(field.api_name, field.path): field for field in target_fields}
    suggestions: list[MappingSuggestion] = []
    for item in mappings:
        if not isinstance(item, dict):
            continue
        source = source_lookup.get((item.get("source_api"), item.get("source_field")))
        target = target_lookup.get((item.get("target_api"), item.get("target_field")))
        if source is None or target is None:
            continue
        baseline = score_pair(source, target)
        suggestions.append(
            MappingSuggestion(
                source_api=source.api_name,
                source_field=source.path,
                source_value=source.value,
                target_api=target.api_name,
                target_field=target.path,
                target_value=target.value,
                compare_type=item.get("compare_type") or baseline.compare_type,
                confidence=item.get("confidence") or baseline.confidence,
                score=max(baseline.score, confidence_score(item.get("confidence"))),
                reason=f"AI: {item.get('reason') or 'AI suggested mapping'}",
                tolerance=item.get("tolerance", baseline.tolerance),
                source_format=item.get("source_format", baseline.source_format),
                target_format=item.get("target_format", baseline.target_format),
                contains_direction=item.get("contains_direction", baseline.contains_direction),
                origin="ai",
            )
        )
    return suggestions


def merge_suggestions(local: list[MappingSuggestion], ai: list[MappingSuggestion]) -> list[MappingSuggestion]:
    merged: dict[tuple[str, str, str, str], MappingSuggestion] = {}
    for suggestion in local:
        key = (suggestion.source_api, suggestion.source_field, suggestion.target_api, suggestion.target_field)
        merged[key] = suggestion
    for suggestion in ai:
        key = (suggestion.source_api, suggestion.source_field, suggestion.target_api, suggestion.target_field)
        existing = merged.get(key)
        if existing:
            merged[key] = MappingSuggestion(
                source_api=existing.source_api,
                source_field=existing.source_field,
                source_value=existing.source_value,
                target_api=existing.target_api,
                target_field=existing.target_field,
                target_value=existing.target_value,
                compare_type=existing.compare_type,
                confidence="HIGH" if existing.confidence == "HIGH" or suggestion.confidence == "HIGH" else existing.confidence,
                score=min(100, max(existing.score, suggestion.score) + 8),
                reason=f"{existing.reason}; confirmed by AI: {suggestion.reason}",
                tolerance=existing.tolerance or suggestion.tolerance,
                source_format=existing.source_format or suggestion.source_format,
                target_format=existing.target_format or suggestion.target_format,
                contains_direction=existing.contains_direction or suggestion.contains_direction,
                origin="local + ai",
            )
        else:
            merged[key] = suggestion
    return sorted(merged.values(), key=lambda item: item.score, reverse=True)


def extract_json_object(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("{"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in AI response")
    return stripped[start : end + 1]


def confidence_score(confidence: str | None) -> int:
    return {"HIGH": 85, "MEDIUM": 65, "LOW": 45}.get(str(confidence or "").upper(), 45)
