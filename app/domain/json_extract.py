from __future__ import annotations

import json
import re
from typing import Any


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract a JSON object from raw text or a markdown code fence."""
    raw = text.strip()
    if not raw:
        raise ValueError("empty message")

    fence = _FENCE_RE.search(raw)
    if fence:
        raw = fence.group(1).strip()

    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("JSON object not found")
        raw = raw[start : end + 1]

    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")
    return data
