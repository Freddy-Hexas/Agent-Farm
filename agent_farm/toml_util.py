from __future__ import annotations

import json
import re
from typing import Any

BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def toml_key_segment(value: str) -> str:
    if BARE_KEY_RE.match(value):
        return value
    return json.dumps(value)


def toml_dotted_key(parts: list[str]) -> str:
    return ".".join(toml_key_segment(part) for part in parts)


def toml_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        items = []
        for key, item in value.items():
            items.append(f"{toml_key_segment(str(key))} = {toml_literal(item)}")
        return "{ " + ", ".join(items) + " }"
    if value is None:
        raise ValueError("None is not a valid TOML override value")
    raise TypeError(f"Unsupported TOML override type: {type(value).__name__}")
