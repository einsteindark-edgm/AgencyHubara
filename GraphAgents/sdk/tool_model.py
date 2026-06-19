"""El contrato de una **tool agnóstica** — la unidad de catálogo de primera clase.

Una tool es un nodo reusable: tiene contrato propio (in/out, side-effect,
idempotencia, approval, credentials, version, tags) y una `impl` PURA que NO
importa ningún runtime (G-AGNOSTIC). Los runtimes (LangGraph/AgentSpan/MCP/HTTP)
la consumen vía adapters. Así la misma tool se enchufa a cualquier agente.

`tools/<id>/tool.yaml` se valida contra este modelo.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

_SLUG = re.compile(r"^[a-z][a-z0-9-]*$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

SideEffect = Literal["pure", "read", "outward"]
FieldType = Literal["string", "number", "integer", "boolean", "object", "array"]


class FieldSpec(BaseModel):
    type: FieldType
    required: bool = True
    description: str = ""


class ToolContract(BaseModel):
    id: str
    version: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    # pure = sin IO · read = lee externo · outward = MUTA externo (gasto/cambios).
    side_effect: SideEffect = "pure"
    idempotent: bool = True
    approval_required: bool = False  # debe ser True si side_effect == outward (T-DUR)
    credentials: list[str] = Field(default_factory=list)  # connections requeridas
    inputs: dict[str, FieldSpec] = Field(default_factory=dict)
    outputs: dict[str, FieldSpec] = Field(default_factory=dict)
    impl: str  # "modulo:funcion" — el callable PURO (G-AGNOSTIC)

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _SLUG.match(v):
            raise ValueError(f"id '{v}' debe ser kebab-case [a-z][a-z0-9-]*")
        return v

    @field_validator("version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not _SEMVER.match(v):
            raise ValueError(f"version '{v}' debe ser semver X.Y.Z")
        return v

    @property
    def major(self) -> str:
        return self.version.split(".", 1)[0]


def load_tool(path: str | Path) -> ToolContract:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: tool.yaml debe ser un mapping YAML")
    return ToolContract.model_validate(data)
