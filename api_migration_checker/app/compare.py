from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any


TRANSFORMS = {
    "strip": lambda value: str(value).strip() if value is not None else value,
    "lower": lambda value: str(value).lower() if value is not None else value,
    "upper": lambda value: str(value).upper() if value is not None else value,
}


def compare_values(source: Any, target: Any, mapping: dict[str, Any]) -> tuple[str, str | None]:
    if mapping.get("ignore"):
        return "SKIPPED", "Ignored by mapping"
    if source is None or target is None:
        if mapping.get("nullable") and source is None and target is None:
            return "PASS", None
        return "FAIL", "Source or target value is null"

    compare_type = mapping.get("compare_type", "exact")
    try:
        if compare_type == "exact":
            passed = source == target
        elif compare_type == "ignore_case":
            passed = str(source).lower() == str(target).lower()
        elif compare_type == "number":
            tolerance = float(mapping.get("tolerance", 0))
            passed = abs(float(source) - float(target)) <= tolerance
        elif compare_type == "boolean":
            passed = _to_bool(source) == _to_bool(target)
        elif compare_type == "date_format":
            source_date = datetime.strptime(str(source), _python_date_format(mapping["source_format"]))
            target_date = datetime.strptime(str(target), _python_date_format(mapping["target_format"]))
            passed = source_date.date() == target_date.date()
        elif compare_type == "contains":
            direction = mapping.get("contains_direction", "target_contains_source")
            passed = str(source) in str(target) if direction == "target_contains_source" else str(target) in str(source)
        elif compare_type == "regex":
            pattern = mapping.get("pattern") or str(source)
            passed = re.search(pattern, str(target)) is not None
        elif compare_type == "custom_transform":
            transform_name = mapping.get("transform")
            if transform_name not in TRANSFORMS:
                return "ERROR", f"Unknown safe transform: {transform_name}"
            transform = TRANSFORMS[transform_name]
            passed = transform(source) == transform(target)
        else:
            return "ERROR", f"Unsupported compare_type: {compare_type}"
    except Exception as exc:
        return "ERROR", str(exc)
    return ("PASS", None) if passed else ("FAIL", f"Values differ: {json.dumps(source, ensure_ascii=False)} != {json.dumps(target, ensure_ascii=False)}")


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    raise ValueError(f"Cannot normalize boolean: {value}")


def _python_date_format(fmt: str) -> str:
    return (
        fmt.replace("yyyy", "%Y")
        .replace("MM", "%m")
        .replace("dd", "%d")
        .replace("HH", "%H")
        .replace("mm", "%M")
        .replace("ss", "%S")
    )
