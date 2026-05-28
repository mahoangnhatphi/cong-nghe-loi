from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


VALID_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
VALID_COMPARE_TYPES = {
    "exact",
    "ignore_case",
    "number",
    "boolean",
    "date_format",
    "contains",
    "regex",
    "custom_transform",
}


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    raw = config_path.read_text(encoding="utf-8")
    if config_path.suffix.lower() == ".json":
        data = json.loads(raw)
    else:
        data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("Config root must be an object")
    return data


def validate_config(config: dict[str, Any]) -> ValidationReport:
    errors: list[str] = []
    warnings: list[str] = []

    if not config.get("migration_name"):
        errors.append("migration_name is required")

    source_apis = config.get("source_apis") or []
    target_apis = config.get("target_apis") or []
    test_cases = config.get("test_cases") or []
    mappings = config.get("field_mapping") or []

    if not isinstance(source_apis, list) or not source_apis:
        errors.append("source_apis must be a non-empty list")
    if not isinstance(target_apis, list) or not target_apis:
        errors.append("target_apis must be a non-empty list")
    if not isinstance(test_cases, list) or not test_cases:
        errors.append("test_cases must be a non-empty list")
    if not isinstance(mappings, list) or not mappings:
        errors.append("field_mapping must be a non-empty list")

    api_names: set[str] = set()
    for group_name, apis in (("source_apis", source_apis), ("target_apis", target_apis)):
        if not isinstance(apis, list):
            continue
        for index, api in enumerate(apis):
            prefix = f"{group_name}[{index}]"
            if not isinstance(api, dict):
                errors.append(f"{prefix} must be an object")
                continue
            name = api.get("name")
            method = str(api.get("method", "")).upper()
            if not name:
                errors.append(f"{prefix}.name is required")
            elif name in api_names:
                errors.append(f"duplicate API name: {name}")
            else:
                api_names.add(name)
            if method not in VALID_METHODS:
                errors.append(f"{prefix}.method must be one of {sorted(VALID_METHODS)}")
            if not api.get("url"):
                errors.append(f"{prefix}.url is required")

    test_case_ids: set[str] = set()
    if isinstance(test_cases, list):
        for index, test_case in enumerate(test_cases):
            if not isinstance(test_case, dict):
                errors.append(f"test_cases[{index}] must be an object")
                continue
            case_id = test_case.get("id")
            if not case_id:
                errors.append(f"test_cases[{index}].id is required")
            elif case_id in test_case_ids:
                errors.append(f"duplicate test case id: {case_id}")
            else:
                test_case_ids.add(case_id)
            if not isinstance(test_case.get("params", {}), dict):
                errors.append(f"test_cases[{index}].params must be an object")

    ignored_count = 0
    if isinstance(mappings, list):
        for index, mapping in enumerate(mappings):
            prefix = f"field_mapping[{index}]"
            if not isinstance(mapping, dict):
                errors.append(f"{prefix} must be an object")
                continue
            if not mapping.get("name"):
                errors.append(f"{prefix}.name is required")
            if mapping.get("source_api") not in api_names:
                errors.append(f"{prefix}.source_api references unknown API: {mapping.get('source_api')}")
            if mapping.get("target_api") not in api_names:
                errors.append(f"{prefix}.target_api references unknown API: {mapping.get('target_api')}")
            if not mapping.get("source_field"):
                errors.append(f"{prefix}.source_field is required")
            if not mapping.get("target_field"):
                errors.append(f"{prefix}.target_field is required")
            compare_type = mapping.get("compare_type", "exact")
            if compare_type not in VALID_COMPARE_TYPES:
                errors.append(f"{prefix}.compare_type is invalid: {compare_type}")
            if compare_type == "date_format" and (not mapping.get("source_format") or not mapping.get("target_format")):
                errors.append(f"{prefix} date_format requires source_format and target_format")
            if mapping.get("ignore"):
                ignored_count += 1

    if mappings and ignored_count / max(len(mappings), 1) > 0.5:
        warnings.append("More than 50% of field mappings are ignored")

    return ValidationReport(valid=not errors, errors=errors, warnings=warnings)
