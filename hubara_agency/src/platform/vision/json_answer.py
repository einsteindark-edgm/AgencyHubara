"""El objeto JSON de la respuesta de un modelo multimodal."""
from __future__ import annotations

import json


def json_object(raw: str | None) -> dict | None:
    """El objeto JSON de la respuesta: tolera un bloque ```json y texto
    alrededor (del primer `{` al último `}`); None si no hay objeto."""
    if not raw:
        return None
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
