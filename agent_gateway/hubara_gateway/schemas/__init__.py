"""`outputSchema` por tool, generado desde los contratos Zod (scripts/gen_output_schemas.mts)."""

import json
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

_DIR = Path(__file__).parent


@cache
def output_schema(tool: str) -> dict[str, Any]:
    return json.loads((_DIR / f"{tool}.json").read_text(encoding="utf-8"))


@cache
def output_validator(tool: str) -> Draft7Validator:
    return Draft7Validator(output_schema(tool))
