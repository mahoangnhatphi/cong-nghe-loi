from __future__ import annotations

import os
import re
from typing import Any


TOKEN_RE = re.compile(r"\$\{([^}]+)}")


def resolve_templates(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: resolve_templates(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_templates(item, variables) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in variables:
            return str(variables[name])
        return os.getenv(name, match.group(0))

    return TOKEN_RE.sub(replace, value)
