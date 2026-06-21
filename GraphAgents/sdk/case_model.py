"""El modelo de un **CASO** — el artefacto que fija el TRIPLE input de un nodo (seed +
port-fixtures + llm-reply) y su golden, para replayearlo determinista desde UNA sola fuente.
Es el `tool.yaml`/manifest de este mundo: data, no código. Lo lista el viewer en un select y
lo verifica el TestKit (`run_case_checks`). El replay vive en `sdk.replay` (importa el loader).

Un `.case.yaml`:

    id: diagnose-scale
    target: tool:diagnose            # tool:<id> | agent:<id> | flow:<id>
    seed: { payload: {...} }         # el arranque (inline o {$ref: fixtures/x.json})
    ports: { meta_marketing_api: {$ref: ...}, llm: "la respuesta fija" }   # opcional
    golden: {...}                    # el output esperado (inline o {$ref: ...})

`$ref` se resuelve relativo a `ga_root` (la raíz de GraphAgents) — así un payload grande (el
JSON de Meta) vive en `fixtures/` y el caso lo referencia, no lo duplica.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, field_validator

_TARGET = re.compile(r"^(tool|agent|flow):[a-z][a-z0-9-]*$")


class Case(BaseModel):
    id: str
    target: str  # "tool:<id>" | "agent:<id>" | "flow:<id>"
    title: Optional[str] = None
    seed: dict = {}
    ports: dict = {}
    # `golden` tipado `dict` porque los entrypoints de HOY (los 9 tools + las capabilities)
    # devuelven dict. Si aparece un tool con output `list`/escalar, ampliar a
    # `dict | list | str | int | float | bool` (y ahí también el retorno de `replay_case`).
    golden: dict = {}

    @field_validator("target")
    @classmethod
    def _target_shape(cls, v: str) -> str:
        if not _TARGET.match(v):
            raise ValueError(f"target '{v}' debe ser 'tool:<id>' | 'agent:<id>' | 'flow:<id>' (kebab-case)")
        return v

    @property
    def kind(self) -> str:
        return self.target.split(":", 1)[0]

    @property
    def target_id(self) -> str:
        return self.target.split(":", 1)[1]


def load_case(path: str | Path) -> Case:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: el caso debe ser un mapping YAML")
    return Case.model_validate(data)


def discover_cases(ga_root: Path) -> list[Case]:
    """El catálogo de casos = `fixtures/cases/*.case.yaml` (lo que el viewer lista)."""
    return [load_case(p) for p in sorted((ga_root / "fixtures" / "cases").glob("*.case.yaml"))]


def _load_ref(ga_root: Path, rel: str):
    p = ga_root / rel
    text = p.read_text(encoding="utf-8")  # raise si no existe → lo captura el check (CASE-REF)
    return json.loads(text) if p.suffix == ".json" else yaml.safe_load(text)


def resolve(value, ga_root: Path):
    """Resuelve `{$ref: <path>}` (relativo a ga_root) en profundidad. Un dict que SOLO tiene
    `$ref` se reemplaza por el contenido del archivo; el resto se recorre recursivamente."""
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            return _load_ref(ga_root, value["$ref"])
        return {k: resolve(v, ga_root) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, ga_root) for v in value]
    return value


def iter_refs(value) -> list[str]:
    """Todos los paths `$ref` que aparecen en un valor (para chequear que resuelven)."""
    out: list[str] = []
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            return [value["$ref"]]
        for v in value.values():
            out += iter_refs(v)
    elif isinstance(value, list):
        for v in value:
            out += iter_refs(v)
    return out
