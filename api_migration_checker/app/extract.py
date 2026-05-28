from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


PART_RE = re.compile(r"([^.[\]]+)|\[(\d+)]")


@dataclass(frozen=True)
class ExtractedValue:
    path: str
    found: bool
    value: Any = None
    error: str | None = None


def extract_value(data: Any, path: str) -> ExtractedValue:
    current = data
    try:
        for name, index in PART_RE.findall(path):
            if name:
                if not isinstance(current, dict) or name not in current:
                    return ExtractedValue(path=path, found=False, error=f"Missing object key: {name}")
                current = current[name]
            else:
                list_index = int(index)
                if not isinstance(current, list) or list_index >= len(current):
                    return ExtractedValue(path=path, found=False, error=f"Missing list index: {list_index}")
                current = current[list_index]
        return ExtractedValue(path=path, found=True, value=current)
    except Exception as exc:  # defensive: invalid user-defined paths should not crash a run
        return ExtractedValue(path=path, found=False, error=str(exc))
